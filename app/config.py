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
    timeout_s: float = 25.0  # 호출 1번의 시간 제한 (재시도 1회 별도)
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
    teacher_llm: LLMSettings = Field(default_factory=lambda: LLMSettings(timeout_s=25.0))
    child_llm: LLMSettings = Field(
        default_factory=lambda: LLMSettings(model="qwen3:14b", timeout_s=10.0, reasoning_effort="none")
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
    # 서버 시작 시 자체 서빙 LLM(허용 목록 호스트) 워밍업. 외부 API는 하지 않음
    llm_warmup_on_start: bool = True
    agent_deadline_s: float = 60.0  # 교사 지시 하나 전체 처리 상한. 넘으면 "응답이 늦어지고 있어요" failed
    child_deadline_s: float = 10.0  # 아이 모드 LLM 단계 상한. 넘으면 기본 답변

    # --- 로봇 실행기 ---
    robot_executor: Literal["mock", "control"] = "mock"
    mock_move_delay_s: float = 0.5
    mock_failure_rate: float = 0.0
    mock_fail_tools: Annotated[list[str], NoDecode] = []  # 비우면 모든 동작이 실패 대상
    mock_seed: int | None = None
    control_ws_url: str = "ws://localhost:8765/control"

    # --- 아이 모드 ---
    # 아이 대화 원문은 로그·DB에 남기지 않는다 (교사 알림이 생긴 문장만 teacher_alerts에 저장). 끄는 설정은 두지 않는다.
    child_safety_rules_path: str = ""  # 비우면 config/child_safety.yaml
    teacher_emergency_rules_path: str = ""  # 비우면 config/teacher_emergency.yaml
    # 단기 대화 기억: temi별 최근 N턴을 메모리에만 두고 TTL 동안 대화가 없으면 지운다. 0이면 기억 안 함.
    child_memory_turns: int = 3
    child_memory_ttl_s: float = 120.0

    # --- 저장소 ---
    db_path: str = "temi_agent.db"

    # --- 모델 비교 평가 (scripts/eval_models.py) ---
    eval_models: Annotated[list[str], NoDecode] = ["qwen3:14b", "qwen3.5:9b", "gemma4:12b", "exaone3.5:7.8b"]
    eval_base_url: str = ""  # 비우면 CHILD_LLM__BASE_URL
    eval_api_key: SecretStr | None = None  # 비우면 CHILD_LLM__API_KEY

    timezone: str = "Asia/Seoul"
    log_level: str = "INFO"

    _split_hosts = field_validator("child_allowed_llm_hosts", "mock_fail_tools", "eval_models", mode="before")(
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
