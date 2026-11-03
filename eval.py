"""번역기 평가 스크립트.

예시 파일을 학습용(few-shot 후보)과 평가용으로 나눠, 평가용 gloss를 번역하고 정답과 비교한다.

    python eval.py --backend api
    python eval.py --backend fallback
    python eval.py --backend api --test-ratio 0.3 --seed 42

결과: results/eval_{backend}_{날짜시각}.md
"""
from __future__ import annotations

import argparse
import math
import random
import re
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Sequence, Set, Tuple

import sacrebleu

from app.config import ROOT, load_settings
from app.prompting.retriever import Example, load_examples
from app.schemas import GlossItem, Scenario, SentenceType, TranslationRequest
from app.service import build_service

# 지어내기 검사에서 무시할 기능어·보조 용언 (gloss에 없어도 정상)
ALLOWED_WORDS = {
    "저", "나", "제", "하", "되", "있", "없", "받", "싶", "주", "수", "것", "거", "때문", "좀", "혹시", "안", "못",
    "같", "보", "말씀", "드리", "이", "그", "어떻", "어떻게", "때", "데", "요", "번", "해", "되나", "가능", "시",
}
CONTENT_TAGS = ("NNG", "NNP", "NR", "SN", "VV", "VA", "XR", "MAG", "SL")
_PARTICLES = re.compile(r"(은|는|이|가|을|를|에|에서|으로|로|도|만|의|와|과|요|예요|이에요|해요|어요|아요)$")


def split_examples(examples: Sequence[Example], test_ratio: float, seed: int) -> Tuple[List[Example], List[Example]]:
    idx = list(range(len(examples)))
    random.Random(seed).shuffle(idx)
    n_test = max(1, round(len(examples) * test_ratio))
    test = [examples[i] for i in sorted(idx[:n_test])]
    train = [examples[i] for i in sorted(idx[n_test:])]
    return train, test


class Hallucination:
    """출력 문장의 내용어가 gloss에 없으면 표시. 사람이 검토할 목록을 뽑는 용도 (완벽하지 않음)."""

    def __init__(self) -> None:
        try:
            from kiwipiepy import Kiwi

            self.kiwi = Kiwi()
        except Exception:  # kiwipiepy가 없으면 단순 규칙으로
            self.kiwi = None

    def _content_words(self, text: str) -> List[str]:
        if self.kiwi is None:
            return [_PARTICLES.sub("", w.strip(".,?!")) for w in text.split() if w.strip(".,?!")]
        return [t.form for t in self.kiwi.tokenize(text) if t.tag.startswith(CONTENT_TAGS)]

    def _gloss_forms(self, glosses: Sequence[str]) -> Set[str]:
        forms: Set[str] = set()
        for g in glosses:
            forms.add(g)
            if g.endswith("다"):
                forms.add(g[:-1])
            forms.update(self._content_words(g))
        return forms

    def check(self, glosses: Sequence[str], sentence: str) -> List[str]:
        forms = self._gloss_forms(glosses)
        flagged = []
        for w in self._content_words(sentence):
            if not w or w in ALLOWED_WORDS or w in forms:
                continue
            # 부분 일치 허용: 전입신고 ↔ 전입/신고, 진료받 ↔ 진료
            if any(w in f or f in w for f in forms if len(f) >= 2):
                continue
            flagged.append(w)
        return sorted(set(flagged))


@dataclass
class Row:
    ex: Example
    sentence: str
    pred_type: str
    backend: str
    latency_ms: float
    flagged: List[str]


def p95(values: Sequence[float]) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, math.ceil(0.95 * len(s)) - 1)]


def run(backend: str, test_ratio: float, seed: int, give_type: bool) -> Path:
    settings = load_settings()
    settings.cache_enabled = False  # 평가에서는 캐시를 끈다
    examples = load_examples(settings.examples_path)
    train, test = split_examples(examples, test_ratio, seed)
    service = build_service(settings, examples=train, backend=backend)
    checker = Hallucination()

    rows: List[Row] = []
    for ex in test:
        req = TranslationRequest(
            glosses=[GlossItem(gloss=g) for g in ex.glosses],
            sentence_type=SentenceType(ex.type) if give_type else SentenceType.unknown,
            scenario=Scenario(ex.scenario) if ex.scenario in Scenario._value2member_map_ else Scenario.general,
        )
        resp = service.translate(req)
        rows.append(Row(ex, resp.sentence, resp.sentence_type.value, resp.backend, resp.latency_ms,
                        checker.check(ex.glosses, resp.sentence)))

    hyps = [r.sentence for r in rows]
    refs = [[r.ex.sentence for r in rows]]
    bleu = sacrebleu.corpus_bleu(hyps, refs)
    chrf = sacrebleu.corpus_chrf(hyps, refs)
    type_acc = sum(r.pred_type == r.ex.type for r in rows) / len(rows)
    lat = [r.latency_ms for r in rows]
    n_fallback = sum(r.backend.startswith("fallback") and backend != "fallback" for r in rows)

    now = datetime.now()
    out = ROOT / "results" / f"eval_{backend}_{now:%Y%m%d_%H%M%S}.md"
    out.parent.mkdir(exist_ok=True)

    def esc(s: str) -> str:
        return s.replace("|", "\\|")

    lines = [
        f"# 번역 평가: `{backend}` ({now:%Y-%m-%d %H:%M})",
        "",
        f"- 번역기: `{service.backend_name}`",
        f"- 예시 {len(examples)}개 → few-shot 후보 {len(train)}개 / 평가 {len(test)}개 (seed={seed})",
        f"- 문장 유형 입력: {'정답 유형 제공' if give_type else 'unknown (모델이 판단)'}",
        "- 주의: 예시는 직접 작성한 임시 데이터이고 평가셋이 작아 지표는 참고용입니다.",
        "",
        "## 지표",
        "",
        "| 지표 | 값 |",
        "|---|---|",
        f"| BLEU | {bleu.score:.2f} |",
        f"| chrF | {chrf.score:.2f} |",
        f"| 문장 유형 정확도 | {type_acc:.1%} |",
        f"| 응답 시간 평균 | {statistics.mean(lat):.0f} ms |",
        f"| 응답 시간 p95 | {p95(lat):.0f} ms |",
        f"| fallback 전환 | {n_fallback}/{len(rows)} |",
        f"| 지어내기 의심 문장 | {sum(bool(r.flagged) for r in rows)}/{len(rows)} |",
        "",
        "## 문장별 결과",
        "",
        "| gloss | 정답 | 출력 | 유형(정답/출력) | 지연 ms | 지어내기 의심 |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {esc(' / '.join(r.ex.glosses))} | {esc(r.ex.sentence)} | {esc(r.sentence)} | "
            f"{r.ex.type[:5]}/{r.pred_type[:5]} | {r.latency_ms:.0f} | {', '.join(r.flagged) or '-'} |"
        )
    lines += ["", "지어내기 의심 단어는 gloss·기능어 목록에 없는 내용어를 규칙으로 뽑은 것이라 오탐이 있습니다. 사람이 확인하세요.", ""]
    out.write_text("\n".join(lines), encoding="utf-8")

    print(f"BLEU {bleu.score:.2f} | chrF {chrf.score:.2f} | 유형 정확도 {type_acc:.1%} | "
          f"평균 {statistics.mean(lat):.0f}ms | p95 {p95(lat):.0f}ms | fallback {n_fallback}/{len(rows)}")
    print(f"저장: {out}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="gloss→문장 번역기 평가")
    ap.add_argument("--backend", default=None, help="api | fallback | kogpt2 | local_llm (기본: config.yaml)")
    ap.add_argument("--test-ratio", type=float, default=0.3)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-type", action="store_true", help="정답 문장 유형을 주지 않고 모델이 판단하게 함")
    args = ap.parse_args()
    run(args.backend or load_settings().backend, args.test_ratio, args.seed, give_type=not args.no_type)


if __name__ == "__main__":
    main()
