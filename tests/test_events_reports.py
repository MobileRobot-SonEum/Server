"""/events → 해제 시 보고서 초안 자동 생성, [교사 확인 필요], 재생성, get_events."""

from app.services import NEEDS_CHECK

from .conftest import RULE_KEY, TEACHER_KEY, call, text_reply, tools_reply

R = {"X-API-Key": RULE_KEY}
T = {"X-API-Key": TEACHER_KEY}
INC = "inc-20261113-115203"

DETECTED = {"event_id": "e1", "incident_id": INC, "type": "fall", "phase": "detected",
            "timestamp": "2026-11-13T11:52:03+09:00", "location": "놀이방", "confidence": 0.91,
            "source": "mediapipe_pose:cam-playroom"}
ALERTED = {"event_id": "e2", "incident_id": INC, "type": "fall", "phase": "alerted",
           "timestamp": "2026-11-13T11:52:05+09:00", "alert_channels": ["teacher_app", "temi_speaker"]}
RESOLVED = {"event_id": "e3", "incident_id": INC, "type": "fall", "phase": "resolved",
            "timestamp": "2026-11-13T11:53:40+09:00", "resolved_at": "2026-11-13T11:53:40+09:00",
            "resolved_by": "햇살반 담임", "resolution_method": "voice"}


def post(client, ev):
    return client.post("/events", json=ev, headers=R)


def test_full_incident_creates_draft_report_and_pending_notice(client, repo, sender):
    r1, r2 = post(client, DETECTED), post(client, ALERTED)
    assert r1.status_code == 201 and r1.json()["report_id"] is None
    assert r2.status_code == 201
    r3 = post(client, RESOLVED)
    assert r3.status_code == 201
    body = r3.json()
    assert body["report_id"] and body["approval_id"]

    report = repo.get_report(body["report_id"])
    assert report["status"] == "draft" and report["version"] == 1 and report["created_by"] == "auto_on_resolve"
    f = report["fields"]
    assert f["occurred_at"] == "2026-11-13 11:52:03"
    assert f["location"] == "놀이방"
    assert f["detection"] == "mediapipe_pose:cam-playroom (신뢰도 0.91)"
    assert f["teacher_alerted"] == "11:52:05 (teacher_app, temi_speaker)"
    assert f["teacher_confirmed"] == "11:53:40, 햇살반 담임, 음성 해제"
    assert f["timeline"] == ["11:52:03 감지", "11:52:05 교사 알림", "11:53:40 교사 확인(해제)"]
    # 기록에 없는 내용은 지어내지 않는다
    for key in ("child", "injury", "cause", "action_taken"):
        assert f[key] == NEEDS_CHECK
    assert report["content"].count(NEEDS_CHECK) >= 4

    approval = repo.get_approval(body["approval_id"])
    assert approval["status"] == "pending" and approval["child"] is None
    assert approval["source"] == "incident_auto" and approval["incident_id"] == INC
    assert approval["text"].startswith("11:52 놀이방에서 넘어짐이 감지되었고, 11:53 선생님이 확인했습니다.")
    assert sender.sent == []  # 승인 전에는 전송 안 됨


def test_duplicate_event_is_idempotent(client, repo):
    post(client, DETECTED)
    post(client, RESOLVED)
    again = post(client, RESOLVED)
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert again.json()["report_id"] is None
    assert len(repo.list_reports(INC)) == 1 and len(repo.list_approvals()) == 1


def test_resolved_without_detection_marks_unknowns(client, repo):
    body = post(client, RESOLVED).json()
    f = repo.get_report(body["report_id"])["fields"]
    assert f["occurred_at"] == NEEDS_CHECK and f["location"] == NEEDS_CHECK and f["detection"] == NEEDS_CHECK
    assert f["teacher_confirmed"].startswith("11:53:40")


def test_second_resolution_new_version_same_notice(client, repo):
    post(client, DETECTED)
    first = post(client, RESOLVED).json()
    second = post(client, {**RESOLVED, "event_id": "e4", "timestamp": "2026-11-13T11:55:00+09:00",
                           "resolved_at": "2026-11-13T11:55:00+09:00"}).json()
    assert second["approval_id"] == first["approval_id"]
    reports = repo.list_reports(INC)
    assert [(r["version"], r["status"]) for r in reports] == [(1, "superseded"), (2, "draft")]


def test_event_validation(client):
    bad_resolved = {k: v for k, v in RESOLVED.items() if k != "resolved_by"}
    assert post(client, bad_resolved).status_code == 422
    assert post(client, {**DETECTED, "timestamp": "2026-11-13T11:52:03"}).status_code == 422  # 시간대 없음
    assert post(client, {**DETECTED, "confidence": 1.5}).status_code == 422
    assert post(client, {**DETECTED, "phase": "maybe"}).status_code == 422
    assert post(client, {**DETECTED, "event_id": "e9", "future_field": 1}).status_code == 201  # 모르는 필드 무시


def test_events_role_restricted(client):
    assert client.post("/events", json=DETECTED, headers=T).status_code == 403


def test_teacher_redrafts_report_by_event_id(client, repo, teacher_llm):
    post(client, DETECTED)
    post(client, RESOLVED)
    teacher_llm.script += [tools_reply(call("draft_incident_report", incident_id="e1")),
                           text_reply("보고서 초안을 다시 만들었습니다.")]
    r = client.post("/agent/teacher", json={"instruction": "아까 넘어짐 보고서 다시 만들어 줘"}, headers=T).json()
    tool = next(s for s in r["trace"] if s["type"] == "tool")
    assert tool["ok"] and tool["result"]["version"] == 2 and tool["result"]["incident_id"] == INC
    assert repo.latest_report(INC)["created_by"] == "teacher_request"
    assert len(repo.list_approvals()) == 1  # 재생성은 부모 알림을 또 만들지 않음


def test_get_events_tool(client, teacher_llm):
    post(client, DETECTED)
    post(client, RESOLVED)
    teacher_llm.script += [tools_reply(call("get_events", date="2026-11-13")), text_reply("1건 있습니다.")]
    r = client.post("/agent/teacher", json={"instruction": "오늘 이벤트 알려 줘"}, headers=T).json()
    tool = next(s for s in r["trace"] if s["type"] == "tool")
    incidents = tool["result"]["incidents"]
    assert len(incidents) == 1 and incidents[0]["incident_id"] == INC
    assert [e["phase"] for e in incidents[0]["events"]] == ["detected", "resolved"]
    assert incidents[0]["report"]["status"] == "draft"


def test_get_events_bad_date(client, teacher_llm):
    teacher_llm.script += [tools_reply(call("get_events", date="어제")), text_reply("x")]
    r = client.post("/agent/teacher", json={"instruction": "어제 이벤트"}, headers=T).json()
    assert "YYYY-MM-DD" in next(s for s in r["trace"] if s["type"] == "tool")["error"]


def test_reports_api(client):
    post(client, DETECTED)
    body = post(client, RESOLVED).json()
    lst = client.get("/reports", params={"incident_id": INC}, headers=T).json()
    assert [r["id"] for r in lst] == [body["report_id"]]
    assert client.get(f"/reports/{body['report_id']}", headers=T).json()["status"] == "draft"
    assert client.get("/reports/999", headers=T).status_code == 404
