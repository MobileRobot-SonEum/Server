"""child_filter / output_guard 단위 테스트 (config/child_safety.yaml 기준)."""

import pytest

from app.safety import SafetyRules, check_utterance, guard_reply


@pytest.fixture(scope="module")
def rules():
    return SafetyRules.load()


@pytest.mark.parametrize("text,category", [
    ("선생님 배가 아파요", "pain"),
    ("머리 아프다", "pain"),
    ("무릎에서 피 나", "injury"),
    ("나 넘어졌어", "injury"),
    ("민수가 나 때렸어", "violence"),
    ("형한테 맞았어", "violence"),
    ("창문에서 뛰어내릴 거야", "danger"),
    ("구슬 삼켰어", "danger"),
])
def test_urgent_signals(rules, text, category):
    r = check_utterance(text, rules)
    assert r.level == "urgent" and r.category == category
    assert r.matched.startswith(f"urgent:{category}:")  # 어떤 규칙이 걸렸는지 남긴다


@pytest.mark.parametrize("text,category", [
    ("엄마 보고 싶어", "missing_parent"),
    ("엄마가 너무 보고싶어", "missing_parent"),
    ("집에 가고 싶어", "missing_parent"),
    ("천둥 무서워", "fear"),
    ("나 슬퍼", "sadness"),
    ("친구들이 나만 빼고 놀아", "sadness"),
])
def test_emotional_signals(rules, text, category):
    r = check_utterance(text, rules)
    assert r.level == "normal" and r.category == category


@pytest.mark.parametrize("text", [
    "우리 집은 아파트야",        # "아파" 오탐 방지
    "아파트 놀이터에서 놀았어",
    "아프리카 코끼리 좋아",      # "아프" 오탐 방지
    "점심에 칼국수 먹었어",      # "칼" 오탐 방지
    "공룡 이야기 해 줘",
    "정답 맞았어",               # 퀴즈 정답은 폭력 아님
    "apple 이 사과야?",
])
def test_no_signal(rules, text):
    assert check_utterance(text, rules).level is None


def test_exclusion_does_not_hide_real_signal(rules):
    assert check_utterance("아파트 계단에서 넘어졌어", rules).level == "urgent"
    assert check_utterance("아파트에서 배가 아파", rules).category == "pain"


def test_urgent_wins_over_emotional(rules):
    r = check_utterance("엄마 보고 싶고 배도 아파", rules)
    assert r.level == "urgent"


def test_whitespace_normalized(rules):
    assert check_utterance("엄마   보고    싶어", rules).level == "normal"


# --- output_guard ---

@pytest.mark.parametrize("reply", [
    "엄마가 금방 올 거야!",
    "엄마는 곧 오실 거야.",
    "꼭 사 줄게!",
    "약속할게.",
    "금방 괜찮아질 거야.",
    "아무 일도 없을 거야.",
])
def test_promises_replaced(rules, reply):
    g = guard_reply(reply, rules)
    assert g.replaced and g.reason.startswith("promise:") and g.text == rules.fallback_reply


def test_emotional_fallback(rules):
    g = guard_reply("엄마가 금방 데리러 올 거야", rules, emotional=True)
    assert g.text == rules.fallback_reply_emotional


def test_banned_replaced(rules):
    assert guard_reply("그건 바보 같은 생각이야", rules).reason.startswith("banned:")


def test_ok_reply_kept(rules):
    g = guard_reply("**사과**는 영어로 apple이야!", rules)
    assert not g.replaced and g.text == "사과는 영어로 apple이야!"  # 마크다운 기호 제거


def test_long_reply_truncated_at_sentence(rules):
    long = "사과는 영어로 apple이야. " + "바나나는 banana라고 해. " * 10
    g = guard_reply(long, rules)
    assert not g.replaced and g.reason == "truncated"
    assert len(g.text) <= rules.max_chars and g.text.endswith(".")


def test_long_reply_without_boundary_falls_back(rules):
    g = guard_reply("가" * 200, rules)
    assert g.replaced and g.reason == "too_long_no_boundary"


def test_empty_reply(rules):
    assert guard_reply("", rules).reason == "empty"
