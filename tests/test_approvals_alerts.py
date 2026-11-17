"""승인 흐름(승인 전 미전송, 승인 후 전송, 거절)과 교사 알림 API."""

import re
from pathlib import Path

import pytest

from app.services import SendError

from .conftest import TEACHER_KEY, TEMI_KEY, call, text_reply, tools_reply

T = {"X-API-Key": TEACHER_KEY}


def draft_via_agent(client, teacher_llm, child="가온", text="오늘 가온이가 그림을 멋지게 그렸어요."):
    teacher_llm.script += [tools_reply(call("draft_parent_message", child=child, text=text)),
                           text_reply("부모님께 보낼 메시지를 승인 대기에 올렸습니다.")]
    r = client.post("/agent/teacher", json={"instruction": "가온이 부모님께 칭찬 메시지 써 줘"}, headers=T).json()
    tool = next(s for s in r["trace"] if s["type"] == "tool")
    return tool


def test_agent_draft_is_pending_not_sent(client, teacher_llm, sender, repo):
    tool = draft_via_agent(client, teacher_llm)
    assert tool["ok"] and tool["result"]["status"] == "pending"
    assert sender.sent == []
    pending = client.get("/approvals", headers=T).json()
    assert len(pending) == 1 and pending[0]["child"] == "가온" and pending[0]["source"] == "agent"


def test_approve_sends_once(client, teacher_llm, sender):
    aid = draft_via_agent(client, teacher_llm)["result"]["approval_id"]
    r = client.post(f"/approvals/{aid}/approve", json={"decided_by": "햇살반 담임"}, headers=T)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "sent" and body["sent_at"] and body["decided_by"] == "햇살반 담임"
    assert [s["id"] for s in sender.sent] == [aid]
    # 두 번 승인해도 다시 전송되지 않는다
    assert client.post(f"/approvals/{aid}/approve", headers=T).status_code == 409
    assert len(sender.sent) == 1


def test_reject_records_reason_and_never_sends(client, teacher_llm, sender):
    aid = draft_via_agent(client, teacher_llm)["result"]["approval_id"]
    r = client.post(f"/approvals/{aid}/reject", json={"reason": "표현 수정 필요"}, headers=T).json()
    assert r["status"] == "rejected" and r["reject_reason"] == "표현 수정 필요"
    assert client.post(f"/approvals/{aid}/approve", headers=T).status_code == 409
    assert sender.sent == []


def test_approve_requires_child_and_allows_edit(client, repo, sender, app):
    a = app.state.services["approvals"].create_draft(child=None, text="11:52 넘어짐 감지", source="incident_auto",
                                                      incident_id="inc-1")
    assert client.post(f"/approvals/{a['id']}/approve", headers=T).status_code == 400
    assert client.post(f"/approvals/{a['id']}/approve", json={"child": "없는아이"}, headers=T).status_code == 400
    assert sender.sent == []
    r = client.post(f"/approvals/{a['id']}/approve",
                    json={"child": "나래", "text": "11:52 놀이방에서 넘어졌고 선생님이 바로 확인했습니다."}, headers=T).json()
    assert r["status"] == "sent" and r["child"] == "나래" and r["text"].startswith("11:52 놀이방")
    assert sender.sent[0]["child"] == "나래" and sender.sent[0]["text"] == r["text"]


def test_draft_tool_rejects_unknown_child(client, teacher_llm, repo):
    tool = draft_via_agent(client, teacher_llm, child="홍길동")
    assert not tool["ok"] and repo.list_approvals() == []


def test_send_failure_recorded(client, teacher_llm, sender):
    async def boom(approval):
        raise SendError("부모 앱 연결 실패")
    sender.send = boom
    aid = draft_via_agent(client, teacher_llm)["result"]["approval_id"]
    r = client.post(f"/approvals/{aid}/approve", headers=T).json()
    assert r["status"] == "send_failed" and r["send_error"] == "부모 앱 연결 실패"


def test_approval_404_and_list_filters(client, teacher_llm):
    assert client.post("/approvals/999/approve", headers=T).status_code == 404
    aid = draft_via_agent(client, teacher_llm)["result"]["approval_id"]
    client.post(f"/approvals/{aid}/reject", json={"reason": "x"}, headers=T)
    assert client.get("/approvals", headers=T).json() == []
    assert len(client.get("/approvals", params={"status": "rejected"}, headers=T).json()) == 1
    assert len(client.get("/approvals", params={"status": "all"}, headers=T).json()) == 1


def test_only_approval_service_calls_sender():
    """승인 없이 전송되는 경로가 없는지: MessageSender.send 호출은 ApprovalService.approve 안에만 있어야 한다."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    callers = []
    for f in app_dir.rglob("*.py"):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"sender\.send\(", line):
                callers.append(f"{f.relative_to(app_dir).as_posix()}:{i}")
    assert len(callers) == 1 and callers[0].startswith("services/approvals.py"), callers


# --- 교사 알림 API ---

def child_say(client, text):
    return client.post("/agent/child", json={"text": text, "device_id": "temi-1"},
                       headers={"X-API-Key": TEMI_KEY}).json()


def test_alerts_list_urgent_first_and_ack(client, child_llm):
    child_llm.script.append(text_reply("무서웠구나."))
    child_say(client, "천둥 무서워")
    urgent = child_say(client, "친구가 때렸어")
    alerts = client.get("/alerts", headers=T).json()
    assert [a["level"] for a in alerts] == ["urgent", "normal"]
    assert alerts[0]["utterance"] == "친구가 때렸어" and alerts[0]["matched"].startswith("urgent:violence:")

    r = client.post(f"/alerts/{urgent['alert_id']}/ack", json={"by": "햇살반 담임"}, headers=T).json()
    assert r["acknowledged_at"] and r["acknowledged_by"] == "햇살반 담임"
    unacked = client.get("/alerts", params={"unacked_only": True}, headers=T).json()
    assert [a["level"] for a in unacked] == ["normal"]
    assert client.get("/alerts", params={"level": "urgent"}, headers=T).json()[0]["id"] == urgent["alert_id"]
    assert client.post("/alerts/999/ack", headers=T).status_code == 404


@pytest.mark.parametrize("path", ["/alerts", "/approvals"])
def test_teacher_only_lists(client, path):
    assert client.get(path, headers={"X-API-Key": TEMI_KEY}).status_code == 403
    assert client.get(path).status_code == 401


def test_draft_and_approve_normalize_calling_name(client, teacher_llm, repo):
    tool = draft_via_agent(client, teacher_llm, child="다솜이")
    assert tool["ok"]
    assert repo.get_approval(tool["result"]["approval_id"])["child"] == "다솜"
