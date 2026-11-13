"""기록 조회·초안 도구 (교사 모드 전용): get_events, draft_parent_message, draft_incident_report."""

from __future__ import annotations

import re

from app.agent.registry import TEACHER, Tool, ToolContext, ToolRegistry, ToolResult

TEACHER_ONLY = frozenset({TEACHER})
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


async def _get_events(args: dict, ctx: ToolContext) -> ToolResult:
    date = args["date"].strip()
    if date in ("today", "오늘"):
        date = ctx.services["now"]().date().isoformat()
    if not _DATE.match(date):
        return ToolResult(ok=False, error="date는 YYYY-MM-DD 형식이어야 합니다")
    repo = ctx.services["repo"]
    events = repo.events_by_date(date)
    incidents: dict[str, dict] = {}
    for e in events:
        inc = incidents.setdefault(e["incident_id"], {"incident_id": e["incident_id"], "type": e["type"], "events": []})
        inc["events"].append({k: e[k] for k in ("event_id", "phase", "timestamp", "location", "confidence",
                                                 "source", "resolved_by") if e.get(k) is not None})
    for inc in incidents.values():
        rep = repo.latest_report(inc["incident_id"])
        inc["report"] = {"report_id": rep["id"], "version": rep["version"], "status": rep["status"]} if rep else None
    return ToolResult(ok=True, data={"date": date, "incidents": list(incidents.values())})


async def _draft_parent_message(args: dict, ctx: ToolContext) -> ToolResult:
    child = args["child"].strip()
    if ctx.data.find_child(child) is None:
        return ToolResult(ok=False, error=f"등록되지 않은 아이입니다: {child}")
    a = ctx.services["approvals"].create_draft(child=child, text=args["text"], source="agent")
    return ToolResult(ok=True, data={"approval_id": a["id"], "status": a["status"],
                                     "note": "승인 대기로 저장했습니다. 교사가 승인해야 부모에게 전송됩니다."})


async def _draft_incident_report(args: dict, ctx: ToolContext) -> ToolResult:
    incidents = ctx.services["incidents"]
    incident_id = incidents.resolve_incident_id(args["incident_id"].strip())
    if incident_id is None:
        return ToolResult(ok=False, error=f"사건을 찾지 못했습니다: {args['incident_id']}. get_events로 먼저 확인하세요.")
    rep = incidents.draft_report(incident_id, created_by="teacher_request")
    return ToolResult(ok=True, data={"report_id": rep["id"], "incident_id": incident_id, "version": rep["version"],
                                     "status": rep["status"], "content": rep["content"]})


def register_draft_tools(reg: ToolRegistry) -> None:
    reg.register(Tool(
        name="get_events",
        description="그날의 현장 이벤트 기록(넘어짐 감지·알림·해제)을 사건별로 조회한다. 보고서 초안이 있으면 함께 알려 준다.",
        input_schema={
            "type": "object",
            "properties": {"date": {"type": "string", "description": "YYYY-MM-DD 또는 '오늘'"}},
            "required": ["date"],
            "additionalProperties": False,
        },
        allowed_modes=TEACHER_ONLY,
        handler=_get_events,
    ))
    reg.register(Tool(
        name="draft_parent_message",
        description="부모에게 보낼 메시지 초안을 만든다. 바로 전송되지 않고 교사 승인 대기로 저장된다.",
        input_schema={
            "type": "object",
            "properties": {
                "child": {"type": "string", "minLength": 1, "description": "아이 이름"},
                "text": {"type": "string", "minLength": 1, "description": "부모에게 보낼 문장"},
            },
            "required": ["child", "text"],
            "additionalProperties": False,
        },
        allowed_modes=TEACHER_ONLY,
        handler=_draft_parent_message,
    ))
    reg.register(Tool(
        name="draft_incident_report",
        description="사건의 사고 보고서 초안을 (다시) 만든다. incident_id 또는 그 사건의 event_id를 넣는다. 이전 초안은 이전 버전으로 남는다.",
        input_schema={
            "type": "object",
            "properties": {"incident_id": {"type": "string", "minLength": 1, "description": "incident_id 또는 event_id"}},
            "required": ["incident_id"],
            "additionalProperties": False,
        },
        allowed_modes=TEACHER_ONLY,
        handler=_draft_incident_report,
    ))

