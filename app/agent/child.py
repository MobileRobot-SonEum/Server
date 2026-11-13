"""아이 모드: child_filter → (통과하면) LLM → output_guard → 응답.

개인정보: 이 모듈은 아이 발화 원문을 로그에 쓰지 않는다. 원문이 저장되는 곳은
교사 알림이 생긴 경우의 teacher_alerts뿐이고, 단기 대화 기억은 프로세스 메모리에만 있다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

from app.llm import ChatLLM, LLMError, Message
from app.safety import ChildConversationMemory, SafetyRules, check_utterance, guard_reply
from app.services import TeacherNotifier
from app.storage import Repository

from .prompts import build_child_prompt
from .registry import CHILD, ToolContext, ToolRegistry

log = logging.getLogger(__name__)

MAX_LLM_CALLS = 2  # 게임 시작 도구 호출 1번 + 답변 1번


@dataclass
class ChildReply:
    reply: str
    alert_created: bool = False
    alert_level: str | None = None
    alert_id: int | None = None
    game_started: str | None = None
    fallback_reason: str | None = None  # 기본 답변으로 바꾼 이유 (LLM 실패, 약속 표현 등)
    llm_called: bool = False
    model: str | None = None
    latency_ms: int = 0
    # 평가용 (API 응답·로그·DB에는 내보내지 않음)
    raw_reply: str | None = None  # output_guard 전 원본
    tool_attempts: list[str] = field(default_factory=list)  # LLM이 시도한 도구 이름 (차단된 것 포함)


class ChildAgent:
    def __init__(self, llm: ChatLLM, registry: ToolRegistry, ctx: ToolContext, rules: SafetyRules,
                 repo: Repository, notifier: TeacherNotifier, memory: ChildConversationMemory,
                 deadline_s: float | None = 10.0):
        assert ctx.mode == CHILD
        self.llm = llm
        self.registry = registry
        self.ctx = ctx
        self.rules = rules
        self.repo = repo
        self.notifier = notifier
        self.memory = memory
        self.deadline_s = deadline_s  # LLM 단계 전체 시간 상한. 넘으면 기본 답변

    async def handle(self, text: str, device_id: str | None = None) -> ChildReply:
        t0 = time.perf_counter()
        result = await self._handle(text, device_id)
        result.latency_ms = int((time.perf_counter() - t0) * 1000)
        # 원문 없이 메타데이터만 기록
        log.info("child: device=%s alert=%s llm=%s fallback=%s game=%s %dms", device_id, result.alert_level,
                 result.llm_called, result.fallback_reason, result.game_started, result.latency_ms)
        return result

    async def _handle(self, text: str, device_id: str | None) -> ChildReply:
        signal = check_utterance(text, self.rules)

        # 1) 긴급: LLM에 넘기지 않는다. 저장과 동시에 교사에게 바로 알린다.
        if signal.level == "urgent":
            alert = self.repo.add_alert("urgent", signal.category, signal.matched, text, device_id)
            try:
                await self.notifier.notify_urgent(alert)
            except Exception:  # 알림 전송 실패해도 아이에게는 고정 답변, 알림은 DB에 남아 있음
                log.exception("긴급 교사 알림 전송 실패 alert_id=%s", alert["id"])
            return ChildReply(reply=self.rules.urgent_fixed_reply, alert_created=True, alert_level="urgent",
                              alert_id=alert["id"], fallback_reason="urgent_fixed_reply")

        # 2) 정서: 일반 알림은 저장만 하고 LLM이 위로하는 답변을 만든다.
        reply = ChildReply(reply="")
        emotional = signal.level == "normal"
        if emotional:
            alert = self.repo.add_alert("normal", signal.category, signal.matched, text, device_id)
            reply.alert_created, reply.alert_level, reply.alert_id = True, "normal", alert["id"]

        # 3) LLM (시간 상한을 넘으면 기본 답변)
        try:
            raw = await asyncio.wait_for(self._ask_llm(text, device_id, emotional, reply), timeout=self.deadline_s)
        except asyncio.TimeoutError:
            raw = None
            reply.fallback_reason = "timeout"

        # 4) 출력 검사
        reply.raw_reply = raw
        if raw is None:
            reply.reply = self.rules.fallback_reply_emotional if emotional else self.rules.fallback_reply
        else:
            g = guard_reply(raw, self.rules, emotional=emotional)
            reply.reply = g.text
            if g.replaced:
                reply.fallback_reason = g.reason
        self.memory.add(device_id, text, reply.reply)
        return reply

    async def _ask_llm(self, text: str, device_id: str | None, emotional: bool, out: ChildReply) -> str | None:
        messages: list[Message] = [
            {"role": "system", "content": build_child_prompt(self.ctx.data.games, emotional)},
            *self.memory.history(device_id),
            {"role": "user", "content": text},
        ]
        tools = self.registry.openai_schemas(CHILD)
        out.llm_called = True
        out.model = self.llm.model
        for _ in range(MAX_LLM_CALLS):
            try:
                resp = await self.llm.chat(messages, tools=tools)
            except LLMError as e:
                out.fallback_reason = "timeout" if e.kind == "timeout" else f"llm_error:{e.kind}"
                return None
            if not resp.tool_calls:
                return resp.content
            messages.append(resp.assistant_message or {"role": "assistant", "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                for c in resp.tool_calls]})
            for call in resp.tool_calls:
                out.tool_attempts.append(call.name)
                # 아이 모드 ctx라 허용되지 않은 도구는 registry가 막는다
                result = await self.registry.execute(call.name, call.arguments, self.ctx)
                if result.ok and call.name == "start_game":
                    out.game_started = call.arguments.get("name")
                messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                 "content": json.dumps(result.to_dict(), ensure_ascii=False)})
        out.fallback_reason = "llm_no_final_reply"
        return None
