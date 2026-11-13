"""서버 설정.

환경 변수(.env)에서 읽는다. 중첩 설정은 `__` 구분자를 쓴다.
예) TEACHER_LLM__BASE_URL, CHILD_LLM__MODEL
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# 아이 모드에서는 허용 목록에 있어도 거부하는 외부 LLM API 도메인.
# 하위 도메인까지 막는다 (예: generativelanguage.googleapis.com).
BLOCKED_EXTERNAL_LLM_DOMAINS: tuple[str, ...] = (
    "googleapis.com",  # Gemini
    "groq.com",  # Groq
    "openai.com",
    "anthropic.com",
    "ollama.com",  # Ollama 클라우드 (자체 서빙 아님)
)


class ConfigError(RuntimeError):
    """서버를 띄우면 안 되는 설정 오류."""


class LLMSettings(BaseModel):
    """OpenAI 호환 Chat Completions 엔드포인트 하나에 대한 설정."""

    base_url: str = "http://localhost:11434/v1"
    api_key: SecretStr = SecretStr("ollama")  # Ollama는 값이 필요하지만 무시한다
    model: str = "qwen3:14b"
    timeout_s: float = 60.0
    temperature: float = 0.2
    max_tokens: int | None = None
    # 도구 호출 방식: native=tools 파라미터, json=JSON 출력 프롬프트, auto=native 실패 시 json으로 대체
    tool_mode: Literal["auto", "native", "json"] = "auto"
    # thinking 제어. 비워 두면 보내지 않는다.
    # Ollama(qwen3 등)는 "none"이면 thinking을 끈다.
    # Gemini 2.5는 "none" 가능, Gemini 3 이상은 끌 수 없어서 "minimal"이 가장 낮다.
    reasoning_effort: str | None = None

    @field_validator("reasoning_effort", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        return v or None

    @property
    def host(self) -> str:
        return (urlparse(self.base_url).hostname or "").lower()


def _split_csv(v):
    if isinstance(v, str):
        return [item.strip() for item in v.split(",") if item.strip()]
    return v


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # --- LLM (모드별 분리) ---
    teacher_llm: LLMSettings = Field(default_factory=LLMSettings)
    child_llm: LLMSettings = Field(
        default_factory=lambda: LLMSettings(model="exaone3.5:7.8b", timeout_s=20.0)
    )
    # 아이 모드 LLM이 쓸 수 있는 호스트 (쉼표 구분). 외부 API 도메인은 여기에 있어도 거부한다.
    child_allowed_llm_hosts: Annotated[list[str], NoDecode] = ["localhost", "127.0.0.1"]

    # --- 접근 제어 (역할별 API 키, 헤더 X-API-Key) ---
    auth_enabled: bool = True
    teacher_app_api_key: SecretStr | None = None
    temi_api_key: SecretStr | None = None
    rule_engine_api_key: SecretStr | None = None

    # --- 에이전트 ---
    agent_max_steps: int = 8

    # --- 로봇 실행기 ---
    robot_executor: Literal["mock", "control"] = "mock"
    mock_move_delay_s: float = 0.5
    mock_failure_rate: float = 0.0
    mock_fail_tools: Annotated[list[str], NoDecode] = []  # 비우면 모든 동작이 실패 대상
    mock_seed: int | None = None
    control_ws_url: str = "ws://localhost:8765/control"

    # --- 개인정보 ---
    # 기본값 False: 아이 대화 원문을 로그·DB에 남기지 않는다 (교사 알림이 생긴 문장만 예외).
    store_child_utterances: bool = False

    # --- 저장소 ---
    db_path: str = "temi_agent.db"

    log_level: str = "INFO"

    _split_hosts = field_validator("child_allowed_llm_hosts", "mock_fail_tools", mode="before")(
        _split_csv
    )

    @field_validator("child_allowed_llm_hosts")
    @classmethod
    def _lower_hosts(cls, v: list[str]) -> list[str]:
        return [h.lower() for h in v]


def is_blocked_external_host(host: str) -> bool:
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in BLOCKED_EXTERNAL_LLM_DOMAINS)


def validate_child_llm_host(settings: Settings) -> None:
    """아이 모드 LLM이 자체 서빙(허용 목록) 호스트인지 확인한다. 아니면 ConfigError."""
    host = settings.child_llm.host
    if not host:
        raise ConfigError(f"CHILD_LLM__BASE_URL에서 호스트를 읽을 수 없습니다: {settings.child_llm.base_url}")
    if is_blocked_external_host(host):
        raise ConfigError(
            f"아이 모드는 외부 LLM API를 쓸 수 없습니다 (호스트: {host}). "
            "자체 서빙 Ollama 주소를 CHILD_LLM__BASE_URL에 넣어 주세요."
        )
    if host not in settings.child_allowed_llm_hosts:
        raise ConfigError(
            f"아이 모드 LLM 호스트 '{host}'가 CHILD_ALLOWED_LLM_HOSTS에 없습니다 "
            f"(현재 허용: {', '.join(settings.child_allowed_llm_hosts)})."
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
