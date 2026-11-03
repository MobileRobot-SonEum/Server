"""API가 실패했을 때 쓰는 규칙 기반 최소 대체 수단.

gloss를 이어 붙이고 마지막 용언만 해요체로 바꾼다. 어색해도 의미 전달이 목적이다.
"""
from __future__ import annotations

from app.schemas import SentenceType, TranslationRequest, TranslationResult
from app.translators.base import Translator

# 자주 나오는 용언의 해요체. 없는 단어는 '~하다'만 일반 규칙으로 처리한다.
HAEYO = {
    "하다": "해요", "되다": "돼요", "없다": "없어요", "있다": "있어요", "원하다": "원해요",
    "아프다": "아파요", "괜찮다": "괜찮아요", "모르다": "몰라요", "알다": "알아요", "가다": "가요",
    "오다": "와요", "받다": "받아요", "주다": "줘요", "싶다": "싶어요", "맞다": "맞아요",
    "보다": "봐요", "쓰다": "써요", "기다리다": "기다려요", "늦다": "늦어요", "잃어버리다": "잃어버렸어요",
    "끝나다": "끝나요", "먹다": "먹어요", "바꾸다": "바꿔요", "내다": "내요", "찾다": "찾아요",
}
WH_WORDS = {"무엇", "뭐", "어디", "언제", "누구", "왜", "어떻게", "얼마", "몇"}
FIRST_PERSON = {"나", "저"}


def to_haeyo(word: str) -> str:
    if word in HAEYO:
        return HAEYO[word]
    for base, conj in HAEYO.items():  # 복합어: 필요하다 → 필요해요, 진료받다 → 진료받아요
        if word.endswith(base) and len(word) > len(base):
            return word[: -len(base)] + conj
    return word


class FallbackTranslator(Translator):
    name = "fallback"

    def translate(self, request: TranslationRequest) -> TranslationResult:
        words = [w for w in request.gloss_words() if w]
        if len(words) > 1 and words[0] in FIRST_PERSON:
            words = words[1:]

        st = request.sentence_type
        if st == SentenceType.unknown:
            st = SentenceType.interrogative if any(w in WH_WORDS for w in words) else SentenceType.declarative

        if words:
            words[-1] = to_haeyo(words[-1])
        sentence = " ".join(words) + ("?" if st == SentenceType.interrogative else ".")
        return TranslationResult(sentence=sentence, candidates=[sentence], sentence_type=st)
