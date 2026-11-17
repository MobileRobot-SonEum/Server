"""도구 호출(tools)을 지원하지 않거나 형식이 깨지는 모델을 위한 JSON 출력 프롬프트 방식.

모델에게 도구 목록을 텍스트로 알려 주고, 아래 둘 중 하나의 JSON 객체만 출력하게 한다.
  {"tool_calls": [{"name": "...", "arguments": {...}}]}
  {"final": "최종 답변"}
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from .types import Message, ToolCall, ToolFormatError

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def strip_think(text: str | None) -> str:
    if not text:
        return ""
    text = _THINK_RE.sub("", text)
    # 닫히지 않은 <think> 이후는 버린다
    idx = text.lower().find("<think>")
    if idx != -1:
        text = text[:idx]
    return text.strip()


def build_json_instruction(tools: list[dict]) -> str:
    lines = []
    for t in tools:
        fn = t["function"]
        lines.append(
            f"- {fn['name']}: {fn.get('description', '')}\n"
            f"  arguments 스키마: {json.dumps(fn.get('parameters', {}), ensure_ascii=False)}"
        )
    tool_text = "\n".join(lines)
    return (
        "\n\n[도구 사용 형식]\n"
        "쓸 수 있는 도구:\n"
        f"{tool_text}\n\n"
        "반드시 JSON 객체 하나만 출력하라. 설명 문장이나 코드 블록 표시는 붙이지 마라.\n"
        '도구를 쓸 때: {"tool_calls": [{"name": "도구이름", "arguments": {...}}]}\n'
        '일을 마쳤을 때: {"final": "최종 답변"}'
    )


def to_text_messages(messages: list[Message], tools: list[dict]) -> list[Message]:
    """tool 역할과 tool_calls를 지원하지 않는 모델용으로 대화를 텍스트 메시지로 바꾼다."""
    out: list[Message] = []
    instruction = build_json_instruction(tools)
    system_done = False
    for m in messages:
        role = m.get("role")
        if role == "system" and not system_done:
            out.append({"role": "system", "content": (m.get("content") or "") + instruction})
            system_done = True
        elif role == "assistant" and m.get("tool_calls"):
            calls = [
                {"name": tc["function"]["name"], "arguments": _loads_or_raw(tc["function"]["arguments"])}
                for tc in m["tool_calls"]
            ]
            out.append({"role": "assistant", "content": json.dumps({"tool_calls": calls}, ensure_ascii=False)})
        elif role == "tool":
            out.append(
                {"role": "user", "content": f"[도구 결과: {m.get('name', '')}] {m.get('content', '')}"}
            )
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    if not system_done:
        out.insert(0, {"role": "system", "content": instruction.strip()})
    return out


def _loads_or_raw(s: Any) -> Any:
    if isinstance(s, str):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return s
    return s


def _extract_json_object(text: str) -> Any:
    text = strip_think(text)
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 앞뒤에 설명이 붙은 경우: 첫 번째 균형 잡힌 {...}를 찾는다
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise ToolFormatError("JSON 객체를 찾지 못했습니다", raw=text[:500])


def parse_json_action(text: str | None) -> tuple[str | None, list[ToolCall]]:
    """JSON 출력에서 (최종 답변, 도구 호출 목록)을 꺼낸다. 형식이 틀리면 ToolFormatError."""
    if not text or not text.strip():
        raise ToolFormatError("빈 응답", raw=text)
    # JSON을 전혀 쓰지 않은 그냥 문장은 최종 답변으로 받는다.
    # (도구 미지원 모델이 아이 모드처럼 대화만 할 때 흔함. 깨진 JSON은 여전히 형식 오류)
    plain = strip_think(text)
    fenced = _FENCE_RE.search(plain)
    if "{" not in plain and not fenced and plain:
        return plain, []
    obj = _extract_json_object(text)
    if not isinstance(obj, dict):
        raise ToolFormatError("JSON 최상위가 객체가 아닙니다", raw=text[:500])

    if "tool_calls" in obj:
        raw_calls = obj["tool_calls"]
        if isinstance(raw_calls, dict):
            raw_calls = [raw_calls]
        if not isinstance(raw_calls, list) or not raw_calls:
            raise ToolFormatError("tool_calls가 비었거나 목록이 아닙니다", raw=text[:500])
        return None, [_to_tool_call(c, text) for c in raw_calls]
    # 일부 모델은 {"name":..., "arguments":...} 하나만 낸다
    if "name" in obj and "arguments" in obj:
        return None, [_to_tool_call(obj, text)]
    if "final" in obj:
        final = obj["final"]
        if not isinstance(final, str):
            final = json.dumps(final, ensure_ascii=False)
        return final, []
    raise ToolFormatError("'tool_calls'나 'final' 키가 없습니다", raw=text[:500])


def _to_tool_call(c: Any, raw: str) -> ToolCall:
    if not isinstance(c, dict) or not isinstance(c.get("name"), str):
        raise ToolFormatError("도구 호출에 name이 없습니다", raw=raw[:500])
    args = c.get("arguments", {})
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError as e:
            raise ToolFormatError(f"arguments가 올바른 JSON이 아닙니다: {e}", raw=raw[:500]) from e
    if not isinstance(args, dict):
        raise ToolFormatError("arguments가 객체가 아닙니다", raw=raw[:500])
    return ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", name=c["name"], arguments=args)
