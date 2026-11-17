"""가짜 실행기. 실제 temi 없이 에이전트 흐름을 돌려 보기 위한 것."""

from __future__ import annotations

import asyncio
import random

from .base import ExecResult


class MockRobotExecutor:
    name = "mock"

    def __init__(
        self,
        locations: list[str],
        games: list[str],
        move_delay_s: float = 0.5,
        failure_rate: float = 0.0,
        fail_tools: list[str] | None = None,
        seed: int | None = None,
    ):
        self.locations = list(locations)
        self.games = list(games)
        self.move_delay_s = move_delay_s
        self.failure_rate = failure_rate
        self.fail_tools = set(fail_tools or [])
        self._rng = random.Random(seed)
        self._fail_next: dict[str, int] = {}
        self.current_location = "충전기" if "충전기" in self.locations else (self.locations[0] if self.locations else "")
        self.calls: list[tuple[str, dict]] = []  # 테스트에서 확인용

    # 테스트·시연용: 다음 n번의 호출을 일부러 실패시킨다
    def fail_next(self, action: str, times: int = 1) -> None:
        self._fail_next[action] = self._fail_next.get(action, 0) + times

    def _injected_failure(self, action: str) -> ExecResult | None:
        if self._fail_next.get(action, 0) > 0:
            self._fail_next[action] -= 1
            return ExecResult(ok=False, error=f"[mock] {action} 일시 실패 (주입)", retryable=True)
        if self.failure_rate > 0 and (not self.fail_tools or action in self.fail_tools):
            if self._rng.random() < self.failure_rate:
                return ExecResult(ok=False, error=f"[mock] {action} 일시 실패 (확률 {self.failure_rate})", retryable=True)
        return None

    async def get_locations(self) -> ExecResult:
        self.calls.append(("get_locations", {}))
        return ExecResult(ok=True, detail={"locations": self.locations, "current": self.current_location})

    async def go_to(self, location: str) -> ExecResult:
        self.calls.append(("go_to", {"location": location}))
        if location not in self.locations:
            return ExecResult(ok=False, error=f"저장되지 않은 위치입니다: {location}. 저장된 위치: {', '.join(self.locations)}")
        if (f := self._injected_failure("go_to")):
            return f
        await asyncio.sleep(self.move_delay_s)
        start = self.current_location
        self.current_location = location
        return ExecResult(ok=True, detail={"from": start, "arrived": location})

    async def speak(self, text: str) -> ExecResult:
        self.calls.append(("speak", {"text": text}))
        if (f := self._injected_failure("speak")):
            return f
        return ExecResult(ok=True, detail={"spoken": text, "at": self.current_location})

    async def start_delivery(self, kind: str, targets: list[str]) -> ExecResult:
        self.calls.append(("start_delivery", {"kind": kind, "targets": targets}))
        if (f := self._injected_failure("start_delivery")):
            return f
        return ExecResult(ok=True, detail={"kind": kind, "targets": targets, "status": "started"})

    async def start_game(self, name: str) -> ExecResult:
        self.calls.append(("start_game", {"name": name}))
        if name not in self.games:
            return ExecResult(ok=False, error=f"없는 게임입니다: {name}")
        if (f := self._injected_failure("start_game")):
            return f
        return ExecResult(ok=True, detail={"game": name, "status": "started"})

    async def stop(self) -> ExecResult:
        self.calls.append(("stop", {}))
        return ExecResult(ok=True, detail={"status": "stopped", "at": self.current_location})

    def bind(self, device_id: str | None, mode: str) -> "MockRobotExecutor":
        return self  # 가짜 실행기는 temi 한 대를 흉내 내므로 그대로

    async def aclose(self) -> None:
        return None
