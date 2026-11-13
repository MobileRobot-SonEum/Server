"""데모 스크립트 공통: 메모리 DB와 데모용 키로 앱을 띄운다 (.env와 무관)."""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=DeprecationWarning)  # starlette TestClient 경고

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from app.config import LLMSettings, Settings  # noqa: E402

KEYS = {"teacher_app": "demo-teacher", "temi": "demo-temi", "rule_engine": "demo-rule"}


def demo_settings(**kw) -> Settings:
    return Settings(
        _env_file=None,
        teacher_llm=LLMSettings(model="(demo)"),
        child_llm=LLMSettings(model="(demo)"),
        teacher_app_api_key=KEYS["teacher_app"],
        temi_api_key=KEYS["temi"],
        rule_engine_api_key=KEYS["rule_engine"],
        db_path=":memory:",
        mock_move_delay_s=0.0,
        log_level="WARNING",
        **kw,
    )


def h(role: str) -> dict[str, str]:
    return {"X-API-Key": KEYS[role]}
