"""이벤트 저장과 사고 보고서 초안.

보고서에는 이벤트 기록에 있는 사실만 쓴다. 기록에 없는 내용(대상 아이, 다친 정도, 원인, 조치)은
지어내지 않고 "[교사 확인 필요]"로 남긴다. LLM을 쓰지 않는 규칙 기반 템플릿이다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.storage import Repository, Row

from .approvals import ApprovalService

NEEDS_CHECK = "[교사 확인 필요]"
TYPE_LABELS = {"fall": "넘어짐"}
PHASE_LABELS = {"detected": "감지", "alerted": "교사 알림", "resolved": "교사 확인(해제)"}
METHOD_LABELS = {"voice": "음성 해제", "app": "앱에서 해제", "timeout": "시간 초과 자동 해제"}


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _fmt(ts: str | None, with_date: bool = True) -> str | None:
    dt = _parse(ts)
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S" if with_date else "%H:%M:%S")


def _hm(ts: str | None) -> str | None:
    dt = _parse(ts)
    return dt.strftime("%H:%M") if dt else None


def build_report(incident_id: str, events: list[Row]) -> tuple[dict[str, Any], str]:
    by_phase: dict[str, Row] = {}
    for e in events:
        by_phase.setdefault(e["phase"], e)  # 단계마다 가장 이른 기록
    det, alr, res = by_phase.get("detected"), by_phase.get("alerted"), by_phase.get("resolved")

    type_ = next((e["type"] for e in events), None)
    location = next((e["location"] for e in events if e.get("location")), None)
    child = next((e["child"] for e in events if e.get("child")), None)

    detection = None
    if det and det.get("source"):
        detection = det["source"] + (f" (신뢰도 {det['confidence']:.2f})" if det.get("confidence") is not None else "")

    alerted = None
    if alr:
        ch = alr.get("alert_channels") or []
        alerted = _fmt(alr["timestamp"], with_date=False) + (f" ({', '.join(ch)})" if ch else "")

    resolved = None
    if res:
        resolved_at = res.get("resolved_at") or res["timestamp"]
        bits = [_fmt(resolved_at, with_date=False)]
        if res.get("resolved_by"):
            bits.append(res["resolved_by"])
        if res.get("resolution_method"):
            bits.append(METHOD_LABELS.get(res["resolution_method"], res["resolution_method"]))
        resolved = ", ".join(b for b in bits if b)

    timeline = [f"{_fmt(e['timestamp'], with_date=False)} {PHASE_LABELS.get(e['phase'], e['phase'])}" for e in events]

    fields: dict[str, Any] = {
        "incident_id": incident_id,
        "type": TYPE_LABELS.get(type_, type_) if type_ else NEEDS_CHECK,
        "occurred_at": _fmt(det["timestamp"]) if det else NEEDS_CHECK,
        "location": location or NEEDS_CHECK,
        "detection": detection or NEEDS_CHECK,
        "teacher_alerted": alerted or NEEDS_CHECK,
        "teacher_confirmed": resolved or NEEDS_CHECK,
        "timeline": timeline,
        "child": child or NEEDS_CHECK,
        "injury": NEEDS_CHECK,
        "cause": NEEDS_CHECK,
        "action_taken": NEEDS_CHECK,
    }
    content = "\n".join([
        f"사고 보고서 (초안) — {incident_id}",
        f"- 사건 종류: {fields['type']}",
        f"- 발생 시각: {fields['occurred_at']}",
        f"- 장소: {fields['location']}",
        f"- 감지 방법: {fields['detection']}",
        f"- 교사 알림: {fields['teacher_alerted']}",
        f"- 교사 확인(해제): {fields['teacher_confirmed']}",
        f"- 대응 경과: {' → '.join(timeline) if timeline else NEEDS_CHECK}",
        f"- 대상 아이: {fields['child']}",
        f"- 다친 정도: {NEEDS_CHECK}",
        f"- 원인·상황: {NEEDS_CHECK}",
        f"- 조치 내용: {NEEDS_CHECK}",
        "",
        "※ 이벤트 기록만으로 만든 초안입니다. [교사 확인 필요] 항목을 채운 뒤 사용하세요.",
    ])
    return fields, content


def build_parent_notice(events: list[Row]) -> str:
    det = next((e for e in events if e["phase"] == "detected"), None)
    res = next((e for e in events if e["phase"] == "resolved"), None)
    type_label = TYPE_LABELS.get(next((e["type"] for e in events), ""), "사건")
    location = next((e["location"] for e in events if e.get("location")), None)
    where = f"{location}에서 " if location else ""
    first = f"{_hm(det['timestamp'])} {where}{type_label}이 감지되었고" if det else f"{where}{type_label}이 감지되었고"
    confirmed_at = _hm((res.get("resolved_at") or res["timestamp"])) if res else None
    second = f"{confirmed_at} 선생님이 확인했습니다." if confirmed_at else "선생님이 확인했습니다."
    return f"{first}, {second} 자세한 내용은 선생님이 따로 안내드리겠습니다."


class IncidentService:
    def __init__(self, repo: Repository, approvals: ApprovalService):
        self.repo = repo
        self.approvals = approvals

    def record_event(self, event: Row) -> dict[str, Any]:
        """이벤트 저장. 해제(resolved)면 보고서 초안과 부모 알림 승인 대기를 만든다."""
        is_new = self.repo.add_event(event)
        out: dict[str, Any] = {"event_id": event["event_id"], "incident_id": event["incident_id"],
                               "duplicate": not is_new, "report_id": None, "approval_id": None}
        if not is_new or event["phase"] != "resolved":
            return out
        report = self.draft_report(event["incident_id"], created_by="auto_on_resolve")
        out["report_id"] = report["id"]
        # 같은 사건으로 부모 알림을 두 번 만들지 않는다
        existing = self.repo.approval_for_incident(event["incident_id"])
        if existing is None:
            events = self.repo.events_by_incident(event["incident_id"])
            child = next((e["child"] for e in events if e.get("child")), None)
            existing = self.approvals.create_draft(child=child, text=build_parent_notice(events),
                                                   source="incident_auto", incident_id=event["incident_id"])
        out["approval_id"] = existing["id"]
        return out

    def resolve_incident_id(self, id_: str) -> str | None:
        """incident_id 또는 event_id를 받아 incident_id를 돌려준다."""
        if self.repo.events_by_incident(id_):
            return id_
        ev = self.repo.get_event(id_)
        return ev["incident_id"] if ev else None

    def draft_report(self, incident_id: str, created_by: str) -> Row:
        events = self.repo.events_by_incident(incident_id)
        fields, content = build_report(incident_id, events)
        return self.repo.add_report(incident_id, content, fields, created_by)
