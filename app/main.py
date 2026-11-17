"""FastAPI 앱 조립. `uvicorn app.main:create_app --factory` 로 실행한다."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI

from app.api.auth import check_auth_config
from app.api.records import router as records_router
from app.api.temi_ws import router as temi_ws_router
from app.api.routes import router
from app.config import Settings, get_settings, validate_child_llm_host
from app.control import TemiHub
from app.data import StaticData
from app.executors import ControlExecutor, MockRobotExecutor, RobotExecutor
from app.llm import ChatLLM, OpenAICompatClient
from app.llm.warmup import warmup
from app.safety import ChildConversationMemory, SafetyRules
from app.safety.teacher_filter import TeacherEmergencyRules
from app.services import (ApprovalService, IncidentService, LogMessageSender, LogTeacherNotifier, MessageSender,
                          TeacherNotifier)
from app.storage import SQLiteRepository
from app.tools import build_registry


def build_executor(settings: Settings, data: StaticData, hub: TemiHub) -> RobotExecutor:
    if settings.robot_executor == "control":
        return ControlExecutor(hub, data.locations)
    return MockRobotExecutor(
        locations=data.locations,
        games=data.games,
        move_delay_s=settings.mock_move_delay_s,
        failure_rate=settings.mock_failure_rate,
        fail_tools=settings.mock_fail_tools,
        seed=settings.mock_seed,
    )


def create_app(
    settings: Settings | None = None,
    teacher_llm: ChatLLM | None = None,
    child_llm: ChatLLM | None = None,
    executor: RobotExecutor | None = None,
    sender: MessageSender | None = None,
    notifier: TeacherNotifier | None = None,
    memory: ChildConversationMemory | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # 요청마다 URL을 찍지 않게

    # 시작 시점 검사: 잘못된 설정이면 서버를 띄우지 않는다
    validate_child_llm_host(settings)
    check_auth_config(settings)

    tz = ZoneInfo(settings.timezone)

    def now() -> datetime:
        return datetime.now(tz)

    data = StaticData.load()
    rules = SafetyRules.load(settings.child_safety_rules_path or None)
    teacher_emergency = TeacherEmergencyRules.load(settings.teacher_emergency_rules_path or None)
    owned: list = []
    if teacher_llm is None:
        teacher_llm = OpenAICompatClient(settings.teacher_llm)
        owned.append(teacher_llm)
    if child_llm is None:
        child_llm = OpenAICompatClient(settings.child_llm)
        owned.append(child_llm)
    repo = SQLiteRepository(settings.db_path, now)
    # temi 명령 채널은 실행기 종류와 상관없이 열어 둔다 (mock일 때는 명령을 보내지 않음)
    hub = TemiHub(repo, now, ack_timeout_s=settings.control_ack_timeout_s,
                  ping_interval_s=settings.control_ping_interval_s, result_grace_s=settings.control_result_grace_s,
                  default_device_id=settings.temi_default_device_id, known_locations=data.locations)
    executor = executor or build_executor(settings, data, hub)
    sender = sender or LogMessageSender()
    notifier = notifier or LogTeacherNotifier()
    approvals = ApprovalService(repo, sender, data, now)
    incidents = IncidentService(repo, approvals)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 자체 서빙 모델을 미리 GPU에 올린다 (요청을 막지 않게 백그라운드). 테스트처럼 LLM을 주입한 경우는 생략
        warm_task = asyncio.create_task(warmup(settings)) if (settings.llm_warmup_on_start and owned) else None
        yield
        if warm_task and not warm_task.done():
            warm_task.cancel()
        await hub.shutdown()
        for c in owned:
            await c.aclose()
        await executor.aclose()
        repo.close()

    app = FastAPI(title="temi 에이전트 서버", version="0.2.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.data = data
    app.state.executor = executor
    app.state.temi_hub = hub
    app.state.registry = build_registry(data)
    app.state.teacher_llm = teacher_llm
    app.state.child_llm = child_llm
    app.state.safety_rules = rules
    app.state.teacher_emergency_rules = teacher_emergency
    app.state.child_memory = memory or ChildConversationMemory(settings.child_memory_turns,
                                                              settings.child_memory_ttl_s)
    app.state.services = {
        "repo": repo,
        "approvals": approvals,
        "incidents": incidents,
        "sender": sender,
        "notifier": notifier,
        "now": now,
    }
    app.include_router(router)
    app.include_router(records_router)
    app.include_router(temi_ws_router)
    return app
