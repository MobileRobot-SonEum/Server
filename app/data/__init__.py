"""정적 데이터(아이 정보, 위치, 게임) 로더."""

from __future__ import annotations

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
        name = name.strip()
        for c in self.children:
            if c["name"] == name:
                return c
        return None

    def children_in_class(self, class_name: str) -> list[dict]:
        return [c for c in self.children if c["class"] == class_name.strip()]
