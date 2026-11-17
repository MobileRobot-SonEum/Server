"""에이전트 루프: 지시 → (계획) → 도구 호출 → 결과 확인 → 다음 단계.

멈추는 경우
- 모델이 도구 없이 답하면: completed
- 모델이 ask_teacher를 부르거나, 형식 오류·도구 실패가 반복되거나, 최대 단계에 닿으면: needs_teacher (교사에게 질문)
- LLM 호출 자체가 실패하면: failed
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from app.llm import ChatLLM, LLMError, Message, ToolCall, ToolFormatError
from app.safety.plaintext import has_foreign_script, to_plain_text
from app.safety.teacher_filter import TeacherEmergencyRules

from .prompts import ASK_TEACHER_TOOL, FORMAT_CORRECTION, KOREAN_CORRECTION
from .registry import ToolContext, ToolRegistry

Status = Literal["completed", "needs_teacher", "failed"]

SLOW_MESSAGE = "응답이 늦어지고 있어요. 다시 시도해 주세요."


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
    timed_out: bool = False
    emergency_redirect: bool = False  # 응급 지시라 규칙으로 안내만 하고 끝냄 (LLM 미호출)


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
        deadline_s: float | None = 60.0,  # 지시 하나 전체 처리 시간 상한
        emergency_rules: TeacherEmergencyRules | None = None,
    ):
        self.llm = llm
        self.registry = registry
        self.ctx = ctx
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.max_tool_failures = max_tool_failures
        self.max_format_errors = max_format_errors
        self.deadline_s = deadline_s
        self.emergency_rules = emergency_rules

    async def run(self, instruction: str) -> AgentResult:
        trace = Trace()
        state = {"step": 0}
        try:
            return await asyncio.wait_for(self._run(instruction, trace, state), timeout=self.deadline_s)
        except asyncio.TimeoutError:
            trace.add(state["step"], "timeout", deadline_s=self.deadline_s)
            trace.add(state["step"], "final", status="failed", answer=SLOW_MESSAGE, question=None)
            return AgentResult(status="failed", answer=SLOW_MESSAGE, question=None, trace=trace.steps,
                               steps=state["step"], model=self.llm.model, timed_out=True)

    async def _run(self, instruction: str, trace: Trace, state: dict) -> AgentResult:
        tools = self.registry.openai_schemas(self.ctx.mode) + [ASK_TEACHER_TOOL]
        messages: list[Message] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": instruction},
        ]
        trace.add(0, "instruction", mode=self.ctx.mode, text=instruction,
                  tools=[t["function"]["name"] for t in tools])

        # 응급 지시: LLM을 부르지 않고 규칙으로 안내 (설계 원칙 1)
        if self.emergency_rules is not None:
            matched = self.emergency_rules.check(instruction)
            if matched:
                trace.add(0, "rule", rule="teacher_emergency", matched=matched)
                trace.add(0, "final", status="completed", answer=self.emergency_rules.reply, question=None)
                return AgentResult(status="completed", answer=self.emergency_rules.reply, question=None,
                                   trace=trace.steps, steps=0, model=self.llm.model, emergency_redirect=True)

        format_errors = 0
        tool_failures = 0
        last_failure = ""

        def done(status: Status, answer: str, step: int, question: str | None = None,
                 timed_out: bool = False) -> AgentResult:
            trace.add(step, "final", status=status, answer=answer, question=question)
            return AgentResult(status=status, answer=answer, question=question,
                               trace=trace.steps, steps=step, model=self.llm.model, timed_out=timed_out)

        for step in range(1, self.max_steps + 1):
            state["step"] = step
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
                if e.kind == "timeout":  # 재시도까지 시간 초과
                    return done("failed", SLOW_MESSAGE, step, timed_out=True)
                return done("failed", f"LLM 호출에 실패했습니다: {e.message}", step)

            trace.add(step, "llm", latency_ms=int(resp.latency_s * 1000), json_fallback=resp.used_json_fallback,
                      content=resp.content,
                      tool_calls=[{"name": c.name, "arguments": c.arguments} for c in resp.tool_calls])

            # 2) 도구 호출이 없으면 최종 답변
            if not resp.tool_calls:
                answer = to_plain_text(resp.content)  # 교사 앱 형식이 정해지기 전까지 일반 텍스트
                if answer and has_foreign_script(answer):
                    format_errors += 1
                    trace.add(step, "llm_format_error", error="한국어가 아닌 답변")
                    if format_errors >= self.max_format_errors:
                        q = "모델이 한국어로 답하지 못했습니다. 지시를 다시 말씀해 주시겠어요?"
                        return done("needs_teacher", q, step, question=q)
                    messages.append({"role": "assistant", "content": resp.content or ""})
                    messages.append({"role": "user", "content": KOREAN_CORRECTION})
                    continue
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
                if result.ask_teacher:
                    # 목록 밖 장소·게임 등: 모델이 추측해서 이어가지 않게 바로 교사에게 묻는다
                    return done("needs_teacher", result.ask_teacher, step, question=result.ask_teacher)

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
