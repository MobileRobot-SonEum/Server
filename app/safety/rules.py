"""config/child_safety.yaml 로더."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "child_safety.yaml"


@dataclass
class Rule:
    level: str  # urgent | normal
    category: str
    pattern: str
    regex: re.Pattern

    @property
    def name(self) -> str:
        return f"{self.level}:{self.category}:{self.pattern}"


@dataclass
class SafetyRules:
    urgent_fixed_reply: str
    exclusions: list[str]
    rules: list[Rule]
    max_chars: int
    fallback_reply: str
    fallback_reply_emotional: str
    promise_patterns: list[re.Pattern] = field(default_factory=list)
    banned_patterns: list[re.Pattern] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "SafetyRules":
        raw = yaml.safe_load(Path(path or DEFAULT_RULES_PATH).read_text(encoding="utf-8"))
        rules: list[Rule] = []
        # 긴급을 먼저 검사한다
        for section, level in (("urgent", "urgent"), ("emotional", "normal")):
            for category, patterns in (raw.get(section) or {}).items():
                for p in patterns or []:
                    rules.append(Rule(level=level, category=category, pattern=p, regex=re.compile(p)))
        og = raw.get("output_guard") or {}
        return cls(
            urgent_fixed_reply=raw["urgent_fixed_reply"],
            exclusions=list(raw.get("exclusions") or []),
            rules=rules,
            max_chars=int(og.get("max_chars", 80)),
            fallback_reply=og["fallback_reply"],
            fallback_reply_emotional=og.get("fallback_reply_emotional", og["fallback_reply"]),
            promise_patterns=[re.compile(p) for p in og.get("promise_patterns") or []],
            banned_patterns=[re.compile(p) for p in og.get("banned_patterns") or []],
        )
