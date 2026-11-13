"""교사 지시 하나를 에이전트로 돌리고 실행 기록을 출력한다.

  # 모의 LLM (정해진 응답, 네트워크 없음)
  python scripts/demo_teacher.py --mock-llm

  # .env의 TEACHER_LLM__* 설정으로 실제 모델 호출 (예: Gemini 무료 키)
  python scripts/demo_teacher.py "식당으로 가서 햇살반 간식 배부 시작해 줘"

로봇은 항상 MockRobotExecutor를 쓴다 (--fail-go-to 로 이동 실패 1회 주입).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Windows 콘솔(cp949)에서도 한글·기호가 깨지거나 오류 나지 않게 UTF-8로 출력
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from app.agent.loop import AgentRunner  # noqa: E402
from app.agent.prompts import build_teacher_prompt  # noqa: E402
from app.agent.registry import TEACHER, ToolContext  # noqa: E402
from app.config import Settings  # noqa: E402
from app.data import StaticData  # noqa: E402
from app.executors import MockRobotExecutor  # noqa: E402
from app.llm import LLMResponse, OpenAICompatClient, ToolCall  # noqa: E402
from app.services import ApprovalService, IncidentService, LogMessageSender  # noqa: E402
from app.storage import SQLiteRepository  # noqa: E402
from app.tools import build_registry  # noqa: E402

DEFAULT_INSTRUCTION = "간식 시간이야. 식당으로 가서 햇살반 간식 배부 시작하고, 아이들한테 손 씻고 오라고 말해 줘."


class DemoScriptedLLM:
    """실제 모델이 할 법한 응답을 차례로 돌려주는 모의 LLM."""

    model = "mock-llm (scripted)"

    def __init__(self):
        self._script = [
            LLMResponse(content=None, tool_calls=[ToolCall("c1", "go_to", {"location": "식당"})]),
            LLMResponse(content=None, tool_calls=[ToolCall("c2", "start_delivery", {"kind": "snack", "targets": ["햇살반"]})]),
            LLMResponse(content=None, tool_calls=[ToolCall("c3", "speak", {"text": "간식 먹기 전에 손 씻고 오세요!"})]),
            LLMResponse(content="식당으로 이동해 햇살반 간식 배부를 시작했고, 손 씻기 안내를 했습니다. "
                                "알레르기 확인 필요: 가온(땅콩), 다솜(우유, 달걀)."),
        ]

    async def chat(self, messages, tools=None):
        return self._script.pop(0)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("instruction", nargs="?", default=DEFAULT_INSTRUCTION)
    ap.add_argument("--mock-llm", action="store_true", help="모의 LLM 사용")
    ap.add_argument("--fail-go-to", action="store_true", help="go_to 1회 일시 실패 주입")
    args = ap.parse_args()

    data = StaticData.load()
    executor = MockRobotExecutor(data.locations, data.games, move_delay_s=0.3)
    if args.fail_go_to:
        executor.fail_next("go_to", 1)

    settings = Settings()
    # 새 도구(get_events, draft_*)용 메모리 DB. 데모가 끝나면 사라진다.
    from datetime import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(settings.timezone)
    now = lambda: datetime.now(tz)  # noqa: E731
    repo = SQLiteRepository(":memory:", now)
    approvals = ApprovalService(repo, LogMessageSender(), data, now)
    services = {"repo": repo, "approvals": approvals, "incidents": IncidentService(repo, approvals), "now": now}
    if args.mock_llm:
        llm = DemoScriptedLLM()
        instruction = DEFAULT_INSTRUCTION
    else:
        llm = OpenAICompatClient(settings.teacher_llm)
        instruction = args.instruction

    runner = AgentRunner(
        llm=llm,
        registry=build_registry(data),
        ctx=ToolContext(executor=executor, data=data, mode=TEACHER, services=services),
        system_prompt=build_teacher_prompt(data.locations, data.games, today=now().date().isoformat()),
        max_steps=settings.agent_max_steps,
    )
    try:
        result = await runner.run(instruction)
    finally:
        if hasattr(llm, "aclose"):
            await llm.aclose()

    print(json.dumps({
        "status": result.status, "answer": result.answer, "question": result.question,
        "steps": result.steps, "model": result.model, "trace": result.trace,
    }, ensure_ascii=False, indent=2))
    return 0 if result.status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
