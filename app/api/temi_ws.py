"""/ws/temi: temi가 서버로 여는 명령 채널 (docs/control_protocol.md).

- 인증: 접속 요청 헤더 X-API-Key(temi 역할 키), X-Device-Id(장치 ID). 틀리면 접속(handshake) 단계에서 거부(HTTP 403).
- 같은 device_id가 다시 붙으면 이전 연결을 닫고(4000) 새 연결로 바꾼다.
- 메시지는 JSON 텍스트, 모두 type과 id를 가진다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.control.hub import PROTOCOL_VERSION

from .auth import role_for_key

log = logging.getLogger(__name__)
router = APIRouter()
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@router.websocket("/ws/temi")
async def temi_ws(ws: WebSocket) -> None:
    st = ws.app.state
    settings = st.settings
    device_id = ws.headers.get("x-device-id", "")
    if settings.auth_enabled:
        key = ws.headers.get("x-api-key", "")
        if not key or role_for_key(settings, key) != "temi":
            log.warning("/ws/temi 인증 실패 (device=%s)", device_id[:64])
            await ws.close(code=1008)  # accept 전에 닫으면 handshake가 403으로 거부됨
            return
    if not DEVICE_ID_RE.match(device_id):
        await ws.close(code=1008)
        return

    await ws.accept()
    hub = st.temi_hub
    send_lock = asyncio.Lock()

    async def send(msg: dict) -> None:
        async with send_lock:
            await ws.send_text(json.dumps(msg, ensure_ascii=False))

    async def close(code: int, reason: str) -> None:
        await ws.close(code=code, reason=reason)

    conn = await hub.connect(device_id, send, close)
    await send({"type": "welcome", "id": uuid.uuid4().hex[:8], "device_id": device_id,
                "protocol": PROTOCOL_VERSION, "ping_interval_s": hub.ping_interval_s,
                "executor": settings.robot_executor, "server_time": st.services["now"]().isoformat(timespec="seconds")})
    ping_task = asyncio.create_task(hub.ping_loop(conn))
    try:
        while True:
            text = await ws.receive_text()
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                await send({"type": "error", "id": "", "message": "JSON이 아닙니다"})
                continue
            await hub.handle_message(conn, msg)
    except WebSocketDisconnect:
        pass
    except RuntimeError:  # 교체·종료로 이미 닫힌 소켓에서 읽으려 할 때
        pass
    finally:
        ping_task.cancel()
        await hub.disconnect(conn)
