"""오픈소스 LLM 번역기 (스텁).

구현 시: vLLM/Ollama 같은 로컬 서버를 호출하는 LLMProvider를 하나 만들어
ApiTranslator에 넘기는 방식이 가장 간단하다 (프롬프트·few-shot·파서를 그대로 재사용).
"""
from __future__ import annotations

from app.schemas import TranslationRequest, TranslationResult
from app.translators.base import Translator


class LocalLLMTranslator(Translator):
    name = "local_llm"

    def __init__(self, endpoint: str | None = None, model: str | None = None):
        self.endpoint = endpoint
        self.model = model

    def translate(self, request: TranslationRequest) -> TranslationResult:
        raise NotImplementedError("LocalLLMTranslator는 아직 구현되지 않았습니다.")
