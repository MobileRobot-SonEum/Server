"""개인정보를 남기지 않는 로깅.

기본값에서는 gloss/문장 원문을 절대 기록하지 않는다.
시간, 번역기, 지연, 성공 여부 같은 메타데이터만 JSON 한 줄로 남긴다.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any, Optional

logger = logging.getLogger("translator")

_RAW_FIELDS = {"glosses", "sentence", "candidates", "context", "raw_output"}


def setup_logging(level: str = "INFO") -> None:
    if logger.handlers:
        logger.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def log_event(event: str, *, log_raw_text: bool = False, level: int = logging.INFO, **fields: Any) -> None:
    if not log_raw_text:
        fields = {k: v for k, v in fields.items() if k not in _RAW_FIELDS}
    logger.log(level, json.dumps({"event": event, **fields}, ensure_ascii=False, default=str))


def safe_error(exc: Optional[BaseException]) -> Optional[str]:
    """예외 메시지에는 프롬프트/응답 일부가 섞일 수 있으므로 클래스 이름만 남긴다."""
    return type(exc).__name__ if exc is not None else None
