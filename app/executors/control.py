"""temi 앱과 WebSocket(/control)으로 통신하는 실행기 (스텁).

통신 규격은 팀에서 정의할 예정. 정해지면 아래 형태로 구현한다.
  - 연결: ws://<temi 앱 또는 중계 서버>/control (CONTROL_WS_URL)
  - 요청: {"id": "...", "action": "go_to", "params": {"location": "식당"}}
  - 응답: {"id": "...", "ok": true, "detail": {...}} 또는 {"id": "...", "ok": false, "error": "...", "retryable": true}
  - 이동처럼 오래 걸리는 동작은 도착 이벤트를 기다린다(타임아웃 포함).
"""

from __future__ import annotations

from .base import ExecResult

_NOT_READY = "ControlExecutor는 아직 구현되지 않았습니다 (temi 통신 규격 정의 대기). ROBOT_EXECUTOR=mock을 쓰세요."


class ControlExecutor:
    name = "control"

    def __init__(self, ws_url: str):
        self.ws_url = ws_url

    async def _todo(self) -> ExecResult:
        return ExecResult(ok=False, error=_NOT_READY, retryable=False)

    async def get_locations(self) -> ExecResult:
        return await self._todo()

    async def go_to(self, location: str) -> ExecResult:
        return await self._todo()

    async def speak(self, text: str) -> ExecResult:
        return await self._todo()

    async def start_delivery(self, kind: str, targets: list[str]) -> ExecResult:
        return await self._todo()

    async def start_game(self, name: str) -> ExecResult:
        return await self._todo()

    async def aclose(self) -> None:
        return None
