"""아이 발화 위험 신호 필터. LLM에 넘기기 전에 규칙으로 검사한다."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .rules import SafetyRules

_WS = re.compile(r"\s+")


@dataclass
class FilterResult:
    level: Literal["urgent", "normal"] | None  # None이면 신호 없음
    category: str | None = None
    matched: str | None = None  # 걸린 규칙 이름 (기준 조정용으로 알림에 저장)

    @property
    def flagged(self) -> bool:
        return self.level is not None


def normalize(text: str, exclusions: list[str]) -> str:
    t = _WS.sub(" ", text).strip()
    for word in exclusions:
        t = t.replace(word, " ")
    return t


def check_utterance(text: str, rules: SafetyRules) -> FilterResult:
    t = normalize(text, rules.exclusions)
    for rule in rules.rules:  # 긴급 규칙이 먼저 온다
        if rule.regex.search(t):
            return FilterResult(level=rule.level, category=rule.category, matched=rule.name)  # type: ignore[arg-type]
    return FilterResult(level=None)
