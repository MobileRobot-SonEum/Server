"""에이전트 루프: 지시 → (계획) → 도구 호출 → 결과 확인 → 다음 단계.

멈추는 경우
- 모델이 도구 없이 답하면: completed
- 모델이 ask_teacher를 부르거나, 형식 오류·도구 실패가 반복되거나, 최대 단계에 닿으면: needs_teacher (교사에게 질문)
- LLM 호출 자체가 실패하면: failed
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from app.llm import ChatLLM, LLMError, Message, ToolCall, ToolFormatError

from .prompts import ASK_TEACHER_TOOL, FORMAT_CORRECTION
from .registry import ToolContext, ToolRegistry

Status = Literal["completed", "needs_teacher", "failed"]


@dataclass
class Trace:
    steps: list[dict[str, Any]] = field(default_factory=list)
    _t0: float = field(default_factory=time.perf_counter)

    def add(self, step: int, type_: str, **detail: Any) -> None:
        self.steps.append({"step": step, "type": type_, "t_ms": int((time.perf_counter() - self._t0) * 1000), **detail})


@dataclass
class AgentResult:
    status: Status
    answer: str
    question: str | None
    trace: list[dict[str, Any]]
    steps: int
    model: str


class AgentRunner:
    def __init__(
        self,
        llm: ChatLLM,
        registry: ToolRegistry,
        ctx: ToolContext,
        system_prompt: str,
        max_steps: int = 8,
        max_tool_failures: int = 3,
        max_format_errors: int = 2,
    ):
        self.llm = llm
        self.registry = registry
        self.ctx = ctx
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.max_tool_failures = max_tool_failures
        self.max_format_errors = max_format_errors

    async def run(self, instruction: str) -> AgentResult:
        tools = self.registry.openai_schemas(self.ctx.mode) + [ASK_TEACHER_TOOL]
        messages: list[Message] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": instruction},
        ]
        trace = Trace()
        trace.add(0, "instruction", mode=self.ctx.mode, text=instruction,
                  tools=[t["function"]["name"] for t in tools])
        format_errors = 0
        tool_failures = 0
        last_failure = ""

        def done(status: Status, answer: str, step: int, question: str | None = None) -> AgentResult:
            trace.add(step, "final", status=status, answer=answer, question=question)
            return AgentResult(status=status, answer=answer, question=question,
                               trace=trace.steps, steps=step, model=self.llm.model)

        for step in range(1, self.max_steps + 1):
            # 1) 모델에게 다음 행동을 묻는다
            try:
                resp = await self.llm.chat(messages, tools=tools)
            except ToolFormatError as e:
                format_errors += 1
                trace.add(step, "llm_format_error", error=e.message, raw=(e.raw or "")[:200])
                if format_errors >= self.max_format_errors:
                    q = "모델 응답 형식이 계속 깨져서 처리하지 못했습니다. 지시를 더 짧고 구체적으로 다시 말씀해 주시겠어요?"
                    return done("needs_teacher", q, step, question=q)
                messages.append({"role": "user", "content": FORMAT_CORRECTION})
                continue
            except LLMError as e:
                trace.add(step, "llm_error", error=e.to_dict())
                return done("failed", f"LLM 호출에 실패했습니다: {e.message}", step)

            trace.add(step, "llm", latency_ms=int(resp.latency_s * 1000), json_fallback=resp.used_json_fallback,
                      content=resp.content,
                      tool_calls=[{"name": c.name, "arguments": c.arguments} for c in resp.tool_calls])

            # 2) 도구 호출이 없으면 최종 답변
            if not resp.tool_calls:
                answer = (resp.content or "").strip()
                if not answer:
                    format_errors += 1
                    trace.add(step, "llm_format_error", error="빈 응답")
                    if format_errors >= self.max_format_errors:
                        q = "모델이 빈 응답을 돌려줬습니다. 다시 지시해 주시겠어요?"
                        return done("needs_teacher", q, step, question=q)
                    messages.append({"role": "user", "content": FORMAT_CORRECTION})
                    continue
                return done("completed", answer, step)

            # 3) 교사에게 질문
            ask = next((c for c in resp.tool_calls if c.name == "ask_teacher"), None)
            if ask is not None:
                q = str(ask.arguments.get("question") or "어떻게 진행할지 알려 주세요.")
                return done("needs_teacher", q, step, question=q)

            # 4) 도구 실행과 결과 확인
            messages.append(resp.assistant_message or _assistant_message(resp.content, resp.tool_calls))
            for call in resp.tool_calls:
                t0 = time.perf_counter()
                result = await self.registry.execute(call.name, call.arguments, self.ctx)
                retried = False
                if not result.ok and result.retryable:
                    trace.add(step, "tool_retry", name=call.name, error=result.error)
                    result = await self.registry.execute(call.name, call.arguments, self.ctx)
                    retried = True
                trace.add(step, "tool", name=call.name, arguments=call.arguments, ok=result.ok,
                          result=result.data if result.ok else None, error=result.error,
                          retried=retried, elapsed_ms=int((time.perf_counter() - t0) * 1000))
                if not result.ok:
                    tool_failures += 1
                    last_failure = f"{call.name}: {result.error}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": json.dumps(result.to_dict(), ensure_ascii=False),
                })

            if tool_failures >= self.max_tool_failures:
                q = f"도구 실행이 {tool_failures}번 실패해 멈췄습니다 (마지막 실패 - {last_failure}). 어떻게 할까요?"
                return done("needs_teacher", q, step, question=q)

        q = f"최대 단계 수({self.max_steps})에 닿아 멈췄습니다. 이어서 진행할까요, 아니면 지시를 나눠 주시겠어요?"
        return done("needs_teacher", q, self.max_steps, question=q)


def _assistant_message(content: str | None, calls: list[ToolCall]) -> Message:
    msg: Message = {
        "role": "assistant",
        "tool_calls": [
            {"id": c.id, "type": "function",
             "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
            for c in calls
        ],
    }
    if content:
        msg["content"] = content
    return msg
