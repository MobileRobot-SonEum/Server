"""config.yaml + 환경변수(.env)를 읽어 Settings 하나로 묶는다."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ApiSettings:
    provider: str = "claude"
    model: str = "claude-haiku-5-5"
    timeout_s: float = 3.0
    retries: int = 1
    total_deadline_s: float = 3.5
    max_tokens: int = 400
    few_shot_k: int = 4


@dataclass
class Settings:
    backend: str = "api"
    api: ApiSettings = field(default_factory=ApiSettings)
    low_confidence_threshold: float = 0.6
    auto_num_candidates: int = 3
    cache_enabled: bool = True
    cache_max_size: int = 512
    examples_path: Path = ROOT / "data" / "examples.jsonl"
    log_level: str = "INFO"
    log_raw_text: bool = False


def _as_bool(v: str) -> bool:
    return v.strip().lower() in {"1", "true", "yes", "on"}


def load_settings(config_path: Optional[Path] = None) -> Settings:
    load_dotenv(ROOT / ".env")
    path = Path(os.getenv("TRANSLATOR_CONFIG", config_path or ROOT / "config.yaml"))
    raw: Dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    api_raw = raw.get("api", {}) or {}
    api = ApiSettings(**{k: v for k, v in api_raw.items() if k in ApiSettings.__dataclass_fields__})
    cand = raw.get("candidates", {}) or {}
    cache = raw.get("cache", {}) or {}
    data = raw.get("data", {}) or {}
    log = raw.get("logging", {}) or {}

    s = Settings(
        backend=(raw.get("translator", {}) or {}).get("backend", "api"),
        api=api,
        low_confidence_threshold=float(cand.get("low_confidence_threshold", 0.6)),
        auto_num_candidates=int(cand.get("auto_num_candidates", 3)),
        cache_enabled=bool(cache.get("enabled", True)),
        cache_max_size=int(cache.get("max_size", 512)),
        examples_path=ROOT / data.get("examples_path", "data/examples.jsonl"),
        log_level=str(log.get("level", "INFO")),
        log_raw_text=bool(log.get("log_raw_text", False)),
    )

    # 환경변수가 설정 파일보다 우선
    if v := os.getenv("TRANSLATOR_BACKEND"):
        s.backend = v
    if v := os.getenv("TRANSLATOR_PROVIDER"):
        s.api.provider = v
    if v := os.getenv("TRANSLATOR_MODEL"):
        s.api.model = v
    if v := os.getenv("TRANSLATOR_TIMEOUT_S"):
        s.api.timeout_s = float(v)
    if v := os.getenv("LOG_RAW_TEXT"):
        s.log_raw_text = _as_bool(v)
    return s
