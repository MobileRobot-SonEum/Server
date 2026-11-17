"""정적 데이터(아이 정보, 위치, 게임) 로더."""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).parent


def _load(name: str) -> dict:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


@dataclass
class StaticData:
    children: list[dict] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    games: list[str] = field(default_factory=list)

    @classmethod
    def load(cls) -> "StaticData":
        return cls(
            children=_load("children.json")["children"],
            locations=_load("locations.json")["locations"],
            games=_load("games.json")["games"],
        )

    def find_child(self, name: str) -> dict | None:
        """정확히 같은 이름, 없으면 부를 때 붙는 '이'를 뗀 이름(다솜이 → 다솜)으로 찾는다."""
        name = name.strip()
        for candidate in _name_variants(name):
            for c in self.children:
                if c["name"] == candidate:
                    return c
        return None

    def similar_names(self, name: str, n: int = 3) -> list[str]:
        """아이·반 이름 중 비슷한 후보."""
        pool = [c["name"] for c in self.children] + sorted({c["class"] for c in self.children})
        out: list[str] = []
        for v in _name_variants(name.strip()):
            for m in difflib.get_close_matches(v, pool, n=n, cutoff=0.5):
                if m not in out:
                    out.append(m)
        return out[:n]

    def children_in_class(self, class_name: str) -> list[dict]:
        return [c for c in self.children if c["class"] == class_name.strip()]


def _name_variants(name: str) -> list[str]:
    variants = [name]
    # 부를 때 붙는 말: 다솜이, 다솜이네, 다솜이가 ...
    for suffix in ("이네", "이가", "이는", "이도", "이랑", "이"):
        if name.endswith(suffix) and len(name) - len(suffix) >= 1:
            variants.append(name[: -len(suffix)])
    return variants


def similar(value: str, options: list[str], n: int = 3) -> list[str]:
    return difflib.get_close_matches(value.strip(), options, n=n, cutoff=0.4)
