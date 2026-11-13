from app.safety.plaintext import has_emoji, has_markdown, to_plain_text, to_speech_text

from .conftest import TEACHER_KEY, text_reply


def test_teacher_plain_text_lists_and_emphasis():
    md = "## 결과\n**가온** 부모님께 초안을 만들었습니다.\n\n- 받는 사람: 가온 부모님\n- 상태: `승인 대기`\n\n[보기](http://x)"
    out = to_plain_text(md)
    assert out == "결과\n가온 부모님께 초안을 만들었습니다.\n\n1) 받는 사람: 가온 부모님\n2) 상태: 승인 대기\n\n보기"


def test_numbered_list_normalized():
    assert to_plain_text("1. 이동\n2. 배부") == "1) 이동\n2) 배부"


def test_speech_text_removes_symbols_and_emoji():
    assert to_speech_text("**사과**는 apple이야! 🍎✨ 같이 해 볼까? ♡") == "사과는 apple이야! 같이 해 볼까?"
    assert to_speech_text("- 하나\n- 둘") == "하나 둘"
    assert to_speech_text("B-A-N-A-N-A, 바나나!") == "B-A-N-A-N-A, 바나나!"


def test_detectors():
    assert has_markdown("**굵게**") and has_markdown("- 목록") and not has_markdown("그냥 문장이야.")
    assert has_emoji("좋아 😀") and not has_emoji("좋아!")


def test_teacher_answer_is_plain_text(client, teacher_llm):
    teacher_llm.script.append(text_reply("**완료**했습니다.\n- 이동: 성공"))
    r = client.post("/agent/teacher", json={"instruction": "x"}, headers={"X-API-Key": TEACHER_KEY}).json()
    assert r["answer"] == "완료했습니다.\n1) 이동: 성공"
