import asyncio

from app.agent.registry import CHILD, TEACHER, ToolContext, validate_args
from app.tools import build_registry

CHILD_FORBIDDEN = {"go_to", "speak", "get_locations", "start_delivery", "get_child_info",
                   "get_events", "draft_parent_message", "draft_incident_report"}


def test_teacher_mode_gets_all_tools(data):
    reg = build_registry(data)
    assert {t.name for t in reg.for_mode(TEACHER)} == set(reg.names())


def test_child_mode_only_games(data):
    reg = build_registry(data)
    names = {t.name for t in reg.for_mode(CHILD)}
    assert names == {"start_game"}
    assert not names & CHILD_FORBIDDEN


def test_child_mode_execution_blocked_even_if_called(data, executor):
    reg = build_registry(data)
    ctx = ToolContext(executor=executor, data=data, mode=CHILD)
    for name, args in [("go_to", {"location": "현관"}), ("start_delivery", {"kind": "snack", "targets": ["가온"]}),
                       ("get_child_info", {"name": "가온"})]:
        r = asyncio.run(reg.execute(name, args, ctx))
        assert not r.ok and "child 모드" in r.error
    assert executor.calls == []


def test_child_can_start_listed_game_only(data, executor):
    reg = build_registry(data)
    ctx = ToolContext(executor=executor, data=data, mode=CHILD)
    assert asyncio.run(reg.execute("start_game", {"name": "숨바꼭질"}, ctx)).ok
    r = asyncio.run(reg.execute("start_game", {"name": "칼싸움"}, ctx))
    assert not r.ok and "목록에 없는 게임" in r.error
    assert executor.calls == [("start_game", {"name": "숨바꼭질"})]


def test_unknown_tool(data, executor):
    reg = build_registry(data)
    r = asyncio.run(reg.execute("fly", {}, ToolContext(executor=executor, data=data, mode=TEACHER)))
    assert not r.ok and "없는 도구" in r.error


def test_validate_args():
    schema = {
        "type": "object",
        "properties": {"kind": {"type": "string", "enum": ["meal", "snack"]},
                       "targets": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
        "required": ["kind", "targets"],
        "additionalProperties": False,
    }
    assert validate_args(schema, {"kind": "meal", "targets": ["가온"]}) == []
    assert any("필수" in p for p in validate_args(schema, {"kind": "meal"}))
    assert any("허용 값" in p for p in validate_args(schema, {"kind": "x", "targets": ["a"]}))
    assert any("최소" in p for p in validate_args(schema, {"kind": "meal", "targets": []}))
    assert any("string" in p for p in validate_args(schema, {"kind": "meal", "targets": [1]}))
    assert any("알 수 없는" in p for p in validate_args(schema, {"kind": "meal", "targets": ["a"], "x": 1}))
    assert validate_args(schema, "문자열") == ["arguments는 object이어야 합니다"]
