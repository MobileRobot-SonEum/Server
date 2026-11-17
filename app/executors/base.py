from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ExecResult:
    ok: bool
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    retryable: bool = False  # 같은 동작을 다시 시도하면 될 수 있는 실패인가
    ask_teacher: str | None = None  # 교사 모드에서 더 진행하지 말고 이 질문으로 교사에게 알릴 때 (예: temi 연결 안 됨)


class RobotExecutor(Protocol):
    """temi 동작을 실제로 수행하는 계층. 도구는 이 인터페이스만 부른다."""

    name: str

    async def get_locations(self) -> ExecResult: ...
    async def go_to(self, location: str) -> ExecResult: ...
    async def speak(self, text: str) -> ExecResult: ...
    async def start_delivery(self, kind: str, targets: list[str]) -> ExecResult: ...
    async def start_game(self, name: str) -> ExecResult: ...
    async def stop(self) -> ExecResult: ...
    async def aclose(self) -> None: ...
