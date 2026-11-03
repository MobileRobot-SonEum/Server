"""번역 서비스: 캐시, 후보 수 결정, 마감 시간, fallback, 지연 측정, 로그.

HTTP와 무관하다. WebSocket 게이트웨이에서는 다음처럼 바로 부르면 된다.

    service = build_service()
    resp = await asyncio.to_thread(service.translate, TranslationRequest(...))
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Optional, Sequence

from app.config import Settings, load_settings
from app.logging_utils import log_event, safe_error, setup_logging
from app.prompting.retriever import Example
from app.schemas import SentenceType, TranslationRequest, TranslationResponse, TranslationResult
from app.translators.base import Translator, TranslatorError
from app.translators.factory import build_translator
from app.translators.fallback import FallbackTranslator

_HARD_DEADLINE_MARGIN_S = 0.2


class LRUCache:
    def __init__(self, max_size: int):
        self.max_size = max_size
        self._data: "OrderedDict[str, TranslationResponse]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[TranslationResponse]:
        with self._lock:
            if key not in self._data:
                return None
            self._data.move_to_end(key)
            return self._data[key]

    def put(self, key: str, value: TranslationResponse) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.max_size:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


def cache_key(req: TranslationRequest, low_conf: Sequence[str]) -> str:
    return json.dumps(
        {
            "g": req.gloss_words(),
            "lc": list(low_conf),
            "t": req.sentence_type.value,
            "s": req.scenario.value,
            "c": [(t.speaker, t.text) for t in req.context],
            "n": req.num_candidates,
        },
        ensure_ascii=False,
    )


class TranslationService:
    def __init__(self, settings: Settings, primary: Translator, fallback: Optional[Translator] = None):
        self.settings = settings
        self.primary = primary
        self.fallback = fallback or FallbackTranslator()
        self.cache = LRUCache(settings.cache_max_size) if settings.cache_enabled else None
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="translate")
        # 번역기 내부 재시도 마감 + 여유. 이 시간을 넘기면 결과를 기다리지 않고 fallback.
        self.hard_deadline_s = settings.api.total_deadline_s + _HARD_DEADLINE_MARGIN_S

    @property
    def backend_name(self) -> str:
        return self.primary.name

    def _low_confidence(self, req: TranslationRequest) -> list[str]:
        th = self.settings.low_confidence_threshold
        return [g.gloss for g in req.glosses if g.confidence is not None and g.confidence < th]

    def _call_primary(self, req: TranslationRequest) -> TranslationResult:
        future = self._pool.submit(self.primary.translate, req)
        try:
            return future.result(timeout=self.hard_deadline_s)
        except FutureTimeout as e:
            future.cancel()
            raise TranslatorError("deadline_exceeded") from e

    def translate(self, request: TranslationRequest) -> TranslationResponse:
        t0 = time.perf_counter()
        rid = uuid.uuid4().hex[:12]
        raw = self.settings.log_raw_text

        low_conf = self._low_confidence(request)
        n = request.num_candidates
        if low_conf:
            n = max(n, self.settings.auto_num_candidates)
        req = request.model_copy(update={"num_candidates": min(n, 3)})

        key = cache_key(req, low_conf)
        if self.cache is not None and (hit := self.cache.get(key)) is not None:
            resp = hit.model_copy(update={"cached": True, "latency_ms": round((time.perf_counter() - t0) * 1000, 2)})
            log_event("translate", log_raw_text=raw, request_id=rid, backend=resp.backend, cache_hit=True,
                      success=True, latency_ms=resp.latency_ms, n_glosses=len(req.glosses),
                      glosses=req.gloss_words(), sentence=resp.sentence)
            return resp

        fallback_reason: Optional[str] = None
        err: Optional[BaseException] = None
        try:
            if isinstance(self.primary, FallbackTranslator):
                result = self.primary.translate(req)
            else:
                result = self._call_primary(req)
            backend = self.primary.name
        except NotImplementedError as e:
            err, fallback_reason = e, "not_implemented"
        except TranslatorError as e:
            err, fallback_reason = e, e.reason
        except Exception as e:  # 예상 못 한 오류도 사용자에게는 fallback 문장을 준다
            err, fallback_reason = e, "unexpected_error"

        if fallback_reason is not None:
            result = self.fallback.translate(req)
            backend = f"fallback({fallback_reason})"

        st = req.sentence_type if req.sentence_type != SentenceType.unknown else result.sentence_type
        candidates = list(result.candidates) if req.num_candidates > 1 else []
        if candidates and candidates[0] != result.sentence:
            candidates = [result.sentence] + [c for c in candidates if c != result.sentence]

        latency = round((time.perf_counter() - t0) * 1000, 2)
        resp = TranslationResponse(
            sentence=result.sentence,
            candidates=candidates[: req.num_candidates],
            sentence_type=st,
            backend=backend,
            latency_ms=latency,
            cached=False,
            low_confidence=low_conf,
        )
        # fallback 결과는 캐시하지 않는다 (API가 회복되면 다시 시도해야 하므로)
        if self.cache is not None and fallback_reason is None:
            self.cache.put(key, resp)

        log_event("translate", log_raw_text=raw, request_id=rid, backend=backend, cache_hit=False,
                  success=fallback_reason is None, fallback_reason=fallback_reason, error=safe_error(err),
                  latency_ms=latency, n_glosses=len(req.glosses), n_candidates=len(resp.candidates),
                  glosses=req.gloss_words(), sentence=resp.sentence, candidates=resp.candidates)
        return resp


def build_service(settings: Optional[Settings] = None, examples: Optional[Sequence[Example]] = None,
                  backend: Optional[str] = None) -> TranslationService:
    settings = settings or load_settings()
    setup_logging(settings.log_level)
    primary = build_translator(settings, examples=examples, backend=backend)
    return TranslationService(settings, primary)
