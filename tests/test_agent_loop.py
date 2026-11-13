import asyncio

from app.agent.loop import AgentRunner
from app.agent.prompts import build_teacher_prompt
from app.agent.registry import TEACHER, ToolContext
from app.llm import LLMError, ToolFormatError
from app.tools import build_registry

from .conftest import ScriptedLLM, call, text_reply, tools_reply


def run(llm, executor, data, instruction="테스트 지시", **kw):
    runner = AgentRunner(
        llm=llm,
        registry=build_registry(data),
        ctx=ToolContext(executor=executor, data=data, mode=TEACHER),
        system_prompt=build_teacher_prompt(data.locations, data.games),
        **kw,
    )
    return asyncio.run(runner.run(instruction))


def types(result):
    return [s["type"] for s in result.trace]


def test_happy_path_multi_step(executor, data):
    llm = ScriptedLLM([
        tools_reply(call("go_to", location="식당")),
        tools_reply(call("speak", text="간식 먹을 시간이에요!")),
        text_reply("식당으로 이동해서 간식 시간을 알렸습니다."),
    ])
    r = run(llm, executor, data)
    assert r.status == "completed"
    assert r.answer.startswith("식당으로")
    assert [c[0] for c in executor.calls] == ["go_to", "speak"]
    tool_steps = [s for s in r.trace if s["type"] == "tool"]
    assert [s["name"] for s in tool_steps] == ["go_to", "speak"]
    assert all(s["ok"] for s in tool_steps)
    # 두 번째 요청에 첫 도구 결과가 tool 메시지로 들어가야 한다
    second = llm.requests[1]["messages"]
    assert second[-1]["role"] == "tool" and "식당" in second[-1]["content"]
    assert types(r)[0] == "instruction" and types(r)[-1] == "final"


def test_retryable_failure_is_retried_once(executor, data):
    executor.fail_next("go_to", 1)
    llm = ScriptedLLM([tools_reply(call("go_to", location="놀이방")), text_reply("놀이방 도착")])
    r = run(llm, executor, data)
    assert r.status == "completed"
    assert "tool_retry" in types(r)
    tool = next(s for s in r.trace if s["type"] == "tool")
    assert tool["ok"] and tool["retried"]


def test_repeated_failures_return_question(executor, data):
    executor.fail_next("go_to", 10)
    llm = ScriptedLLM([tools_reply(call("go_to", location="놀이방"))] * 3)
    r = run(llm, executor, data, max_tool_failures=3)
    assert r.status == "needs_teacher"
    assert "실패" in r.question


def test_tool_error_is_fed_back_to_llm(executor, data):
    llm = ScriptedLLM([
        tools_reply(call("go_to", location="옥상")),  # 없는 위치
        tools_reply(call("get_locations")),
        tools_reply(call("go_to", location="놀이방")),
        text_reply("놀이방으로 갔습니다."),
    ])
    r = run(llm, executor, data)
    assert r.status == "completed"
    assert "저장되지 않은 위치" in llm.requests[1]["messages"][-1]["content"]


def test_ask_teacher(executor, data):
    llm = ScriptedLLM([tools_reply(call("ask_teacher", question="어느 반으로 갈까요?"))])
    r = run(llm, executor, data)
    assert r.status == "needs_teacher"
    assert r.question == "어느 반으로 갈까요?"
    assert executor.calls == []


def test_max_steps(executor, data):
    llm = ScriptedLLM([tools_reply(call("get_locations"))] * 3)
    r = run(llm, executor, data, max_steps=3)
    assert r.status == "needs_teacher"
    assert "최대 단계" in r.question
    assert r.steps == 3


def test_format_error_then_recovery(executor, data):
    llm = ScriptedLLM([ToolFormatError("깨짐", raw="{oops"), text_reply("완료했습니다.")])
    r = run(llm, executor, data)
    assert r.status == "completed"
    assert "llm_format_error" in types(r)
    # 교정 메시지가 다음 요청에 들어간다
    assert "형식" in llm.requests[1]["messages"][-1]["content"]


def test_format_error_twice_asks_teacher(executor, data):
    llm = ScriptedLLM([ToolFormatError("깨짐"), ToolFormatError("또 깨짐")])
    r = run(llm, executor, data)
    assert r.status == "needs_teacher"


def test_llm_error_fails_clearly(executor, data):
    llm = ScriptedLLM([LLMError("timeout", "LLM 응답 시간 초과")])
    r = run(llm, executor, data)
    assert r.status == "failed" and r.timed_out
    assert r.answer == "응답이 늦어지고 있어요. 다시 시도해 주세요."
    err = next(s for s in r.trace if s["type"] == "llm_error")
    assert err["error"]["kind"] == "timeout"


def test_bad_arguments_rejected_before_executor(executor, data):
    llm = ScriptedLLM([tools_reply(call("start_delivery", kind="drink", targets=[])), text_reply("실패")])
    r = run(llm, executor, data)
    tool = next(s for s in r.trace if s["type"] == "tool")
    assert not tool["ok"] and "인자 오류" in tool["error"]
    assert executor.calls == []


def test_delivery_reports_allergies(executor, data):
    llm = ScriptedLLM([tools_reply(call("start_delivery", kind="snack", targets=["햇살반"])), text_reply("시작")])
    r = run(llm, executor, data)
    tool = next(s for s in r.trace if s["type"] == "tool")
    notes = {n["name"]: n["allergies"] for n in tool["result"]["allergy_notes"]}
    assert notes == {"가온": ["땅콩"], "다솜": ["우유", "달걀"]}


def test_delivery_unknown_target_not_started(executor, data):
    llm = ScriptedLLM([tools_reply(call("start_delivery", kind="meal", targets=["없는반"])), text_reply("x")])
    run(llm, executor, data)
    assert executor.calls == []


def test_prompt_includes_locations_and_games(executor, data):
    llm = ScriptedLLM([text_reply("네")])
    run(llm, executor, data)
    system = llm.requests[0]["messages"][0]["content"]
    assert "놀이방" in system and "숨바꼭질" in system and "get_locations로 다시 확인" in system


def test_connection_error_is_not_timeout(executor, data):
    r = run(ScriptedLLM([LLMError("connection", "연결 실패")]), executor, data)
    assert r.status == "failed" and not r.timed_out and "연결 실패" in r.answer


class SlowLLM:
    model = "slow"

    def __init__(self, delay):
        self.delay = delay

    async def chat(self, messages, tools=None):
        await asyncio.sleep(self.delay)
        return text_reply("늦은 답")


def test_overall_deadline(executor, data):
    r = run(SlowLLM(1.0), executor, data, deadline_s=0.1)
    assert r.status == "failed" and r.timed_out
    assert r.answer == "응답이 늦어지고 있어요. 다시 시도해 주세요."
    assert [s["type"] for s in r.trace][-2:] == ["timeout", "final"]


def test_deadline_counts_tool_time(data):
    from app.executors import MockRobotExecutor
    slow_robot = MockRobotExecutor(data.locations, data.games, move_delay_s=1.0)
    llm = ScriptedLLM([tools_reply(call("go_to", location="식당")), text_reply("도착")])
    r = run(llm, slow_robot, data, deadline_s=0.2)
    assert r.timed_out and r.steps == 1
