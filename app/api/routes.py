from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from app.agent.loop import AgentRunner
from app.agent.prompts import TEACHER_SYSTEM_PROMPT
from app.agent.registry import TEACHER, ToolContext

from .auth import require_role
from .schemas import AgentResponse, HealthResponse, ModelInfo, TeacherRequest

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/agent/teacher", response_model=AgentResponse, dependencies=[Depends(require_role("teacher_app"))])
async def agent_teacher(req: TeacherRequest, request: Request) -> AgentResponse:
    st = request.app.state
    ctx = ToolContext(executor=st.executor, data=st.data, mode=TEACHER, services=st.services)
    runner = AgentRunner(
        llm=st.teacher_llm,
        registry=st.registry,
        ctx=ctx,
        system_prompt=TEACHER_SYSTEM_PROMPT,
        max_steps=st.settings.agent_max_steps,
    )
    result = await runner.run(req.instruction)
    log.info("teacher agent: status=%s steps=%d model=%s", result.status, result.steps, result.model)
    return AgentResponse(
        status=result.status, answer=result.answer, question=result.question,
        steps=result.steps, model=result.model, trace=result.trace,
    )


@router.get("/health", response_model=HealthResponse)
async def health(request: Request, check_llm: bool = False) -> HealthResponse:
    st = request.app.state
    models: dict[str, ModelInfo] = {}
    for mode, cfg, client in (("teacher", st.settings.teacher_llm, st.teacher_llm),
                              ("child", st.settings.child_llm, st.child_llm)):
        reachable = None
        if check_llm and hasattr(client, "ping"):
            reachable = (await client.ping()).get("ok", False)
        models[mode] = ModelInfo(model=cfg.model, host=cfg.host, tool_mode=cfg.tool_mode,
                                 reasoning_effort=cfg.reasoning_effort, reachable=reachable)
    return HealthResponse(status="ok", executor=st.executor.name, models=models)
