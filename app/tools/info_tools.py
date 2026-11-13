"""정보 조회 도구: get_child_info (2단계에서 get_events 추가)."""

from __future__ import annotations

from app.agent.registry import TEACHER, Tool, ToolContext, ToolRegistry, ToolResult
from app.data import StaticData


async def _get_child_info(args: dict, ctx: ToolContext) -> ToolResult:
    name = args["name"].strip()
    child = ctx.data.find_child(name)
    if child:
        return ToolResult(ok=True, data=child)
    members = ctx.data.children_in_class(name)
    if members:
        return ToolResult(ok=True, data={"class": name, "children": members})
    return ToolResult(ok=False, error=f"'{name}' 아이나 반을 찾지 못했습니다")


def register_info_tools(reg: ToolRegistry, data: StaticData) -> None:
    reg.register(Tool(
        name="get_child_info",
        description="아이 이름으로 반, 나이, 알레르기를 조회한다. 반 이름을 넣으면 그 반 아이 전체를 돌려준다.",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string", "minLength": 1, "description": "아이 이름 또는 반 이름"}},
            "required": ["name"],
            "additionalProperties": False,
        },
        allowed_modes=frozenset({TEACHER}),
        handler=_get_child_info,
    ))
