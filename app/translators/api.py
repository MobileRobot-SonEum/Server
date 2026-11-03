"""상용 LLM API(few-shot 프롬프트) 번역기."""
from __future__ import annotations

import time
from typing import Callable, List, Optional, Sequence

from app.config import ApiSettings
from app.prompting.parser import parse_model_output
from app.prompting.retriever import Example, select_examples
from app.prompting.system_prompt import SYSTEM_PROMPT, build_user_prompt
from app.schemas import TranslationRequest, TranslationResult
from app.translators.base import Translator, TranslatorError
from app.translators.providers import RETRYABLE, LLMProvider

ExampleSelector = Callable[[Sequence[str], Sequence[Example], int, Optional[str]], List[Example]]

_MIN_ATTEMPT_S = 0.3  # 남은 시간이 이보다 적으면 재시도하지 않는다


class ApiTranslator(Translator):
    def __init__(
        self,
        provider: LLMProvider,
        examples: Sequence[Example],
        settings: ApiSettings,
        *,
        low_conf_threshold: float = 0.6,
        selector: ExampleSelector = select_examples,
    ):
        self.provider = provider
        self.examples = list(examples)
        self.settings = settings
        self.low_conf_threshold = low_conf_threshold
        self.selector = selector
        self.name = f"api:{provider.name}:{provider.model}"

    def build_prompt(self, request: TranslationRequest) -> tuple[str, str]:
        shots = self.selector(request.gloss_words(), self.examples, self.settings.few_shot_k, request.scenario.value)
        user = build_user_prompt(
            request, shots, num_candidates=request.num_candidates, low_conf_threshold=self.low_conf_threshold
        )
        return SYSTEM_PROMPT, user

    def translate(self, request: TranslationRequest) -> TranslationResult:
        system, user = self.build_prompt(request)
        deadline = time.perf_counter() + self.settings.total_deadline_s
        last: Optional[TranslatorError] = None

        for _attempt in range(self.settings.retries + 1):
            remaining = deadline - time.perf_counter()
            if remaining < _MIN_ATTEMPT_S:
                break
            try:
                text = self.provider.complete(
                    system,
                    user,
                    timeout=min(self.settings.timeout_s, remaining),
                    max_tokens=self.settings.max_tokens,
                )
                result = parse_model_output(text, request.num_candidates)
                if request.sentence_type.value != "unknown":
                    result.sentence_type = request.sentence_type  # 비수지 신호 판별 결과를 우선
                return result
            except TranslatorError as e:
                last = e
                if e.reason not in RETRYABLE:
                    break
        raise last or TranslatorError("deadline_exceeded")
