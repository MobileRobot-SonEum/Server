import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import ApiSettings, Settings  # noqa: E402
from app.prompting.retriever import Example  # noqa: E402
from app.translators.base import TranslatorError  # noqa: E402
from app.translators.providers import LLMProvider  # noqa: E402


class FakeProvider(LLMProvider):
    """실제 API 대신 쓰는 모의 제공자. outputs 항목이 예외면 던지고, 문자열이면 돌려준다."""

    name = "fake"

    def __init__(self, outputs, delay_s: float = 0.0):
        super().__init__("fake-model")
        self.outputs = list(outputs)
        self.delay_s = delay_s
        self.calls = []

    def complete(self, system, user, *, timeout, max_tokens):
        import time

        self.calls.append({"system": system, "user": user, "timeout": timeout})
        if self.delay_s:
            time.sleep(self.delay_s)
        out = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        if isinstance(out, BaseException):
            raise out
        return out


@pytest.fixture
def settings():
    return Settings(
        backend="api",
        api=ApiSettings(provider="fake", model="fake-model", timeout_s=0.5, retries=1, total_deadline_s=1.0, few_shot_k=2),
        cache_enabled=True,
    )


@pytest.fixture
def examples():
    return [
        Example(("나", "감기", "진료", "원하다"), "감기 때문에 진료를 받고 싶어요.", "declarative", "hospital"),
        Example(("신분증", "없다", "휴대폰", "되다"), "신분증이 없는데 휴대폰으로 해도 되나요?", "interrogative", "hospital"),
        Example(("화장실", "어디"), "화장실이 어디예요?", "interrogative", "general"),
        Example(("수수료", "얼마"), "수수료가 얼마예요?", "interrogative", "civil_office"),
    ]


OK_JSON = '{"sentence": "감기 때문에 진료를 받고 싶어요.", "candidates": ["감기 때문에 진료를 받고 싶어요.", "감기 진료를 원해요."], "sentence_type": "declarative"}'

__all__ = ["FakeProvider", "TranslatorError", "OK_JSON"]
