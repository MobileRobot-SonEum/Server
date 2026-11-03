import logging

from app.schemas import GlossItem, SentenceType, TranslationRequest
from app.service import TranslationService
from app.translators.api import ApiTranslator
from app.translators.base import TranslatorError
from app.translators.fallback import FallbackTranslator
from app.translators.kogpt2 import KoGPT2Translator
from tests.conftest import FakeProvider, OK_JSON


def req(*glosses, **kw):
    return TranslationRequest(glosses=[GlossItem(gloss=g) for g in glosses], **kw)


def test_fallback_rules():
    t = FallbackTranslator()
    r = t.translate(req("신분증", "없다", "휴대폰", "되다", sentence_type="interrogative"))
    assert r.sentence == "신분증 없다 휴대폰 돼요?"
    r = t.translate(req("나", "감기", "진료", "원하다"))
    assert r.sentence == "감기 진료 원해요."
    r = t.translate(req("화장실", "어디"))
    assert r.sentence_type == SentenceType.interrogative


def test_timeout_switches_to_fallback(settings, examples):
    provider = FakeProvider([TranslatorError("timeout")])
    svc = TranslationService(settings, ApiTranslator(provider, examples, settings.api))
    resp = svc.translate(req("감기", "진료", "원하다"))
    assert resp.backend == "fallback(timeout)"
    assert resp.sentence == "감기 진료 원해요."
    assert len(provider.calls) == 2  # 재시도 1회 후 fallback


def test_slow_provider_hits_hard_deadline(settings, examples):
    # 제공자가 타임아웃을 무시하고 오래 걸려도 서비스 마감 시간에 fallback으로 넘어가야 한다.
    settings.api.total_deadline_s = 0.3
    provider = FakeProvider([OK_JSON], delay_s=2.0)
    svc = TranslationService(settings, ApiTranslator(provider, examples, settings.api))
    resp = svc.translate(req("감기"))
    assert resp.backend == "fallback(deadline_exceeded)"
    assert resp.latency_ms < 1500


def test_stub_backend_falls_back(settings):
    svc = TranslationService(settings, KoGPT2Translator())
    resp = svc.translate(req("감기"))
    assert resp.backend == "fallback(not_implemented)"


def test_fallback_result_not_cached(settings, examples):
    provider = FakeProvider([TranslatorError("timeout"), TranslatorError("timeout"), OK_JSON])
    svc = TranslationService(settings, ApiTranslator(provider, examples, settings.api))
    assert svc.translate(req("감기")).backend.startswith("fallback")
    second = svc.translate(req("감기"))
    assert second.backend == "api:fake:fake-model"
    assert second.cached is False


def test_cache_hit(settings, examples):
    provider = FakeProvider([OK_JSON])
    svc = TranslationService(settings, ApiTranslator(provider, examples, settings.api))
    a = svc.translate(req("감기"))
    b = svc.translate(req("감기"))
    assert not a.cached and b.cached
    assert b.sentence == a.sentence
    assert len(provider.calls) == 1


def test_low_confidence_triggers_candidates(settings, examples):
    provider = FakeProvider([OK_JSON])
    svc = TranslationService(settings, ApiTranslator(provider, examples, settings.api))
    r = TranslationRequest(glosses=[GlossItem(gloss="감기", confidence=0.9), GlossItem(gloss="진료", confidence=0.2)])
    resp = svc.translate(r)
    assert resp.low_confidence == ["진료"]
    assert len(resp.candidates) == 2
    assert "후보 수: 3" in provider.calls[0]["user"]


def test_single_candidate_returns_empty_list(settings, examples):
    svc = TranslationService(settings, ApiTranslator(FakeProvider([OK_JSON]), examples, settings.api))
    assert svc.translate(req("감기")).candidates == []


def test_logs_do_not_contain_raw_text(settings, examples, caplog):
    from app.logging_utils import logger

    logger.propagate = True
    try:
        svc = TranslationService(settings, ApiTranslator(FakeProvider([OK_JSON]), examples, settings.api))
        with caplog.at_level(logging.INFO, logger="translator"):
            svc.translate(req("감기", "진료"))
        text = caplog.text
        assert "latency_ms" in text and "backend" in text
        assert "감기" not in text and "진료" not in text
    finally:
        logger.propagate = False


def test_raw_log_opt_in(settings, examples, caplog):
    from app.logging_utils import logger

    settings.log_raw_text = True
    logger.propagate = True
    try:
        svc = TranslationService(settings, ApiTranslator(FakeProvider([OK_JSON]), examples, settings.api))
        with caplog.at_level(logging.INFO, logger="translator"):
            svc.translate(req("감기"))
        assert "감기" in caplog.text
    finally:
        logger.propagate = False
