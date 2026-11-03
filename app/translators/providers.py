"""LLM 제공자 어댑터. ApiTranslator는 이 인터페이스만 안다.

제공자를 추가하려면 LLMProvider를 상속해 complete()만 구현하고 build_provider()에 등록한다.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, Dict

from app.translators.base import TranslatorError

# 모델 출력 JSON 스키마 (구조화 출력을 지원하는 제공자에 전달)
OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "sentence": {"type": "string"},
        "candidates": {"type": "array", "items": {"type": "string"}},
        "sentence_type": {"type": "string", "enum": ["declarative", "interrogative"]},
    },
    "required": ["sentence", "candidates", "sentence_type"],
    "additionalProperties": False,
}


class LLMProvider(ABC):
    name: str = "provider"

    def __init__(self, model: str):
        self.model = model

    @abstractmethod
    def complete(self, system: str, user: str, *, timeout: float, max_tokens: int) -> str:
        """모델의 텍스트 출력을 돌려준다.

        실패 시 TranslatorError를 던진다. reason:
          timeout / connection / server_error / rate_limited  → 재시도 가능
          auth / bad_request / refusal / truncated           → 재시도해도 소용없음
        """


RETRYABLE = {"timeout", "connection", "server_error", "rate_limited", "parse_error"}


class ClaudeProvider(LLMProvider):
    name = "claude"

    def __init__(self, model: str, api_key: str | None = None, structured_output: bool = True):
        super().__init__(model)
        import anthropic  # 지연 import: 다른 제공자만 쓸 때 의존성 불필요

        self._anthropic = anthropic
        # 재시도는 ApiTranslator가 마감 시간을 보며 직접 관리하므로 SDK 자동 재시도는 끈다.
        self.has_key = bool(api_key or os.getenv("ANTHROPIC_API_KEY"))
        self._client = anthropic.Anthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"), max_retries=0)
        self.structured_output = structured_output

    def complete(self, system: str, user: str, *, timeout: float, max_tokens: int) -> str:
        if not self.has_key:
            raise TranslatorError("no_api_key")
        a = self._anthropic
        output_config: Dict[str, Any] = {"effort": "low"}
        if self.structured_output:
            output_config["format"] = {"type": "json_schema", "schema": OUTPUT_SCHEMA}
        try:
            resp = self._client.with_options(timeout=timeout).messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                # 짧은 변환 작업이라 지연을 줄이기 위해 thinking을 끄고 effort를 낮춘다.
                thinking={"type": "disabled"},
                output_config=output_config,
            )
        except a.APITimeoutError as e:
            raise TranslatorError("timeout") from e
        except a.APIConnectionError as e:
            raise TranslatorError("connection") from e
        except a.RateLimitError as e:
            raise TranslatorError("rate_limited") from e
        except (a.AuthenticationError, a.PermissionDeniedError) as e:
            raise TranslatorError("auth") from e
        except a.APIStatusError as e:
            raise TranslatorError("server_error" if e.status_code >= 500 else "bad_request") from e

        if resp.stop_reason == "refusal":
            raise TranslatorError("refusal")
        if resp.stop_reason == "max_tokens":
            raise TranslatorError("truncated")
        text = "".join(b.text for b in resp.content if b.type == "text")
        if not text.strip():
            raise TranslatorError("empty_output")
        return text


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, model: str, api_key: str | None = None):
        super().__init__(model)
        import openai

        self._openai = openai
        self.has_key = bool(api_key or os.getenv("OPENAI_API_KEY"))
        # 키가 없어도 서버는 떠야 하므로(fallback으로 응답) 생성자 오류를 피하려 빈 문자열 대신 자리표시 키를 쓴다.
        self._client = openai.OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY") or "missing", max_retries=0)

    def complete(self, system: str, user: str, *, timeout: float, max_tokens: int) -> str:
        if not self.has_key:
            raise TranslatorError("no_api_key")
        o = self._openai
        try:
            resp = self._client.with_options(timeout=timeout).chat.completions.create(
                model=self.model,
                max_completion_tokens=max_tokens,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
        except o.APITimeoutError as e:
            raise TranslatorError("timeout") from e
        except o.APIConnectionError as e:
            raise TranslatorError("connection") from e
        except o.RateLimitError as e:
            raise TranslatorError("rate_limited") from e
        except (o.AuthenticationError, o.PermissionDeniedError) as e:
            raise TranslatorError("auth") from e
        except o.APIStatusError as e:
            raise TranslatorError("server_error" if e.status_code >= 500 else "bad_request") from e

        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise TranslatorError("truncated")
        text = choice.message.content or ""
        if not text.strip():
            raise TranslatorError("empty_output")
        return text


def build_provider(provider: str, model: str) -> LLMProvider:
    if provider == "claude":
        return ClaudeProvider(model)
    if provider == "openai":
        return OpenAIProvider(model)
    raise ValueError(f"unknown provider: {provider}")
