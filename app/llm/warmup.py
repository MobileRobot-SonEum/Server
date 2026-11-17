"""서버 시작 시 자체 서빙 LLM(Ollama) 워밍업.

첫 요청 때 모델을 GPU에 올리느라 수십 초가 걸리면 교사 지시가 시간 제한(60초)에 걸린다.
시작하자마자 백그라운드로 짧은 요청을 보내 모델을 미리 올려 둔다(OLLAMA_KEEP_ALIVE=-1과 함께 쓰면 계속 유지).
외부 API(Gemini 등)는 요금·한도 때문에 워밍업하지 않는다.
"""

from __future__ import annotations

import logging
import time

from app.config import LLMSettings, Settings

from .client import OpenAICompatClient
from .types import LLMError

log = logging.getLogger(__name__)
WARMUP_TIMEOUT_S = 300.0


def warmup_targets(settings: Settings) -> list[LLMSettings]:
    """허용 목록(자체 서빙) 호스트의 서로 다른 (base_url, model)만."""
    seen: set[tuple[str, str]] = set()
    out: list[LLMSettings] = []
    for cfg in (settings.teacher_llm, settings.child_llm):
        key = (cfg.base_url.rstrip("/"), cfg.model)
        if key in seen or cfg.host not in settings.child_allowed_llm_hosts:
            continue
        seen.add(key)
        out.append(cfg.model_copy(update={"timeout_s": WARMUP_TIMEOUT_S}))
    return out


async def warmup(settings: Settings) -> None:
    for cfg in warmup_targets(settings):
        client = OpenAICompatClient(cfg, retry_backoff_s=5.0)
        t0 = time.perf_counter()
        try:
            await client.chat([{"role": "user", "content": "안녕"}])
            log.info("LLM 워밍업 완료: %s @ %s (%.1fs)", cfg.model, cfg.host, time.perf_counter() - t0)
        except LLMError as e:
            log.warning("LLM 워밍업 실패: %s @ %s: %s", cfg.model, cfg.host, e.message)
        finally:
            await client.aclose()
