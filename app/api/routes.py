from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from app.agent.child import ChildAgent
from app.agent.loop import AgentRunner
from app.agent.prompts import build_teacher_prompt
from app.agent.registry import CHILD, TEACHER, ToolContext

from .auth import require_role
from .schemas import AgentResponse, ChildRequest, ChildResponse, HealthResponse, ModelInfo, TeacherRequest

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
        system_prompt=build_teacher_prompt(st.data.locations, st.data.games,
                                           today=st.services["now"]().date().isoformat()),
        max_steps=st.settings.agent_max_steps,
        deadline_s=st.settings.agent_deadline_s,
    )
    result = await runner.run(req.instruction)
    log.info("teacher agent: status=%s steps=%d model=%s", result.status, result.steps, result.model)
    return AgentResponse(
        status=result.status, answer=result.answer, question=result.question,
        steps=result.steps, model=result.model, timed_out=result.timed_out, trace=result.trace,
    )


@router.post("/agent/child", response_model=ChildResponse, dependencies=[Depends(require_role("temi"))])
async def agent_child(req: ChildRequest, request: Request) -> ChildResponse:
    st = request.app.state
    agent = ChildAgent(
        llm=st.child_llm,
        registry=st.registry,
        ctx=ToolContext(executor=st.executor, data=st.data, mode=CHILD, services=st.services),
        rules=st.safety_rules,
        repo=st.services["repo"],
        notifier=st.services["notifier"],
        memory=st.child_memory,
        deadline_s=st.settings.child_deadline_s,
    )
    r = await agent.handle(req.text, req.device_id)
    return ChildResponse(reply=r.reply, alert_created=r.alert_created, alert_level=r.alert_level,
                         alert_id=r.alert_id, game_started=r.game_started, fallback_reason=r.fallback_reason,
                         model=r.model, latency_ms=r.latency_ms)


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
