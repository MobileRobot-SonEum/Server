"""temi 동작 도구: go_to, speak, get_locations, start_delivery, start_game."""

from __future__ import annotations

from app.agent.registry import CHILD, TEACHER, Tool, ToolContext, ToolRegistry, ToolResult
from app.data import StaticData, similar
from app.executors import ExecResult

TEACHER_ONLY = frozenset({TEACHER})
BOTH = frozenset({TEACHER, CHILD})


def _from_exec(r: ExecResult) -> ToolResult:
    return ToolResult(ok=r.ok, data=r.detail if r.ok else None, error=r.error, retryable=r.retryable,
                      ask_teacher=r.ask_teacher)


async def _get_locations(args: dict, ctx: ToolContext) -> ToolResult:
    return _from_exec(await ctx.executor.get_locations())


async def _go_to(args: dict, ctx: ToolContext) -> ToolResult:
    loc = args["location"].strip()
    if loc not in ctx.data.locations:
        # 실행기 종류와 상관없이 목록 밖 장소로는 움직이지 않는다
        sug = similar(loc, ctx.data.locations)
        hint = f" 혹시 {', '.join(sug)}인가요?" if sug else ""
        return ToolResult(
            ok=False,
            error=f"목록에 없는 장소입니다: {loc}. 이동하지 않았습니다.",
            data={"locations": ctx.data.locations, "suggestions": sug},
            ask_teacher=f"'{loc}'은(는) 저장된 장소 목록에 없어요.{hint} 저장된 장소: {', '.join(ctx.data.locations)}. 어디로 갈까요?",
        )
    return _from_exec(await ctx.executor.go_to(loc))


async def _speak(args: dict, ctx: ToolContext) -> ToolResult:
    return _from_exec(await ctx.executor.speak(args["text"].strip()))


async def _stop(args: dict, ctx: ToolContext) -> ToolResult:
    return _from_exec(await ctx.executor.stop())


async def _start_game(args: dict, ctx: ToolContext) -> ToolResult:
    name = args["name"].strip()
    if name not in ctx.data.games:
        sug = similar(name, ctx.data.games)
        hint = f" 혹시 {', '.join(sug)}인가요?" if sug else ""
        return ToolResult(
            ok=False,
            error=f"목록에 없는 게임입니다: {name}. 시작하지 않았습니다. 할 수 있는 게임: {', '.join(ctx.data.games)}",
            data={"games": ctx.data.games, "suggestions": sug},
            ask_teacher=f"'{name}'은(는) 게임 목록에 없어요.{hint} 할 수 있는 게임: {', '.join(ctx.data.games)}. 어떤 걸 시작할까요?",
        )
    return _from_exec(await ctx.executor.start_game(name))


async def _start_delivery(args: dict, ctx: ToolContext) -> ToolResult:
    kind = args["kind"]
    targets = [t.strip() for t in args["targets"] if t.strip()]
    # 대상은 아이 이름 또는 반 이름. 확인되지 않은 대상이 있으면 배부를 시작하지 않는다.
    children: list[dict] = []
    unknown: list[str] = []
    for t in targets:
        if (c := ctx.data.find_child(t)):
            children.append(c)
        elif (in_class := ctx.data.children_in_class(t)):
            children.extend(in_class)
        else:
            unknown.append(t)
    if unknown:
        return ToolResult(ok=False, error=f"확인되지 않은 배부 대상: {', '.join(unknown)} (아이 이름이나 반 이름을 쓰세요)")

    r = await ctx.executor.start_delivery(kind, targets)
    if not r.ok:
        return _from_exec(r)
    allergy_notes = [
        {"name": c["name"], "allergies": c["allergies"]} for c in children if c.get("allergies")
    ]
    data = dict(r.detail)
    data["children"] = [c["name"] for c in children]
    data["allergy_notes"] = allergy_notes  # 배부 전에 교사가 확인해야 할 알레르기
    return ToolResult(ok=True, data=data)


def register_robot_tools(reg: ToolRegistry, data: StaticData) -> None:
    reg.register(Tool(
        name="get_locations",
        description="temi에 저장된 위치 목록과 현재 위치를 조회한다.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        allowed_modes=TEACHER_ONLY,
        handler=_get_locations,
    ))
    reg.register(Tool(
        name="go_to",
        description="temi를 저장된 위치로 이동시킨다. 위치 이름은 저장된 위치 목록과 정확히 같아야 하며, 목록에 없으면 이동하지 않는다.",
        input_schema={
            "type": "object",
            "properties": {"location": {"type": "string", "minLength": 1, "description": "저장된 위치 이름"}},
            "required": ["location"],
            "additionalProperties": False,
        },
        allowed_modes=TEACHER_ONLY,
        handler=_go_to,
    ))
    reg.register(Tool(
        name="speak",
        description="temi가 현재 위치에서 문장을 소리 내어 말한다. 아이들이 듣기 쉬운 짧은 문장으로 쓴다.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 1, "description": "말할 문장"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        allowed_modes=TEACHER_ONLY,
        handler=_speak,
    ))
    reg.register(Tool(
        name="start_delivery",
        description="식판(meal) 또는 간식(snack) 배부를 시작한다. targets는 아이 이름이나 반 이름 목록. 결과의 allergy_notes를 교사에게 꼭 알린다.",
        input_schema={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["meal", "snack"], "description": "meal=식판, snack=간식"},
                "targets": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                            "description": "아이 이름 또는 반 이름"},
            },
            "required": ["kind", "targets"],
            "additionalProperties": False,
        },
        allowed_modes=TEACHER_ONLY,
        handler=_start_delivery,
    ))
    reg.register(Tool(
        name="stop",
        description="temi가 하던 동작(이동, 배부, 게임, 말하기)을 멈춘다.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        allowed_modes=TEACHER_ONLY,
        handler=_stop,
    ))
    reg.register(Tool(
        name="start_game",
        description=f"정해진 게임 중 하나를 시작한다. 게임 목록: {', '.join(data.games)}. 목록에 없는 게임은 시작되지 않는다.",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string", "minLength": 1, "description": "게임 목록의 이름 그대로"}},
            "required": ["name"],
            "additionalProperties": False,
        },
        allowed_modes=BOTH,
        handler=_start_game,
    ))
