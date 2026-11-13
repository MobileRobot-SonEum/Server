"""OpenAI 호환 Chat Completions 클라이언트.

base_url, api_key, model만 바꿔서 Ollama(로컬·EC2), Gemini, Groq를 모두 쓴다.
- 도구 호출(tools)을 쓰고, 모델이 지원하지 않거나 형식이 깨지면 JSON 출력 프롬프트로 대체한다(tool_mode=auto).
- 타임아웃, 재시도 1회(타임아웃·연결 오류·429·5xx), 실패하면 LLMError를 던진다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import httpx

from app.config import LLMSettings

from .json_fallback import parse_json_action, strip_think, to_text_messages
from .types import LLMError, LLMResponse, Message, ToolCall, ToolFormatError

log = logging.getLogger(__name__)

_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class OpenAICompatClient:
    def __init__(
        self,
        cfg: LLMSettings,
        http: httpx.AsyncClient | None = None,
        retry_backoff_s: float = 1.0,
    ):
        self.cfg = cfg
        self.model = cfg.model
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(cfg.timeout_s, connect=10.0))
        self._owns_http = http is None
        self._retry_backoff_s = retry_backoff_s
        # auto 모드에서 native 도구 호출이 안 된다고 확인되면 이후에는 바로 JSON 방식을 쓴다
        self._native_tools_unsupported = False
        # thinking 설정을 거부하는 모델이면 이후 reasoning_effort를 보내지 않는다
        self._reasoning_unsupported = False

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # ------------------------------------------------------------------ public
    async def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse:
        if not tools:
            return await self._chat_plain(messages)

        mode = self.cfg.tool_mode
        if mode == "json" or (mode == "auto" and self._native_tools_unsupported):
            return await self._chat_json(messages, tools)

        try:
            return await self._chat_native(messages, tools)
        except LLMError as e:
            if mode != "auto":
                raise
            if e.kind == "http" and e.status == 400 and "tool" in e.message.lower():
                log.warning("모델 %s가 tools를 지원하지 않아 JSON 방식으로 바꿉니다", self.model)
                self._native_tools_unsupported = True
                return await self._chat_json(messages, tools)
            if isinstance(e, ToolFormatError):
                log.warning("모델 %s의 도구 호출 형식이 깨져 JSON 방식으로 다시 요청합니다", self.model)
                return await self._chat_json(messages, tools)
            raise

    async def ping(self) -> dict[str, Any]:
        """GET {base_url}/models 로 연결만 확인한다."""
        url = self.cfg.base_url.rstrip("/") + "/models"
        try:
            r = await self._http.get(url, headers=self._headers(), timeout=5.0)
            return {"ok": r.status_code < 400, "status": r.status_code}
        except httpx.HTTPError as e:
            return {"ok": False, "error": type(e).__name__}

    # ----------------------------------------------------------------- modes
    async def _chat_plain(self, messages: list[Message]) -> LLMResponse:
        t0 = time.perf_counter()
        data = await self._post(self._payload(messages))
        msg = self._first_message(data)
        return LLMResponse(
            content=strip_think(msg.get("content")),
            model=data.get("model", self.model),
            latency_s=time.perf_counter() - t0,
        )

    async def _chat_native(self, messages: list[Message], tools: list[dict]) -> LLMResponse:
        t0 = time.perf_counter()
        payload = self._payload(messages)
        payload["tools"] = tools
        data = await self._post(payload)
        msg = self._first_message(data)
        content = strip_think(msg.get("content"))
        raw_calls = msg.get("tool_calls") or []

        calls: list[ToolCall] = []
        echoed_calls: list[dict[str, Any]] = []
        for rc in raw_calls:
            fn = rc.get("function") or {}
            name = fn.get("name")
            args = fn.get("arguments", {})
            if not name:
                raise ToolFormatError("도구 호출에 이름이 없습니다", raw=json.dumps(rc, ensure_ascii=False)[:500])
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError as e:
                    raise ToolFormatError(f"도구 인자가 올바른 JSON이 아닙니다: {e}", raw=args[:500]) from e
            if not isinstance(args, dict):
                raise ToolFormatError("도구 인자가 객체가 아닙니다", raw=str(args)[:500])
            call_id = rc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
            calls.append(ToolCall(id=call_id, name=name, arguments=args))
            echoed = dict(rc)  # 알 수 없는 필드도 그대로 돌려보낸다
            echoed["id"] = call_id
            echoed["type"] = rc.get("type", "function")
            echoed["function"] = {**fn, "arguments": json.dumps(args, ensure_ascii=False)}
            echoed_calls.append(echoed)

        # 도구 호출을 본문 JSON으로 써 버리는 모델 대응
        if not calls and content.lstrip().startswith(("{", "```")):
            try:
                final, calls = parse_json_action(content)
                content = final or ""
                echoed_calls = [_call_to_openai(c) for c in calls]
            except ToolFormatError:
                pass

        assistant: Message = {"role": "assistant"}
        if content:
            assistant["content"] = content
        if echoed_calls:
            assistant["tool_calls"] = echoed_calls
        return LLMResponse(
            content=content or None,
            tool_calls=calls,
            model=data.get("model", self.model),
            latency_s=time.perf_counter() - t0,
            assistant_message=assistant,
        )

    async def _chat_json(self, messages: list[Message], tools: list[dict]) -> LLMResponse:
        t0 = time.perf_counter()
        payload = self._payload(to_text_messages(messages, tools))
        data = await self._post(payload)
        msg = self._first_message(data)
        final, calls = parse_json_action(msg.get("content"))  # 형식이 틀리면 ToolFormatError
        assistant: Message = {"role": "assistant"}
        if final:
            assistant["content"] = final
        if calls:
            assistant["tool_calls"] = [_call_to_openai(c) for c in calls]
        return LLMResponse(
            content=final,
            tool_calls=calls,
            model=data.get("model", self.model),
            used_json_fallback=True,
            latency_s=time.perf_counter() - t0,
            assistant_message=assistant,
        )

    # --------------------------------------------------------------- helpers
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.cfg.api_key.get_secret_value()}"}

    def _payload(self, messages: list[Message]) -> dict[str, Any]:
        # Groq는 messages[].name을 받지 않으므로(400) 보내기 전에 뺀다
        clean = [{k: v for k, v in m.items() if k != "name"} for m in messages]
        p: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": clean,
            "temperature": self.cfg.temperature,
            "stream": False,
        }
        if self.cfg.max_tokens:
            p["max_tokens"] = self.cfg.max_tokens
        if self.cfg.reasoning_effort and not self._reasoning_unsupported:
            p["reasoning_effort"] = self.cfg.reasoning_effort
        return p

    @property
    def reasoning_dropped(self) -> bool:
        return self._reasoning_unsupported

    @staticmethod
    def _first_message(data: dict[str, Any]) -> dict[str, Any]:
        try:
            return data["choices"][0]["message"] or {}
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError("bad_response", f"응답에 choices[0].message가 없습니다: {e}",
                           raw=json.dumps(data, ensure_ascii=False)[:500]) from e

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        last_err: LLMError | None = None
        for attempt in range(2):  # 최초 1회 + 재시도 1회
            if attempt:
                await asyncio.sleep(self._retry_backoff_s)
                log.info("LLM 요청 재시도 (%s): %s", self.model, last_err.message if last_err else "")
            try:
                r = await self._http.post(url, json=payload, headers=self._headers())
            except httpx.TimeoutException:
                last_err = LLMError("timeout", f"LLM 응답 시간 초과 ({self.cfg.timeout_s}s, 모델 {self.model})")
                continue
            except httpx.HTTPError as e:
                last_err = LLMError("connection", f"LLM 서버 연결 실패 ({self.cfg.host}): {type(e).__name__}")
                continue

            if r.status_code >= 400:
                err = LLMError("http", f"LLM HTTP {r.status_code}: {_error_text(r)}", status=r.status_code)
                # thinking을 지원하지 않는 모델(예: exaone3.5)이 reasoning_effort를 거부하면 빼고 한 번 더
                if (r.status_code == 400 and "reasoning_effort" in payload and not self._reasoning_unsupported
                        and any(w in err.message.lower() for w in ("think", "reasoning"))):
                    log.warning("모델 %s가 reasoning_effort를 지원하지 않아 빼고 다시 요청합니다", self.model)
                    self._reasoning_unsupported = True
                    payload = {k: v for k, v in payload.items() if k != "reasoning_effort"}
                    r = await self._http.post(url, json=payload, headers=self._headers())
                    if r.status_code < 400:
                        try:
                            return r.json()
                        except ValueError as e:
                            raise LLMError("bad_response", "LLM 응답이 JSON이 아닙니다", raw=r.text[:500]) from e
                    err = LLMError("http", f"LLM HTTP {r.status_code}: {_error_text(r)}", status=r.status_code)
                if r.status_code in _RETRYABLE_STATUS:
                    last_err = err
                    continue
                raise err
            try:
                return r.json()
            except ValueError as e:
                raise LLMError("bad_response", "LLM 응답이 JSON이 아닙니다", raw=r.text[:500]) from e

        assert last_err is not None
        raise last_err


def _call_to_openai(c: ToolCall) -> dict[str, Any]:
    return {
        "id": c.id,
        "type": "function",
        "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)},
    }


def _error_text(r: httpx.Response) -> str:
    try:
        body = r.json()
        if isinstance(body, list) and body:
            body = body[0]
        err = body.get("error", body) if isinstance(body, dict) else body
        if isinstance(err, dict):
            return str(err.get("message", err))[:300]
        return str(err)[:300]
    except ValueError:
        return r.text[:300]
