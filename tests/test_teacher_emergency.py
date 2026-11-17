import pytest

from app.safety.teacher_filter import TeacherEmergencyRules

from .conftest import TEACHER_KEY, text_reply

T = {"X-API-Key": TEACHER_KEY}


@pytest.fixture(scope="module")
def rules():
    return TeacherEmergencyRules.load()


@pytest.mark.parametrize("text", [
    "놀이방에서 아이가 넘어졌대, 알림 울려 줘",
    "가온이가 다쳤어",
    "마루 무릎에서 피가 나",
    "아이가 쓰러졌어요",
    "다솜이가 구슬을 삼켰어",
    "숨을 못 쉬어요",
])
def test_emergency_detected(rules, text):
    assert rules.check(text)


@pytest.mark.parametrize("text", [
    "오늘 넘어진 사고 보고서 다시 만들어 줘",     # T12
    "오늘 넘어짐 같은 사고 기록 있었어?",           # T11
    "오늘 넘어진 일 부모님께 알려 드려",            # T15
    "놀이방으로 가 줘",
    "피아노 소리 좀 줄이라고 말해 줘",
    "어제 넘어졌던 일 기록 보여 줘",
])
def test_non_emergency(rules, text):
    assert rules.check(text) is None


def test_emergency_skips_llm(client, teacher_llm, executor):
    r = client.post("/agent/teacher", json={"instruction": "놀이방에서 아이가 넘어졌대, 알림 울려 줘"}, headers=T).json()
    assert r["status"] == "completed" and r["emergency_redirect"] is True
    assert "비상" in r["answer"] and "현장" in r["answer"]
    assert teacher_llm.requests == [] and executor.calls == []
    rule = next(s for s in r["trace"] if s["type"] == "rule")
    assert rule["rule"] == "teacher_emergency" and rule["matched"].startswith("넘어졌")


def test_non_korean_answer_retried(client, teacher_llm):
    teacher_llm.script += [text_reply("去哪里呢？请告诉我具体的位置。"), text_reply("어디로 갈까요?")]
    r = client.post("/agent/teacher", json={"instruction": "거기로 가 줘"}, headers=T).json()
    assert r["answer"] == "어디로 갈까요?"
    assert "한국어로만" in teacher_llm.requests[1]["messages"][-1]["content"]


def test_non_korean_twice_asks_teacher(client, teacher_llm):
    teacher_llm.script += [text_reply("去哪里呢？"), text_reply("请告诉我。")]
    r = client.post("/agent/teacher", json={"instruction": "거기로 가 줘"}, headers=T).json()
    assert r["status"] == "needs_teacher"
