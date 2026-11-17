"""도구 레지스트리: 이름, 설명, 입력 스키마, 허용 모드, 실행 함수."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from app.data import StaticData
from app.executors import RobotExecutor

log = logging.getLogger(__name__)

Mode = Literal["teacher", "child"]
TEACHER: Mode = "teacher"
CHILD: Mode = "child"


@dataclass
class ToolContext:
    executor: RobotExecutor
    data: StaticData
    mode: Mode
    services: dict[str, Any] = field(default_factory=dict)  # 2단계: 저장소 등


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str | None = None
    retryable: bool = False
    # 교사 모드에서 이 값이 있으면 에이전트가 더 진행하지 않고 이 질문으로 교사에게 되묻는다
    ask_teacher: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"ok": self.ok}
        if not self.ok:
            d["error"] = self.error
        if self.data is not None:
            d["data"] = self.data
        return d


Handler = Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult]]


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    allowed_modes: frozenset[str]
    handler: Handler

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.input_schema},
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"도구 이름 중복: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def for_mode(self, mode: Mode) -> list[Tool]:
        return [t for t in self._tools.values() if mode in t.allowed_modes]

    def openai_schemas(self, mode: Mode) -> list[dict[str, Any]]:
        return [t.openai_schema() for t in self.for_mode(mode)]

    async def execute(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(ok=False, error=f"없는 도구입니다: {name}")
        # 모드 제한은 LLM에게 도구를 보여 주지 않는 것에 더해 실행 시점에도 다시 막는다
        if ctx.mode not in tool.allowed_modes:
            log.warning("모드 %s에서 허용되지 않은 도구 호출 차단: %s", ctx.mode, name)
            return ToolResult(ok=False, error=f"{ctx.mode} 모드에서는 '{name}' 도구를 쓸 수 없습니다")
        problems = validate_args(tool.input_schema, args)
        if problems:
            return ToolResult(ok=False, error="인자 오류: " + "; ".join(problems))
        try:
            return await tool.handler(args, ctx)
        except Exception as e:  # 도구 내부 오류가 에이전트 전체를 죽이지 않게
            log.exception("도구 %s 실행 중 오류", name)
            return ToolResult(ok=False, error=f"도구 실행 오류: {type(e).__name__}: {e}")


_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def validate_args(schema: dict[str, Any], args: Any, path: str = "") -> list[str]:
    """도구 스키마에 쓰는 정도의 JSON Schema 부분집합만 검사한다 (type, required, enum, items, minItems, minLength)."""
    problems: list[str] = []
    typ = schema.get("type")
    label = path or "arguments"
    if typ and typ in _TYPE_CHECKS and not _TYPE_CHECKS[typ](args):
        return [f"{label}는 {typ}이어야 합니다"]
    if "enum" in schema and args not in schema["enum"]:
        problems.append(f"{label} 값 '{args}'는 허용 값({', '.join(map(str, schema['enum']))})이 아닙니다")
    if typ == "string" and "minLength" in schema and len(args) < schema["minLength"]:
        problems.append(f"{label}가 비어 있습니다")
    if typ == "object":
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in args:
                problems.append(f"필수 인자 '{req}'가 없습니다")
        for key, val in args.items():
            if key in props:
                problems.extend(validate_args(props[key], val, key))
            elif schema.get("additionalProperties") is False:
                problems.append(f"알 수 없는 인자 '{key}'")
    if typ == "array":
        if "minItems" in schema and len(args) < schema["minItems"]:
            problems.append(f"{label}는 최소 {schema['minItems']}개가 필요합니다")
        if "items" in schema:
            for i, item in enumerate(args):
                problems.extend(validate_args(schema["items"], item, f"{label}[{i}]"))
    return problems
