"""few-shot 예시 로드와 선택.

select_examples()만 바꾸면 임베딩 검색으로 교체할 수 있다.
(같은 시그니처로 query gloss → 상위 k개 Example을 돌려주면 된다.)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence


@dataclass(frozen=True)
class Example:
    glosses: tuple
    sentence: str
    type: str = "declarative"
    scenario: str = "general"
    meta: dict = field(default_factory=dict, compare=False, hash=False)


def normalize_gloss(g: str) -> str:
    return g.strip().lower()


def load_examples(path: Path) -> List[Example]:
    """data/examples.jsonl 읽기. '//'나 '#'로 시작하는 줄과 _comment 레코드는 건너뛴다."""
    out: List[Example] = []
    if not Path(path).exists():
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "//")):
            continue
        rec = json.loads(line)
        if "_comment" in rec:
            continue
        out.append(
            Example(
                glosses=tuple(rec["glosses"]),
                sentence=rec["sentence"],
                type=rec.get("type", "declarative"),
                scenario=rec.get("scenario", "general"),
                meta={k: v for k, v in rec.items() if k not in {"glosses", "sentence", "type", "scenario"}},
            )
        )
    return out


def overlap_score(query: Iterable[str], example: Example) -> float:
    q = {normalize_gloss(g) for g in query}
    e = {normalize_gloss(g) for g in example.glosses}
    if not q or not e:
        return 0.0
    return len(q & e) / len(q | e)  # Jaccard


def select_examples(
    query_glosses: Sequence[str],
    pool: Sequence[Example],
    k: int = 4,
    scenario: Optional[str] = None,
) -> List[Example]:
    """입력 gloss와 단어가 많이 겹치는 예시 k개. 같은 시나리오는 가산점.

    겹치는 예시가 k개보다 적으면 같은 시나리오 예시로 채워, 출력 형식 시연용 예시가 항상 들어가게 한다.
    """
    if k <= 0 or not pool:
        return []
    scored = []
    for idx, ex in enumerate(pool):
        s = overlap_score(query_glosses, ex)
        if scenario and ex.scenario == scenario:
            s += 0.1
        scored.append((s, idx, ex))
    # 점수 내림차순, 동점이면 파일 순서 (결정적)
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [ex for _, _, ex in scored[:k]]
