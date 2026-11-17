"""temi 명령 채널 허브.

temi가 서버로 WebSocket(/ws/temi)을 열고, 서버는 그 연결로 명령을 보낸다(temi는 NAT 뒤라 서버가 먼저 접속할 수 없음).
전송 방식과 분리해 두었다: 연결은 send/close 함수만 넘겨 등록하고, 받은 메시지는 handle_message로 넣는다.

명령 흐름 (docs/control_protocol.md)
  서버 → temi : {"type": "command", "id", "action", "args", "timeout_s"}
  temi → 서버 : {"type": "ack", "id"}                      (받자마자)
  temi → 서버 : {"type": "result", "id", "status", "detail"} (완료·실패·거부)
send_command는 ack까지만 기다리고(기본 5초) 바로 돌아간다. 완료 결과는 나중에 robot_jobs에 기록된다.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

from app.storage import Repository, Row

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
ACTIONS = ("go_to", "speak", "start_game", "start_delivery", "stop")
# 동작별 완료 대기 상한(초). temi에도 command.timeout_s로 알려 준다.
ACTION_TIMEOUTS: dict[str, float] = {
    "go_to": 120.0, "speak": 30.0, "start_game": 30.0, "start_delivery": 600.0, "stop": 10.0,
}
RESULT_STATUSES = ("completed", "failed", "rejected")
OPEN_STATUSES = ("sent", "acked")

Sender = Callable[[dict], Awaitable[None]]
Closer = Callable[[int, str], Awaitable[None]]


class TemiNotConnected(Exception):
    pass


class TemiAmbiguous(Exception):
    """temi가 여러 대 연결돼 있는데 어느 것에 보낼지 정해지지 않음."""


class AckTimeout(Exception):
    pass


@dataclass
class TemiConnection:
    device_id: str
    send: Sender
    close: Closer
    connected_at: str
    last_seen: float = field(default_factory=time.monotonic)
    info: dict[str, Any] = field(default_factory=dict)  # hello로 받은 앱 버전·위치 목록 등
    pending_acks: dict[str, asyncio.Future] = field(default_factory=dict)
    closed: bool = False


class TemiHub:
    def __init__(self, repo: Repository, now: Callable[[], datetime], *, ack_timeout_s: float = 5.0,
                 ping_interval_s: float = 20.0, result_grace_s: float = 10.0,
                 default_device_id: str | None = None, action_timeouts: dict[str, float] | None = None,
                 known_locations: list[str] | None = None):
        self.repo = repo
        self._now = now
        self.ack_timeout_s = ack_timeout_s
        self.ping_interval_s = ping_interval_s
        self.result_grace_s = result_grace_s
        self.default_device_id = default_device_id
        self.action_timeouts = {**ACTION_TIMEOUTS, **(action_timeouts or {})}
        self.known_locations = known_locations or []
        self._conns: dict[str, TemiConnection] = {}
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------ 연결
    async def connect(self, device_id: str, send: Sender, close: Closer) -> TemiConnection:
        old = self._conns.get(device_id)
        conn = TemiConnection(device_id=device_id, send=send, close=close,
                              connected_at=self._now().isoformat(timespec="seconds"))
        self._conns[device_id] = conn
        if old is not None:
            log.warning("temi %s 재접속: 이전 연결을 교체합니다", device_id)
            self._fail_pending(old, "새 연결로 교체됨")
            old.closed = True
            try:
                await old.close(4000, "replaced by new connection")
            except Exception:  # 이미 끊긴 연결
                pass
        else:
            log.info("temi %s 연결됨", device_id)
        return conn

    async def disconnect(self, conn: TemiConnection) -> None:
        conn.closed = True
        self._fail_pending(conn, "연결 끊김")
        if self._conns.get(conn.device_id) is not conn:
            return  # 이미 새 연결로 교체됨: 진행 중 작업은 새 연결이 이어 받을 수 있으니 건드리지 않음
        del self._conns[conn.device_id]
        for job in self.repo.open_jobs(conn.device_id):
            self.repo.update_job(job["id"], "disconnected", {"message": "temi 연결이 끊겨 결과를 받지 못했습니다"},
                                 finished=True, only_if=OPEN_STATUSES)
        log.warning("temi %s 연결 끊김", conn.device_id)

    @staticmethod
    def _fail_pending(conn: TemiConnection, reason: str) -> None:
        for fut in conn.pending_acks.values():
            if not fut.done():
                fut.set_exception(TemiNotConnected(reason))
        conn.pending_acks.clear()

    def is_connected(self, device_id: str) -> bool:
        return device_id in self._conns

    def connected_devices(self) -> list[dict[str, Any]]:
        return [{"device_id": c.device_id, "connected_at": c.connected_at,
                 "idle_s": round(time.monotonic() - c.last_seen, 1),
                 "app_version": c.info.get("app_version")} for c in self._conns.values()]

    def resolve_device(self, device_id: str | None) -> str:
        """명령을 보낼 temi를 정한다: 지정값 → 기본값(TEMI_DEFAULT_DEVICE_ID) → 연결이 하나뿐이면 그것."""
        target = device_id or self.default_device_id
        if target:
            if target not in self._conns:
                raise TemiNotConnected(f"temi '{target}'가 연결되어 있지 않습니다")
            return target
        if not self._conns:
            raise TemiNotConnected("연결된 temi가 없습니다")
        if len(self._conns) > 1:
            raise TemiAmbiguous(f"temi가 여러 대 연결돼 있습니다({', '.join(self._conns)}). TEMI_DEFAULT_DEVICE_ID를 정하세요")
        return next(iter(self._conns))

    # ------------------------------------------------------------ 명령
    async def send_command(self, device_id: str, action: str, args: dict, source_mode: str) -> Row:
        """명령을 보내고 ack까지만 기다린다. 성공하면 acked 상태의 작업 행을 돌려준다."""
        if action not in ACTIONS:
            raise ValueError(f"알 수 없는 동작: {action}")
        conn = self._conns.get(device_id)
        if conn is None:
            raise TemiNotConnected(f"temi '{device_id}'가 연결되어 있지 않습니다")
        job_id = uuid.uuid4().hex[:16]
        timeout_s = self.action_timeouts[action]
        self.repo.add_job(job_id, device_id, action, args, source_mode)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        conn.pending_acks[job_id] = fut
        try:
            try:
                await conn.send({"type": "command", "id": job_id, "action": action, "args": args, "timeout_s": timeout_s})
            except Exception as e:
                self.repo.update_job(job_id, "disconnected", {"message": f"전송 실패: {type(e).__name__}"}, finished=True)
                raise TemiNotConnected("명령 전송 실패 (연결 끊김)") from e
            try:
                await asyncio.wait_for(asyncio.shield(fut), timeout=self.ack_timeout_s)
            except asyncio.TimeoutError:
                self.repo.update_job(job_id, "ack_timeout", {"message": f"{self.ack_timeout_s:g}초 안에 ack 없음"},
                                     finished=True, only_if=("sent",))
                raise AckTimeout(f"temi가 {self.ack_timeout_s:g}초 안에 명령을 받았다고 응답하지 않았습니다") from None
            except TemiNotConnected:
                self.repo.update_job(job_id, "disconnected", {"message": "ack 전에 연결 끊김"}, finished=True,
                                     only_if=OPEN_STATUSES)
                raise
        finally:
            conn.pending_acks.pop(job_id, None)
        job = self.repo.update_job(job_id, "acked", acked=True, only_if=("sent",)) or self.repo.get_job(job_id)
        self._spawn(self._expire(job_id, timeout_s + self.result_grace_s))
        return job  # type: ignore[return-value]

    async def _expire(self, job_id: str, after_s: float) -> None:
        await asyncio.sleep(after_s)
        job = self.repo.update_job(job_id, "timeout", {"message": f"{after_s:g}초 안에 결과 없음"}, finished=True,
                                   only_if=("acked",))
        if job and job["status"] == "timeout":
            log.warning("temi 작업 %s(%s) 결과 시간 초과", job_id, job["action"])

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        for conn in list(self._conns.values()):
            try:
                await conn.close(1001, "server shutdown")
            except Exception:
                pass

    # ------------------------------------------------------------ 받은 메시지
    async def handle_message(self, conn: TemiConnection, msg: Any) -> None:
        conn.last_seen = time.monotonic()
        if not isinstance(msg, dict) or not isinstance(msg.get("type"), str) or not isinstance(msg.get("id"), str):
            await self._error(conn, None, "모든 메시지는 문자열 type과 id가 있는 JSON 객체여야 합니다")
            return
        mtype, mid = msg["type"], msg["id"]
        if mtype == "ack":
            fut = conn.pending_acks.get(mid)
            if fut is not None and not fut.done():
                fut.set_result(True)
            else:  # ack 시간 초과 뒤 늦게 온 ack: 기록만 갱신
                self.repo.update_job(mid, "acked", acked=True, only_if=("ack_timeout",))
        elif mtype == "result":
            await self._on_result(conn, mid, msg)
        elif mtype == "ping":
            await conn.send({"type": "pong", "id": mid})
        elif mtype == "pong":
            pass
        elif mtype == "hello":
            conn.info = {k: msg.get(k) for k in ("app_version", "protocol", "locations") if k in msg}
            locs = msg.get("locations")
            if isinstance(locs, list) and self.known_locations:
                missing = sorted(set(self.known_locations) - set(map(str, locs)))
                if missing:
                    log.warning("temi %s에 저장되지 않은 위치(서버 목록에는 있음): %s", conn.device_id, ", ".join(missing))
        elif mtype == "event":
            await self._error(conn, mid, "이벤트(넘어짐 등)는 이 채널로 받지 않습니다. 현장 규칙 엔진을 거쳐 POST /events로 보내세요")
        else:
            await self._error(conn, mid, f"알 수 없는 type: {mtype}")

    async def _on_result(self, conn: TemiConnection, mid: str, msg: dict) -> None:
        status = msg.get("status")
        if status not in RESULT_STATUSES:
            await self._error(conn, mid, f"result.status는 {', '.join(RESULT_STATUSES)} 중 하나여야 합니다")
            return
        job = self.repo.get_job(mid)
        if job is None or job["device_id"] != conn.device_id:
            await self._error(conn, mid, "알 수 없는 작업 id")
            return
        fut = conn.pending_acks.get(mid)
        if fut is not None and not fut.done():  # ack 없이 바로 결과가 온 경우 ack로도 처리
            fut.set_result(True)
        detail = msg.get("detail") if isinstance(msg.get("detail"), dict) else (
            {"message": str(msg["detail"])} if msg.get("detail") is not None else None)
        # 시간 초과로 표시된 뒤에 온 결과도 기록한다 (실제 결과가 더 정확)
        self.repo.update_job(mid, status, detail, finished=True, only_if=("sent", "acked", "timeout", "ack_timeout"))
        log.info("temi 작업 %s(%s) %s", mid, job["action"], status)

    @staticmethod
    async def _error(conn: TemiConnection, mid: str | None, message: str) -> None:
        try:
            await conn.send({"type": "error", "id": mid or "", "message": message})
        except Exception:
            pass

    async def ping_loop(self, conn: TemiConnection) -> None:
        """20초마다 ping. 3번 주기 동안 아무 메시지도 없으면 끊는다."""
        while not conn.closed:
            await asyncio.sleep(self.ping_interval_s)
            if conn.closed:
                return
            if time.monotonic() - conn.last_seen > 3 * self.ping_interval_s:
                log.warning("temi %s 응답 없음: 연결을 닫습니다", conn.device_id)
                try:
                    await conn.close(4001, "ping timeout")
                finally:
                    await self.disconnect(conn)
                return
            try:
                await conn.send({"type": "ping", "id": uuid.uuid4().hex[:8]})
            except Exception:
                await self.disconnect(conn)
                return
