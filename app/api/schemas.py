from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class TeacherRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000, examples=["간식 시간이야. 식당으로 가서 햇살반 간식 배부 시작해 줘"])


class AgentResponse(BaseModel):
    status: Literal["completed", "needs_teacher", "failed"]
    answer: str
    question: str | None = None
    steps: int
    model: str
    timed_out: bool = False  # 시간 초과로 failed면 true (교사 앱이 '다시 시도' 버튼에 쓸 수 있음)
    emergency_redirect: bool = False  # 응급 지시라 비상 버튼·현장 대응을 안내함 (교사 앱이 비상 기능을 띄울 수 있음)
    trace: list[dict[str, Any]]


class ModelInfo(BaseModel):
    model: str
    host: str
    tool_mode: str
    reasoning_effort: str | None
    reachable: bool | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"]
    executor: str
    models: dict[str, ModelInfo]
    temi_connected: list[str] = []  # /ws/temi에 연결된 temi device_id


# ------------------------------------------------------------------ 아이 모드
class ChildRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    device_id: str | None = Field(None, max_length=64, pattern=r"^[A-Za-z0-9._-]+$",
                                  description="temi 장치 ID (단기 대화 기억의 구분 키, 명령 채널과 같은 값)")


class ChildResponse(BaseModel):
    reply: str
    alert_created: bool
    alert_level: Literal["urgent", "normal"] | None = None
    alert_id: int | None = None
    game_started: str | None = None
    fallback_reason: str | None = None
    model: str | None = None
    latency_ms: int


# ------------------------------------------------------------------ 이벤트 (docs/events.md)
class EventIn(BaseModel):
    model_config = ConfigDict(extra="ignore")  # 모르는 필드는 무시

    event_id: str = Field(min_length=1, max_length=100)
    incident_id: str = Field(min_length=1, max_length=100)
    type: str = Field(min_length=1, max_length=40, pattern=r"^[a-z_]+$")
    phase: Literal["detected", "alerted", "resolved"]
    timestamp: AwareDatetime
    location: str | None = Field(None, max_length=100)
    confidence: float | None = Field(None, ge=0, le=1)
    source: str | None = Field(None, max_length=200)
    alert_channels: list[str] | None = None
    resolved_at: AwareDatetime | None = None
    resolved_by: str | None = Field(None, max_length=100)
    resolution_method: Literal["voice", "app", "timeout"] | None = None
    child: str | None = Field(None, max_length=50)
    note: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _resolved_fields(self):
        if self.phase == "resolved" and (self.resolved_at is None or not self.resolved_by):
            raise ValueError("phase=resolved이면 resolved_at과 resolved_by가 필요합니다")
        return self

    def to_row(self) -> dict[str, Any]:
        d = self.model_dump()
        d["timestamp"] = self.timestamp.isoformat()
        d["resolved_at"] = self.resolved_at.isoformat() if self.resolved_at else None
        return d


class EventOut(BaseModel):
    event_id: str
    incident_id: str
    duplicate: bool
    report_id: int | None = None
    approval_id: int | None = None


# ------------------------------------------------------------------ 승인·알림·보고서
class ApprovalOut(BaseModel):
    id: int
    child: str | None
    text: str
    status: str
    source: str
    incident_id: str | None
    created_at: str
    decided_at: str | None
    decided_by: str | None
    reject_reason: str | None
    sent_at: str | None
    send_error: str | None


class ApproveIn(BaseModel):
    child: str | None = Field(None, max_length=50, description="받는 아이. 비어 있는 요청이면 필수")
    text: str | None = Field(None, max_length=1000, description="고친 문구 (생략하면 원래 문구)")
    decided_by: str | None = Field(None, max_length=100)


class RejectIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    decided_by: str | None = Field(None, max_length=100)


class AlertOut(BaseModel):
    id: int
    level: str
    category: str
    matched: str
    utterance: str
    device_id: str | None
    created_at: str
    acknowledged_at: str | None
    acknowledged_by: str | None


class AckIn(BaseModel):
    by: str | None = Field(None, max_length=100)


class ReportOut(BaseModel):
    id: int
    incident_id: str
    version: int
    status: str
    content: str
    fields: dict[str, Any]
    created_by: str
    created_at: str


class RobotJobOut(BaseModel):
    id: str
    device_id: str
    action: str
    args: dict[str, Any]
    source_mode: str
    status: str  # sent | acked | completed | failed | rejected | ack_timeout | timeout | disconnected
    detail: dict[str, Any] | None
    created_at: str
    acked_at: str | None
    finished_at: str | None


class TemiDeviceOut(BaseModel):
    device_id: str
    connected_at: str
    idle_s: float
    app_version: str | None = None
