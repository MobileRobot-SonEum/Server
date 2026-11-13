"""/agent/child: 처리 순서, 알림, 원문 미저장, 단기 기억, 모드 제한."""

import logging

import pytest

from app.llm import LLMError

from .conftest import RULE_KEY, TEACHER_KEY, TEMI_KEY, call, text_reply, tools_reply

H = {"X-API-Key": TEMI_KEY}


def say(client, text, device="temi-1"):
    r = client.post("/agent/child", json={"text": text, "device_id": device}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


# --- 위험 신호 ---

def test_urgent_skips_llm_and_notifies_now(client, child_llm, notifier, repo):
    body = say(client, "선생님 배가 아파요")
    assert body["reply"] == "선생님께 바로 알려줄게. 여기서 같이 기다리자."
    assert body["alert_created"] and body["alert_level"] == "urgent"
    assert child_llm.requests == []  # LLM 미호출
    alert = repo.get_alert(body["alert_id"])
    assert alert["level"] == "urgent" and alert["category"] == "pain"
    assert alert["utterance"] == "선생님 배가 아파요"  # 알림이 생긴 경우만 원문 저장
    assert alert["matched"].startswith("urgent:pain:")
    assert notifier.notified == [alert["id"]]  # 저장과 동시에 즉시 전송


def test_urgent_reply_has_no_promise(client, app):
    from app.safety import guard_reply
    rules = app.state.safety_rules
    assert not guard_reply(rules.urgent_fixed_reply, rules).replaced


def test_emotional_alert_and_llm_comfort(client, child_llm, notifier, repo):
    child_llm.script.append(text_reply("엄마가 보고 싶구나. 선생님이랑 같이 그림 그려 볼까?"))
    body = say(client, "엄마 보고 싶어")
    assert body["alert_level"] == "normal" and body["reply"].startswith("엄마가 보고 싶구나")
    assert notifier.notified == []  # 일반 알림은 저장만
    assert repo.get_alert(body["alert_id"])["category"] == "missing_parent"
    system = child_llm.requests[0]["messages"][0]["content"]
    assert "선생님에게는 이미 알렸다" in system


def test_emotional_promise_replaced_with_emotional_fallback(client, child_llm, app):
    child_llm.script.append(text_reply("엄마가 금방 올 거야!"))
    body = say(client, "엄마 보고 싶어")
    assert body["reply"] == app.state.safety_rules.fallback_reply_emotional
    assert body["fallback_reason"].startswith("promise:")


def test_normal_promise_replaced(client, child_llm, app):
    child_llm.script.append(text_reply("내일 꼭 사탕 사 줄게!"))
    body = say(client, "사탕 줘")
    assert body["reply"] == app.state.safety_rules.fallback_reply and not body["alert_created"]


def test_llm_failure_gives_safe_reply(client, child_llm, app):
    child_llm.script.append(LLMError("connection", "Ollama 연결 실패"))
    body = say(client, "공룡 이야기 해 줘")
    assert body["reply"] == app.state.safety_rules.fallback_reply
    assert body["fallback_reason"] == "llm_error:connection"
    assert "Ollama" not in body["reply"]


# --- 원문 미저장 ---

def test_plain_conversation_leaves_no_trace(client, child_llm, repo, caplog):
    caplog.set_level(logging.DEBUG)
    secret = "우리 강아지 이름은 뭉치야"
    child_llm.script.append(text_reply("뭉치 귀엽겠다!"))
    body = say(client, secret)
    assert not body["alert_created"]
    assert repo.list_alerts() == []
    assert secret not in repo.dump_all_text()
    assert "뭉치" not in caplog.text  # 아이 발화도, 답변도 로그에 없음


def test_alerted_utterance_not_in_logs(client, caplog):
    caplog.set_level(logging.DEBUG)
    say(client, "친구가 나 때렸어")
    assert "때렸어" not in caplog.text  # DB(teacher_alerts)에는 있지만 로그에는 없음


# --- 모드 제한 ---

def test_child_can_start_listed_game(client, child_llm, executor):
    child_llm.script += [tools_reply(call("start_game", name="숨바꼭질")), text_reply("숨바꼭질 시작! 숨어 봐!")]
    body = say(client, "숨바꼭질 하자")
    assert body["game_started"] == "숨바꼭질"
    assert child_llm.tool_names_offered(0) == ["start_game"]
    assert executor.calls == [("start_game", {"name": "숨바꼭질"})]


def test_child_cannot_move_robot_even_if_llm_tries(client, child_llm, executor):
    child_llm.script += [tools_reply(call("go_to", location="현관")), text_reply("그건 선생님께 물어보자.")]
    body = say(client, "밖에 데려가 줘")
    assert executor.calls == []
    assert body["game_started"] is None
    tool_msg = child_llm.requests[1]["messages"][-1]
    assert "child 모드에서는 'go_to'" in tool_msg["content"]


# --- 단기 대화 기억 ---

def test_memory_keeps_recent_turns_per_device(client, child_llm):
    child_llm.script += [text_reply("사과는 apple!"), text_reply("맞아, apple이야!")]
    say(client, "사과는 영어로 뭐야?")
    say(client, "아까 그거 다시 말해 줘")
    msgs = child_llm.requests[1]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[1]["content"] == "사과는 영어로 뭐야?" and msgs[2]["content"] == "사과는 apple!"


def test_memory_separate_per_device(client, child_llm):
    child_llm.script += [text_reply("a"), text_reply("b")]
    say(client, "첫 번째", device="temi-1")
    say(client, "두 번째", device="temi-2")
    assert len(child_llm.requests[1]["messages"]) == 2  # system + user


def test_memory_expires_after_ttl(client, child_llm, clock):
    child_llm.script += [text_reply("a"), text_reply("b")]
    say(client, "첫 번째")
    clock.advance(121)
    say(client, "두 번째")
    assert len(child_llm.requests[1]["messages"]) == 2


def test_memory_caps_turns(client, child_llm, memory):
    child_llm.script += [text_reply(f"답{i}") for i in range(5)]
    for i in range(5):
        say(client, f"질문{i}")
    hist = memory.history("temi-1")
    assert [m["content"] for m in hist if m["role"] == "user"] == ["질문2", "질문3", "질문4"]


def test_memory_disabled_with_zero(settings, child_llm, teacher_llm, executor):
    from fastapi.testclient import TestClient

    from app.main import create_app
    s = settings.model_copy(update={"child_memory_turns": 0})
    with TestClient(create_app(s, teacher_llm=teacher_llm, child_llm=child_llm, executor=executor)) as c:
        child_llm.script += [text_reply("a"), text_reply("b")]
        say(c, "첫 번째")
        say(c, "두 번째")
    assert len(child_llm.requests[1]["messages"]) == 2


def test_memory_never_written_to_db(client, child_llm, repo):
    child_llm.script.append(text_reply("좋아!"))
    say(client, "기억해 줘 비밀단어 바나나")
    assert "바나나" not in repo.dump_all_text()


def test_no_device_id_no_memory(client, child_llm):
    child_llm.script += [text_reply("a"), text_reply("b")]
    client.post("/agent/child", json={"text": "첫 번째"}, headers=H)
    client.post("/agent/child", json={"text": "두 번째"}, headers=H)
    assert len(child_llm.requests[1]["messages"]) == 2


# --- 역할별 키 ---

@pytest.mark.parametrize("method,path", [("post", "/events"), ("get", "/approvals"), ("get", "/alerts"),
                                         ("post", "/agent/teacher"), ("get", "/reports")])
def test_temi_key_forbidden_elsewhere(client, method, path):
    r = getattr(client, method)(path, headers=H, **({"json": {}} if method == "post" else {}))
    assert r.status_code == 403


@pytest.mark.parametrize("key", [TEACHER_KEY, RULE_KEY])
def test_child_endpoint_only_for_temi(client, key):
    r = client.post("/agent/child", json={"text": "안녕"}, headers={"X-API-Key": key})
    assert r.status_code == 403
