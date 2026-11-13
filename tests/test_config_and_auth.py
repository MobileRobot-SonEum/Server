import pytest

from app.config import ConfigError, LLMSettings, Settings, validate_child_llm_host
from app.main import create_app

from .conftest import RULE_KEY, TEACHER_KEY, TEMI_KEY, ScriptedLLM, text_reply


def child_settings(base_url: str, hosts: str | None = None) -> Settings:
    kw = {"child_llm": LLMSettings(base_url=base_url)}
    if hosts is not None:
        kw["child_allowed_llm_hosts"] = hosts
    return Settings(_env_file=None, **kw)


# --- 아이 모드 LLM 호스트 허용 목록 ---

@pytest.mark.parametrize("url", ["http://localhost:11434/v1", "http://127.0.0.1:11434/v1"])
def test_child_host_default_allowlist(url):
    validate_child_llm_host(child_settings(url))


def test_child_host_not_in_allowlist_rejected():
    with pytest.raises(ConfigError, match="CHILD_ALLOWED_LLM_HOSTS"):
        validate_child_llm_host(child_settings("http://3.35.10.20:11434/v1"))


def test_child_host_ec2_added_to_allowlist():
    s = child_settings("http://3.35.10.20:11434/v1", hosts="localhost, 127.0.0.1, 3.35.10.20")
    assert s.child_allowed_llm_hosts == ["localhost", "127.0.0.1", "3.35.10.20"]
    validate_child_llm_host(s)


@pytest.mark.parametrize("url,host", [
    ("https://generativelanguage.googleapis.com/v1beta/openai/", "generativelanguage.googleapis.com"),
    ("https://api.groq.com/openai/v1", "api.groq.com"),
    ("https://ollama.com/v1", "ollama.com"),
])
def test_external_api_rejected_even_if_allowlisted(url, host):
    with pytest.raises(ConfigError, match="외부 LLM API"):
        validate_child_llm_host(child_settings(url, hosts=f"localhost,{host}"))


def test_app_refuses_to_start_with_external_child_llm():
    s = Settings(_env_file=None, child_llm=LLMSettings(base_url="https://api.groq.com/openai/v1"),
                 child_allowed_llm_hosts="api.groq.com")
    with pytest.raises(ConfigError):
        create_app(s, teacher_llm=ScriptedLLM(), child_llm=ScriptedLLM())


def test_duplicate_role_keys_rejected():
    s = Settings(_env_file=None, teacher_app_api_key="same", temi_api_key="same")
    with pytest.raises(ConfigError, match="같습니다"):
        create_app(s, teacher_llm=ScriptedLLM(), child_llm=ScriptedLLM())


# --- 역할별 API 키 ---

def test_teacher_endpoint_requires_key(client):
    assert client.post("/agent/teacher", json={"instruction": "안녕"}).status_code == 401
    assert client.post("/agent/teacher", json={"instruction": "안녕"},
                       headers={"X-API-Key": "wrong"}).status_code == 401


@pytest.mark.parametrize("key", [TEMI_KEY, RULE_KEY])
def test_other_roles_cannot_use_teacher_endpoint(client, key):
    r = client.post("/agent/teacher", json={"instruction": "안녕"}, headers={"X-API-Key": key})
    assert r.status_code == 403


def test_teacher_key_allowed(client, teacher_llm):
    teacher_llm.script.append(text_reply("네"))
    r = client.post("/agent/teacher", json={"instruction": "안녕"}, headers={"X-API-Key": TEACHER_KEY})
    assert r.status_code == 200 and r.json()["answer"] == "네"


def test_health_is_open_and_shows_models(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["models"]["teacher"]["model"] == "test-teacher"
    assert body["models"]["child"]["model"] == "test-child"
    assert body["executor"] == "mock"
    assert "api_key" not in r.text
