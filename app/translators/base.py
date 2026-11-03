"""번역기 공통 인터페이스. 모든 번역 모델은 이 클래스 뒤에 숨는다."""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas import TranslationRequest, TranslationResult


class TranslatorError(Exception):
    """번역 실패. reason은 로그·backend 표시에 쓰는 짧은 코드 (원문을 담지 말 것)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Translator(ABC):
    name: str = "base"

    @abstractmethod
    def translate(self, request: TranslationRequest) -> TranslationResult:
        """gloss 나열을 한국어 문장으로 옮긴다. 실패하면 TranslatorError를 던진다."""
