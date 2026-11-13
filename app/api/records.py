"""이벤트(rule_engine), 승인·알림·보고서(teacher_app) API."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.services import ApprovalError

from .auth import require_role
from .schemas import (AckIn, AlertOut, ApprovalOut, ApproveIn, EventIn, EventOut, RejectIn, ReportOut)

router = APIRouter()
teacher_only = [Depends(require_role("teacher_app"))]


# ------------------------------------------------------------------ events
@router.post("/events", response_model=EventOut, status_code=201,
             dependencies=[Depends(require_role("rule_engine"))])
async def post_event(ev: EventIn, request: Request, response: Response) -> EventOut:
    out = request.app.state.services["incidents"].record_event(ev.to_row())
    if out["duplicate"]:
        response.status_code = status.HTTP_200_OK
    return EventOut(**out)


# ------------------------------------------------------------------ approvals
@router.get("/approvals", response_model=list[ApprovalOut], dependencies=teacher_only)
async def list_approvals(request: Request,
                         status_: Literal["pending", "approved", "sent", "send_failed", "rejected", "all"] = Query(
                             "pending", alias="status")) -> list[ApprovalOut]:
    repo = request.app.state.services["repo"]
    return [ApprovalOut(**a) for a in repo.list_approvals(None if status_ == "all" else status_)]


def _approval_error(e: ApprovalError) -> HTTPException:
    return HTTPException(e.status_code, e.message)


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalOut, dependencies=teacher_only)
async def approve(approval_id: int, request: Request, body: ApproveIn | None = None) -> ApprovalOut:
    body = body or ApproveIn()
    try:
        a = await request.app.state.services["approvals"].approve(
            approval_id, child=body.child, text=body.text, decided_by=body.decided_by)
    except ApprovalError as e:
        raise _approval_error(e) from e
    return ApprovalOut(**a)


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalOut, dependencies=teacher_only)
async def reject(approval_id: int, body: RejectIn, request: Request) -> ApprovalOut:
    try:
        a = request.app.state.services["approvals"].reject(approval_id, body.reason, decided_by=body.decided_by)
    except ApprovalError as e:
        raise _approval_error(e) from e
    return ApprovalOut(**a)


# ------------------------------------------------------------------ alerts (아이 대화에서 생긴 교사 알림)
@router.get("/alerts", response_model=list[AlertOut], dependencies=teacher_only)
async def list_alerts(request: Request, level: Literal["urgent", "normal"] | None = None,
                      unacked_only: bool = False) -> list[AlertOut]:
    repo = request.app.state.services["repo"]
    return [AlertOut(**a) for a in repo.list_alerts(level=level, unacked_only=unacked_only)]


@router.post("/alerts/{alert_id}/ack", response_model=AlertOut, dependencies=teacher_only)
async def ack_alert(alert_id: int, request: Request, body: AckIn | None = None) -> AlertOut:
    a = request.app.state.services["repo"].ack_alert(alert_id, (body or AckIn()).by)
    if a is None:
        raise HTTPException(404, f"알림 {alert_id}이(가) 없습니다")
    return AlertOut(**a)


# ------------------------------------------------------------------ reports
@router.get("/reports", response_model=list[ReportOut], dependencies=teacher_only)
async def list_reports(request: Request, incident_id: str | None = None) -> list[ReportOut]:
    return [ReportOut(**r) for r in request.app.state.services["repo"].list_reports(incident_id)]


@router.get("/reports/{report_id}", response_model=ReportOut, dependencies=teacher_only)
async def get_report(report_id: int, request: Request) -> ReportOut:
    r = request.app.state.services["repo"].get_report(report_id)
    if r is None:
        raise HTTPException(404, f"보고서 {report_id}이(가) 없습니다")
    return ReportOut(**r)
