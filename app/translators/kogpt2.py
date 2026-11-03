"""KoGPT2 파인튜닝 모델 번역기 (스텁).

구현 시: __init__에서 체크포인트를 로드하고, translate()에서
gloss → 프롬프트 문자열 → generate → TranslationResult로 변환한다.
GPU가 필요하면 EC2 인스턴스 유형(g4dn 등)과 Dockerfile 베이스 이미지를 바꿔야 한다.
"""
from __future__ import annotations

from app.schemas import TranslationRequest, TranslationResult
from app.translators.base import Translator


class KoGPT2Translator(Translator):
    name = "kogpt2"

    def __init__(self, checkpoint_path: str | None = None):
        self.checkpoint_path = checkpoint_path

    def translate(self, request: TranslationRequest) -> TranslationResult:
        raise NotImplementedError("KoGPT2Translator는 아직 구현되지 않았습니다.")
