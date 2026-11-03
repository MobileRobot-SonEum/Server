"""모델 출력 JSON 파싱. 코드블록, 앞뒤 잡담, 잘못된 필드에 대비한다."""
from __future__ import annotations

import json
import re
from typing import Any, List

from app.schemas import SentenceType, TranslationResult
from app.translators.base import TranslatorError

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json_object(text: str) -> Any:
    text = text.strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise TranslatorError("parse_error")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise TranslatorError("parse_error") from e


def parse_model_output(text: str, num_candidates: int = 1) -> TranslationResult:
    data = _extract_json_object(text)
    if not isinstance(data, dict):
        raise TranslatorError("parse_error")

    sentence = data.get("sentence")
    raw_cands = data.get("candidates") or []
    if not isinstance(raw_cands, list):
        raw_cands = []
    cands: List[str] = [c.strip() for c in raw_cands if isinstance(c, str) and c.strip()]

    if not isinstance(sentence, str) or not sentence.strip():
        if not cands:
            raise TranslatorError("parse_error")
        sentence = cands[0]
    sentence = sentence.strip()

    # sentence를 맨 앞에 두고 중복 제거
    ordered: List[str] = []
    for c in [sentence, *cands]:
        if c not in ordered:
            ordered.append(c)

    st_raw = data.get("sentence_type")
    try:
        st = SentenceType(st_raw)
    except ValueError:
        st = SentenceType.interrogative if sentence.endswith("?") else SentenceType.declarative
    if st == SentenceType.unknown:
        st = SentenceType.interrogative if sentence.endswith("?") else SentenceType.declarative

    return TranslationResult(sentence=sentence, candidates=ordered[: max(1, num_candidates)], sentence_type=st)
