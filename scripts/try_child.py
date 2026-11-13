"""실제 아이 모드 LLM(.env의 CHILD_LLM__*)과 대화해 보기. 노트북 Ollama 시험용.

  python scripts/try_child.py                 # 대화형 (빈 줄이나 Ctrl+C로 종료)
  python scripts/try_child.py "엄마 보고 싶어"   # 한 문장만

서버와 같은 처리 순서(child_filter → LLM → output_guard)를 거친다. DB는 메모리라 끝나면 사라진다.
화면에는 원답(검사 전)과 최종 답변을 함께 보여 준다.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from app.agent.child import ChildAgent  # noqa: E402
from app.agent.registry import CHILD, ToolContext  # noqa: E402
from app.config import ConfigError, Settings, validate_child_llm_host  # noqa: E402
from app.data import StaticData  # noqa: E402
from app.executors import MockRobotExecutor  # noqa: E402
from app.llm import OpenAICompatClient  # noqa: E402
from app.safety import ChildConversationMemory, SafetyRules  # noqa: E402
from app.services import LogTeacherNotifier  # noqa: E402
from app.storage import SQLiteRepository  # noqa: E402
from app.tools import build_registry  # noqa: E402


async def main() -> int:
    s = Settings()
    try:
        validate_child_llm_host(s)
    except ConfigError as e:
        print(f"설정 오류: {e}")
        return 1
    tz = ZoneInfo(s.timezone)
    now = lambda: datetime.now(tz)  # noqa: E731
    data = StaticData.load()
    llm = OpenAICompatClient(s.child_llm)
    repo = SQLiteRepository(":memory:", now)
    agent = ChildAgent(llm=llm, registry=build_registry(data),
                       ctx=ToolContext(executor=MockRobotExecutor(data.locations, data.games, move_delay_s=0), data=data, mode=CHILD),
                       rules=SafetyRules.load(s.child_safety_rules_path or None), repo=repo,
                       notifier=LogTeacherNotifier(), memory=ChildConversationMemory(s.child_memory_turns, s.child_memory_ttl_s))
    print(f"아이 모드 모델: {s.child_llm.model} @ {s.child_llm.host}")
    once = sys.argv[1:] and " ".join(sys.argv[1:])
    try:
        while True:
            text = once or input("\n아이> ").strip()
            if not text:
                break
            r = await agent.handle(text, device_id="try-temi")
            if r.raw_reply is not None and r.raw_reply != r.reply:
                print(f"  (원답) {r.raw_reply}")
            print(f"temi> {r.reply}")
            print(f"  알림={r.alert_level} 게임={r.game_started} 대체사유={r.fallback_reason} {r.latency_ms}ms")
            if once:
                break
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        await llm.aclose()
        repo.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
