"""FastAPI 앱 조립. `uvicorn app.main:create_app --factory` 로 실행한다."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.auth import check_auth_config
from app.api.routes import router
from app.config import Settings, get_settings, validate_child_llm_host
from app.data import StaticData
from app.executors import ControlExecutor, MockRobotExecutor, RobotExecutor
from app.llm import ChatLLM, OpenAICompatClient
from app.tools import build_registry


def build_executor(settings: Settings, data: StaticData) -> RobotExecutor:
    if settings.robot_executor == "control":
        return ControlExecutor(settings.control_ws_url)
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
) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # 요청마다 URL을 찍지 않게

    # 시작 시점 검사: 잘못된 설정이면 서버를 띄우지 않는다
    validate_child_llm_host(settings)
    check_auth_config(settings)

    data = StaticData.load()
    owned: list = []
    if teacher_llm is None:
        teacher_llm = OpenAICompatClient(settings.teacher_llm)
        owned.append(teacher_llm)
    if child_llm is None:
        child_llm = OpenAICompatClient(settings.child_llm)
        owned.append(child_llm)
    executor = executor or build_executor(settings, data)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        for c in owned:
            await c.aclose()
        await executor.aclose()

    app = FastAPI(title="temi 에이전트 서버", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.data = data
    app.state.executor = executor
    app.state.registry = build_registry(data)
    app.state.teacher_llm = teacher_llm
    app.state.child_llm = child_llm
    app.state.services = {}  # 2단계: 저장소, MessageSender
    app.include_router(router)
    return app
