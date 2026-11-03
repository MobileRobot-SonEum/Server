"""설정값 → 번역기 생성."""
from __future__ import annotations

from typing import Optional, Sequence

from app.config import Settings
from app.prompting.retriever import Example, load_examples
from app.translators.base import Translator
from app.translators.fallback import FallbackTranslator
from app.translators.kogpt2 import KoGPT2Translator
from app.translators.local_llm import LocalLLMTranslator


def build_translator(settings: Settings, examples: Optional[Sequence[Example]] = None, backend: Optional[str] = None) -> Translator:
    backend = backend or settings.backend
    if backend == "api":
        from app.translators.api import ApiTranslator
        from app.translators.providers import build_provider

        if examples is None:
            examples = load_examples(settings.examples_path)
        provider = build_provider(settings.api.provider, settings.api.model)
        return ApiTranslator(provider, examples, settings.api, low_conf_threshold=settings.low_confidence_threshold)
    if backend == "kogpt2":
        return KoGPT2Translator()
    if backend == "local_llm":
        return LocalLLMTranslator()
    if backend == "fallback":
        return FallbackTranslator()
    raise ValueError(f"unknown backend: {backend}")
