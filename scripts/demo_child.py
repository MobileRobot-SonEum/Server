"""아이 모드 예시 3개: 일반 대화 / 정서 발화 / 긴급 발화 (모의 LLM).

  python scripts/demo_child.py

모의 LLM 답변은 미리 정해 둔 것이다. 필터·알림·출력 검사·저장 여부는 실제 코드 결과다.
"""

from __future__ import annotations

import _demo_common as dc  # noqa: F401
from _demo_common import demo_settings, h
from fastapi.testclient import TestClient

from app.llm import LLMResponse
from app.main import create_app
from app.services import LogTeacherNotifier


class DemoChildLLM:
    model = "mock-child-llm (scripted)"

    def __init__(self, replies: list[str]):
        self.replies = replies
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        return LLMResponse(content=self.replies.pop(0))


CASES = [
    ("일반 대화", "사과는 영어로 뭐야?", "사과는 영어로 apple이야! 같이 말해 볼까? 애플!"),
    ("정서 발화", "엄마 보고 싶어", "엄마가 금방 오실 거야!"),   # 약속 표현 → 출력 검사에서 대체되는지 보기
    ("긴급 발화", "친구가 나 때렸어", None),                   # LLM 미호출
]


def main() -> None:
    llm = DemoChildLLM([c[2] for c in CASES if c[2]])
    notifier = LogTeacherNotifier()
    app = create_app(demo_settings(), child_llm=llm, notifier=notifier)
    repo = app.state.services["repo"]
    with TestClient(app) as c:
        for title, text, scripted in CASES:
            before = llm.calls
            r = c.post("/agent/child", json={"text": text, "device_id": "temi-1"}, headers=h("temi")).json()
            print(f"[{title}] 아이: \"{text}\"")
            if scripted:
                print(f"   모의 LLM 원답: \"{scripted}\"")
            print(f"   temi 답변   : \"{r['reply']}\"")
            print(f"   LLM 호출={llm.calls - before}  알림={r['alert_level']}  fallback_reason={r['fallback_reason']}")
            if r["alert_id"]:
                a = repo.get_alert(r["alert_id"])
                print(f"   teacher_alerts #{a['id']}: level={a['level']} matched=\"{a['matched']}\" 원문 저장=\"{a['utterance']}\"")
            print()

        print(f"즉시 전송된 긴급 알림(TeacherNotifier 스텁): {notifier.notified}")
        stored = repo.dump_all_text()
        print(f"DB에 일반 대화 원문 있음? {'사과는 영어로 뭐야?' in stored}")
        print(f"단기 기억(메모리) 턴 수, temi-1: {len(app.state.child_memory.history('temi-1')) // 2}")


if __name__ == "__main__":
    main()
