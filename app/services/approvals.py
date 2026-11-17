"""부모 메시지 승인 흐름. 부모에게 가는 메시지는 이 서비스의 approve()를 거쳐야만 전송된다."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from app.data import StaticData
from app.storage import Repository, Row

from .notify import MessageSender, SendError


class ApprovalError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class ApprovalService:
    def __init__(self, repo: Repository, sender: MessageSender, data: StaticData, now: Callable[[], datetime]):
        self.repo = repo
        self._sender = sender
        self.data = data
        self._now = now

    def _ts(self) -> str:
        return self._now().isoformat(timespec="seconds")

    def create_draft(self, child: str | None, text: str, source: str, incident_id: str | None = None) -> Row:
        """승인 대기로만 저장한다. 여기서는 절대 전송하지 않는다."""
        return self.repo.add_approval(child=child, text=text.strip(), source=source, incident_id=incident_id)

    def _pending(self, approval_id: int) -> Row:
        a = self.repo.get_approval(approval_id)
        if a is None:
            raise ApprovalError(404, f"승인 요청 {approval_id}이(가) 없습니다")
        if a["status"] != "pending":
            raise ApprovalError(409, f"이미 처리된 요청입니다 (상태: {a['status']})")
        return a

    async def approve(self, approval_id: int, child: str | None = None, text: str | None = None,
                      decided_by: str | None = None) -> Row:
        a = self._pending(approval_id)
        child = (child or a["child"] or "").strip() or None
        text = (text or a["text"]).strip()
        if not child:
            raise ApprovalError(400, "받는 아이(child)가 정해지지 않았습니다. 승인할 때 child를 지정하세요.")
        found = self.data.find_child(child)
        if found is None:
            raise ApprovalError(400, f"등록되지 않은 아이입니다: {child}")
        child = found["name"]  # 다솜이 → 다솜
        if not text:
            raise ApprovalError(400, "메시지 내용이 비어 있습니다")

        a = self.repo.update_approval(approval_id, child=child, text=text, status="approved",
                                      decided_at=self._ts(), decided_by=decided_by)
        try:
            await self._sender.send(a)
        except SendError as e:
            return self.repo.update_approval(approval_id, status="send_failed", send_error=str(e))  # type: ignore[return-value]
        return self.repo.update_approval(approval_id, status="sent", sent_at=self._ts())  # type: ignore[return-value]

    def reject(self, approval_id: int, reason: str, decided_by: str | None = None) -> Row:
        self._pending(approval_id)
        if not reason.strip():
            raise ApprovalError(400, "거절 사유가 필요합니다")
        return self.repo.update_approval(approval_id, status="rejected", reject_reason=reason.strip(),  # type: ignore[return-value]
                                         decided_at=self._ts(), decided_by=decided_by)
