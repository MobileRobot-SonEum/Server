from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Message = dict[str, Any]  # OpenAI Chat Completions 메시지 형식


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    used_json_fallback: bool = False
    latency_s: float = 0.0
    # 대화 기록에 다시 넣을 assistant 메시지. 제공자가 붙인 추가 필드(예: Gemini thought signature)를 보존한다.
    assistant_message: Message | None = None


ErrorKind = Literal["timeout", "connection", "http", "bad_response", "tool_format"]


class LLMError(Exception):
    def __init__(self, kind: ErrorKind, message: str, status: int | None = None, raw: str | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status
        self.raw = raw

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "message": self.message, "status": self.status}


class ToolFormatError(LLMError):
    """모델이 도구 호출이나 JSON 출력 형식을 깨뜨렸을 때."""

    def __init__(self, message: str, raw: str | None = None):
        super().__init__("tool_format", message, raw=raw)


class ChatLLM(Protocol):
    model: str

    async def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse: ...
