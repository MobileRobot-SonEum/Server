"""temi 앱과 WebSocket(/ws/temi)으로 연결된 실제 실행기.

- 명령을 보내고 temi의 ack까지만 기다린다(기본 5초). 도구 결과는 "started + job_id".
  완료·실패는 temi가 나중에 result로 보내고 robot_jobs에 기록된다(GET /robot/jobs).
  그래서 에이전트의 지시 처리 시간(AGENT_DEADLINE_S)은 로봇 동작 완료를 기다리지 않는다.
- temi가 연결돼 있지 않으면 즉시 실패하고, 에이전트는 교사에게 알린다(ask_teacher).
- 아이 모드에서 온 명령은 여기서 한 번 더 막는다: start_game만 통과.
규격: docs/control_protocol.md
"""

from __future__ import annotations

from app.control import AckTimeout, TemiAmbiguous, TemiHub, TemiNotConnected

from .base import ExecResult

NOT_CONNECTED_Q = "temi가 서버에 연결되어 있지 않아요. temi 앱이 켜져 있고 와이파이에 연결돼 있는지 확인해 주세요. 연결된 뒤 다시 지시해 주시겠어요?"
CHILD_ALLOWED_ACTIONS = ("start_game",)


class ControlExecutor:
    name = "control"

    def __init__(self, hub: TemiHub, locations: list[str], device_id: str | None = None, mode: str = "teacher"):
        self.hub = hub
        self.locations = list(locations)
        self.device_id = device_id
        self.mode = mode

    def bind(self, device_id: str | None, mode: str) -> "ControlExecutor":
        """요청마다 대상 temi와 모드를 정한 실행기."""
        return ControlExecutor(self.hub, self.locations, device_id, mode)

    async def _run(self, action: str, args: dict) -> ExecResult:
        if self.mode == "child" and action not in CHILD_ALLOWED_ACTIONS:
            return ExecResult(ok=False, error=f"아이 모드에서는 {action} 명령을 보낼 수 없습니다 (start_game만 허용)")
        try:
            device = self.hub.resolve_device(self.device_id)
        except TemiNotConnected as e:
            return ExecResult(ok=False, error=f"temi 연결 안 됨: {e}", ask_teacher=NOT_CONNECTED_Q)
        except TemiAmbiguous as e:
            return ExecResult(ok=False, error=str(e), ask_teacher=f"{e}. 어느 temi에 시킬까요?")
        try:
            job = await self.hub.send_command(device, action, args, source_mode=self.mode)
        except TemiNotConnected as e:
            return ExecResult(ok=False, error=f"temi 연결 안 됨: {e}", ask_teacher=NOT_CONNECTED_Q)
        except AckTimeout as e:
            return ExecResult(ok=False, error=str(e),
                              ask_teacher="temi가 명령에 응답하지 않아요. temi 화면 상태를 확인한 뒤 다시 지시해 주시겠어요?")
        return ExecResult(ok=True, detail={
            "status": "started", "job_id": job["id"], "device_id": device, "action": action,
            "note": "temi가 명령을 받아 시작했습니다. 완료 여부는 아직 모릅니다(작업 기록에서 확인).",
        })

    async def get_locations(self) -> ExecResult:
        return ExecResult(ok=True, detail={"locations": self.locations})

    async def go_to(self, location: str) -> ExecResult:
        return await self._run("go_to", {"location": location})

    async def speak(self, text: str) -> ExecResult:
        return await self._run("speak", {"text": text})

    async def start_delivery(self, kind: str, targets: list[str]) -> ExecResult:
        return await self._run("start_delivery", {"kind": kind, "targets": targets})

    async def start_game(self, name: str) -> ExecResult:
        return await self._run("start_game", {"name": name})

    async def stop(self) -> ExecResult:
        return await self._run("stop", {})

    async def aclose(self) -> None:
        return None
