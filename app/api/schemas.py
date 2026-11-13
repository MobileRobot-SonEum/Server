from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TeacherRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000, examples=["간식 시간이야. 식당으로 가서 햇살반 간식 배부 시작해 줘"])


class AgentResponse(BaseModel):
    status: Literal["completed", "needs_teacher", "failed"]
    answer: str
    question: str | None = None
    steps: int
    model: str
    trace: list[dict[str, Any]]


class ModelInfo(BaseModel):
    model: str
    host: str
    tool_mode: str
    reasoning_effort: str | None
    reachable: bool | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"]
    executor: str
    models: dict[str, ModelInfo]
