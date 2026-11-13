"""테스트 공통 준비. 실제 LLM 대신 정해진 응답을 차례로 돌려주는 ScriptedLLM을 쓴다."""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import LLMSettings, Settings
from app.data import StaticData
from app.executors import MockRobotExecutor
from app.llm import LLMResponse, ToolCall
from app.main import create_app

TEACHER_KEY = "test-teacher-key"
TEMI_KEY = "test-temi-key"
RULE_KEY = "test-rule-key"

_ids = itertools.count(1)


def call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"call_{next(_ids)}", name=name, arguments=arguments)


def tools_reply(*calls: ToolCall) -> LLMResponse:
    return LLMResponse(content=None, tool_calls=list(calls), model="scripted")


def text_reply(text: str) -> LLMResponse:
    return LLMResponse(content=text, model="scripted")


class ScriptedLLM:
    """chat()이 불릴 때마다 script의 다음 항목을 돌려준다. 예외 객체면 던진다."""

    def __init__(self, script: list[LLMResponse | Exception] | None = None, model: str = "scripted"):
        self.script = list(script or [])
        self.model = model
        self.requests: list[dict[str, Any]] = []

    async def chat(self, messages, tools=None) -> LLMResponse:
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools})
        if not self.script:
            raise AssertionError("ScriptedLLM: 준비된 응답이 더 없습니다")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def tool_names_offered(self, i: int = 0) -> list[str]:
        return [t["function"]["name"] for t in (self.requests[i]["tools"] or [])]


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        teacher_llm=LLMSettings(base_url="http://localhost:11434/v1", model="test-teacher"),
        child_llm=LLMSettings(base_url="http://localhost:11434/v1", model="test-child"),
        teacher_app_api_key=TEACHER_KEY,
        temi_api_key=TEMI_KEY,
        rule_engine_api_key=RULE_KEY,
        mock_move_delay_s=0.0,
        db_path=":memory:",
    )


@pytest.fixture
def data() -> StaticData:
    return StaticData.load()


@pytest.fixture
def executor(data) -> MockRobotExecutor:
    return MockRobotExecutor(locations=data.locations, games=data.games, move_delay_s=0.0)


@pytest.fixture
def teacher_llm() -> ScriptedLLM:
    return ScriptedLLM(model="scripted-teacher")


@pytest.fixture
def child_llm() -> ScriptedLLM:
    return ScriptedLLM(model="scripted-child")


@pytest.fixture
def client(settings, teacher_llm, child_llm, executor):
    app = create_app(settings, teacher_llm=teacher_llm, child_llm=child_llm, executor=executor)
    with TestClient(app) as c:
        yield c
