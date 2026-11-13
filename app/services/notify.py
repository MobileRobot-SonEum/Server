"""외부로 나가는 전송 인터페이스. 지금은 로그만 남기는 스텁.

- MessageSender: 부모에게 메시지 전송. **ApprovalService.approve()에서만** 호출한다.
- TeacherNotifier: 아이 대화에서 생긴 **긴급** 교사 알림을 바로 전송. (넘어짐 등 현장 응급 알림은 규칙 엔진 담당)
"""

from __future__ import annotations

import logging
from typing import Protocol

from app.storage import Row

log = logging.getLogger(__name__)


class SendError(Exception):
    pass


class MessageSender(Protocol):
    async def send(self, approval: Row) -> None: ...  # 실패하면 SendError


class TeacherNotifier(Protocol):
    async def notify_urgent(self, alert: Row) -> None: ...


class LogMessageSender:
    """부모 앱 규격이 정해지면 실제 전송으로 바꾼다."""

    def __init__(self) -> None:
        self.sent: list[Row] = []

    async def send(self, approval: Row) -> None:
        self.sent.append(dict(approval))
        log.info("[전송 스텁] 부모 메시지 approval_id=%s child=%s 길이=%d",
                 approval["id"], approval["child"], len(approval["text"]))


class LogTeacherNotifier:
    """교사 앱 푸시 규격이 정해지면 실제 전송으로 바꾼다.

    로그에는 아이 발화 원문을 남기지 않는다(원문은 teacher_alerts에만 있음).
    실제 구현에서는 원문을 교사 앱으로 보내도 되지만, 로그에는 여전히 쓰지 않는다.
    """

    def __init__(self) -> None:
        self.notified: list[int] = []

    async def notify_urgent(self, alert: Row) -> None:
        self.notified.append(alert["id"])
        log.warning("[알림 스텁] 긴급 교사 알림 alert_id=%s category=%s device=%s",
                    alert["id"], alert["category"], alert.get("device_id"))
