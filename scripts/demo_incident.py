"""시나리오 재현: 넘어짐 감지 → 교사 알림 → "테미, 괜찮아" 해제 → 보고서 초안·부모 알림 승인 대기 → 교사 승인 → 전송 스텁.

  python scripts/demo_incident.py

메모리 DB, 데모용 키를 쓰므로 .env와 무관하게 돌아간다. LLM은 쓰지 않는다(보고서는 규칙 기반 템플릿).
"""

from __future__ import annotations

import _demo_common as dc  # noqa: F401  (경로·인코딩 설정)
from _demo_common import demo_settings, h
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import LogMessageSender

INC = "inc-20261113-115203"
EVENTS = [
    {"event_id": "evt-001", "incident_id": INC, "type": "fall", "phase": "detected",
     "timestamp": "2026-11-13T11:52:03+09:00", "location": "놀이방", "confidence": 0.91,
     "source": "mediapipe_pose:cam-playroom"},
    {"event_id": "evt-002", "incident_id": INC, "type": "fall", "phase": "alerted",
     "timestamp": "2026-11-13T11:52:05+09:00", "alert_channels": ["teacher_app", "temi_speaker"]},
    {"event_id": "evt-003", "incident_id": INC, "type": "fall", "phase": "resolved",
     "timestamp": "2026-11-13T11:53:40+09:00", "resolved_at": "2026-11-13T11:53:40+09:00",
     "resolved_by": "햇살반 담임", "resolution_method": "voice"},
]


def main() -> None:
    sender = LogMessageSender()
    with TestClient(create_app(demo_settings(), sender=sender)) as c:
        print("① 규칙 엔진 → POST /events")
        last = None
        for ev in EVENTS:
            r = c.post("/events", json=ev, headers=h("rule_engine"))
            last = r.json()
            print(f"   {ev['phase']:9s} → {r.status_code} {last}")

        print("\n   (같은 해제 이벤트 재전송)")
        r = c.post("/events", json=EVENTS[-1], headers=h("rule_engine"))
        print(f"   resolved  → {r.status_code} duplicate={r.json()['duplicate']}")

        print("\n② 자동 생성된 사고 보고서 초안  GET /reports/{id}")
        rep = c.get(f"/reports/{last['report_id']}", headers=h("teacher_app")).json()
        print(f"   status={rep['status']} version={rep['version']} created_by={rep['created_by']}")
        print("   " + rep["content"].replace("\n", "\n   "))

        print("\n③ 승인 대기 목록  GET /approvals")
        for a in c.get("/approvals", headers=h("teacher_app")).json():
            print(f"   #{a['id']} status={a['status']} child={a['child']} source={a['source']}")
            print(f"      \"{a['text']}\"")
        print(f"   전송된 메시지 수: {len(sender.sent)}")

        aid = last["approval_id"]
        print(f"\n④ 아이 지정 없이 승인 시도  POST /approvals/{aid}/approve")
        r = c.post(f"/approvals/{aid}/approve", headers=h("teacher_app"))
        print(f"   → {r.status_code} {r.json()['detail']}")

        print(f"\n⑤ 교사가 아이를 지정하고 승인  POST /approvals/{aid}/approve")
        r = c.post(f"/approvals/{aid}/approve", headers=h("teacher_app"),
                   json={"child": "나래", "decided_by": "햇살반 담임"})
        a = r.json()
        print(f"   → {r.status_code} status={a['status']} child={a['child']} sent_at={a['sent_at']}")

        print("\n⑥ MessageSender(스텁) 호출 기록")
        for s in sender.sent:
            print(f"   approval_id={s['id']} child={s['child']} text=\"{s['text']}\"")


if __name__ == "__main__":
    main()
