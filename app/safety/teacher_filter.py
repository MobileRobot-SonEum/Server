"""교사 모드 응급 지시 규칙 (config/teacher_emergency.yaml).

응급 대응은 에이전트가 하지 않는다(설계 원칙 1). 교사가 지금 벌어진 응급 상황을 말하면
LLM을 부르지 않고 비상 버튼·교사 앱·현장 대응을 안내하는 고정 답변을 돌려준다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "teacher_emergency.yaml"
_WS = re.compile(r"\s+")


@dataclass
class TeacherEmergencyRules:
    reply: str
    patterns: list[re.Pattern]
    exclusions: list[str]

    @classmethod
    def load(cls, path: str | Path | None = None) -> "TeacherEmergencyRules":
        raw = yaml.safe_load(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
        return cls(reply=raw["reply"], patterns=[re.compile(p) for p in raw.get("patterns") or []],
                   exclusions=list(raw.get("exclusions") or []))

    def check(self, instruction: str) -> str | None:
        """걸리면 규칙 이름(패턴), 아니면 None."""
        t = _WS.sub(" ", instruction).strip()
        if any(word in t for word in self.exclusions):
            return None
        for p in self.patterns:
            if p.search(t):
                return p.pattern
        return None
