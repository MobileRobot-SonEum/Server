"""아이에게 가는 LLM 답변 검사: 길이, 지킬 수 없는 약속, 나이에 맞지 않는 말."""

from __future__ import annotations

from dataclasses import dataclass

from .plaintext import to_speech_text
from .rules import SafetyRules


@dataclass
class GuardResult:
    text: str
    replaced: bool
    reason: str | None = None  # promise | banned | empty | too_long_no_boundary | truncated


def guard_reply(reply: str | None, rules: SafetyRules, emotional: bool = False) -> GuardResult:
    fallback = rules.fallback_reply_emotional if emotional else rules.fallback_reply
    text = to_speech_text(reply)  # 마크다운·이모지·특수기호 제거 (temi가 읽어 준다)
    if not text:
        return GuardResult(fallback, True, "empty")
    for p in rules.promise_patterns:
        if p.search(text):
            return GuardResult(fallback, True, f"promise:{p.pattern}")
    for p in rules.banned_patterns:
        if p.search(text):
            return GuardResult(fallback, True, f"banned:{p.pattern}")
    if len(text) <= rules.max_chars:
        return GuardResult(text, False)
    # 길면 제한 안의 마지막 문장 끝에서 자른다
    head = text[: rules.max_chars]
    cut = max(head.rfind(c) for c in ".!?…")
    if cut >= 4:
        return GuardResult(head[: cut + 1].strip(), False, "truncated")
    return GuardResult(fallback, True, "too_long_no_boundary")
