"""시험용 가짜 temi: 서버 /ws/temi에 붙어 명령을 받으면 로그를 찍고 ack, 2초 뒤 completed를 돌려준다.
명령은 규격대로 받은 순서대로 하나씩 실행한다(stop만 바로).

  python scripts/fake_temi.py --url ws://localhost:8000/ws/temi --key <TEMI_API_KEY> --device temi-test-1
  python scripts/fake_temi.py ... --delay 5 --fail go_to --reject start_delivery   # 실패·거부 흉내
  python scripts/fake_temi.py ... --no-ack speak                                    # ack 안 보내기 (서버 ack 시간 초과 시험)

키를 생략하면 .env의 TEMI_API_KEY를 쓴다. 연결이 끊기면 3초 뒤 다시 붙는다(--once면 종료).
규격: docs/control_protocol.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from websockets.asyncio.client import connect  # noqa: E402
from websockets.exceptions import ConnectionClosed  # noqa: E402

log = logging.getLogger("fake_temi")
LOCATIONS = ["햇살반", "별빛반", "식당", "놀이방", "현관", "교사실", "충전기"]


class FakeTemi:
    def __init__(self, url: str, key: str, device_id: str, delay_s: float = 2.0,
                 fail: set[str] | None = None, reject: set[str] | None = None, no_ack: set[str] | None = None,
                 ping_interval_s: float = 20.0):
        self.url, self.key, self.device_id = url, key, device_id
        self.delay_s = delay_s
        self.fail, self.reject, self.no_ack = fail or set(), reject or set(), no_ack or set()
        self.ping_interval_s = ping_interval_s
        self.received: list[dict] = []  # 받은 command (테스트 확인용)
        self.welcome: dict | None = None
        self.ws = None
        self.connected = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        # 규격: 명령은 받은 순서대로 하나씩 실행 (stop만 기다리지 않고 바로)
        self._queue_lock = asyncio.Lock()
        self.finished_order: list[str] = []  # 결과를 보낸 action 순서 (테스트 확인용)

    async def _send(self, msg: dict) -> None:
        await self.ws.send(json.dumps(msg, ensure_ascii=False))

    async def run_once(self) -> None:
        headers = {"X-API-Key": self.key, "X-Device-Id": self.device_id}
        async with connect(self.url, additional_headers=headers, ping_interval=None) as ws:
            self.ws = ws
            await self._send({"type": "hello", "id": uuid.uuid4().hex[:8], "device_id": self.device_id,
                              "app_version": "fake-0.1", "protocol": 1, "locations": LOCATIONS})
            pinger = asyncio.create_task(self._ping_loop())
            try:
                async for raw in ws:
                    await self._on_message(json.loads(raw))
            finally:
                pinger.cancel()
                self.connected.clear()

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(self.ping_interval_s)
            await self._send({"type": "ping", "id": uuid.uuid4().hex[:8]})

    async def _on_message(self, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype == "welcome":
            self.welcome = msg
            self.connected.set()
            log.info("연결됨: %s (서버 실행기=%s)", msg.get("device_id"), msg.get("executor"))
        elif mtype == "ping":
            await self._send({"type": "pong", "id": msg["id"]})
        elif mtype == "command":
            self.received.append(msg)
            action = msg.get("action")
            log.info("명령 받음 id=%s action=%s args=%s timeout=%ss", msg["id"], action,
                     json.dumps(msg.get("args"), ensure_ascii=False), msg.get("timeout_s"))
            if action in self.no_ack:
                log.info("  (시험) ack를 보내지 않음")
                return
            await self._send({"type": "ack", "id": msg["id"]})
            task = asyncio.create_task(self._finish(msg))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        elif mtype == "error":
            log.warning("서버 오류 메시지: %s", msg.get("message"))

    async def _finish(self, cmd: dict) -> None:
        action = cmd.get("action")
        if action == "stop":
            await self._execute(cmd)
            return
        async with self._queue_lock:  # 앞 명령이 끝난 뒤 실행
            await self._execute(cmd)

    async def _execute(self, cmd: dict) -> None:
        action = cmd.get("action")
        if action in self.reject:
            status, detail = "rejected", {"message": "지금은 할 수 없어요 (시험용 거부)"}
        else:
            await asyncio.sleep(0 if action == "stop" else self.delay_s)
            if action in self.fail:
                status, detail = "failed", {"message": "장애물 때문에 실패 (시험용)"}
            else:
                status, detail = "completed", {"message": f"{action} 완료 (가짜 temi)"}
        self.finished_order.append(action)
        try:
            await self._send({"type": "result", "id": cmd["id"], "status": status, "detail": detail})
            log.info("결과 보냄 id=%s %s", cmd["id"], status)
        except ConnectionClosed:
            log.warning("결과를 보내기 전에 연결이 끊김 id=%s", cmd["id"])

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()


async def main() -> int:
    ap = argparse.ArgumentParser(description="시험용 가짜 temi")
    ap.add_argument("--url", default="ws://localhost:8000/ws/temi")
    ap.add_argument("--key", default=None, help="temi 역할 키 (생략하면 .env의 TEMI_API_KEY)")
    ap.add_argument("--device", default="temi-test-1")
    ap.add_argument("--delay", type=float, default=2.0, help="ack 후 결과까지 초")
    ap.add_argument("--fail", default="", help="실패로 응답할 action (쉼표 구분)")
    ap.add_argument("--reject", default="", help="거부로 응답할 action")
    ap.add_argument("--no-ack", default="", help="ack를 보내지 않을 action")
    ap.add_argument("--once", action="store_true", help="끊기면 다시 붙지 않고 종료")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    key = args.key
    if key is None:
        from app.config import Settings
        secret = Settings().temi_api_key
        key = secret.get_secret_value() if secret else ""
    split = lambda s: {x.strip() for x in s.split(",") if x.strip()}  # noqa: E731
    temi = FakeTemi(args.url, key, args.device, args.delay, split(args.fail), split(args.reject), split(args.no_ack))
    while True:
        try:
            await temi.run_once()
            log.warning("서버가 연결을 닫았습니다")
        except (OSError, ConnectionClosed) as e:
            log.warning("연결 실패/끊김: %s", e)
        except Exception as e:  # handshake 거부(InvalidStatus) 등
            log.error("접속 거부: %s (키·device_id 확인)", e)
            return 1
        if args.once:
            return 0
        await asyncio.sleep(3)


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
