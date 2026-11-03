from fastapi.testclient import TestClient

from app.main import create_app
from app.service import TranslationService
from app.translators.api import ApiTranslator
from app.translators.base import TranslatorError
from tests.conftest import FakeProvider, OK_JSON


def make_client(settings, examples, outputs):
    svc = TranslationService(settings, ApiTranslator(FakeProvider(outputs), examples, settings.api))
    return TestClient(create_app(svc))


def test_health(settings, examples):
    with make_client(settings, examples, [OK_JSON]) as c:
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "backend": "api:fake:fake-model", "fallback": "fallback"}


def test_translate_ok(settings, examples):
    with make_client(settings, examples, [OK_JSON]) as c:
        r = c.post("/translate", json={
            "glosses": [{"gloss": "나"}, {"gloss": "감기"}, {"gloss": "진료"}, {"gloss": "원하다", "confidence": 0.95}],
            "sentence_type": "declarative",
            "scenario": "hospital",
            "num_candidates": 2,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["sentence"] == "감기 때문에 진료를 받고 싶어요."
        assert len(body["candidates"]) == 2
        assert body["sentence_type"] == "declarative"
        assert body["backend"] == "api:fake:fake-model"
        assert body["latency_ms"] >= 0


def test_translate_fallback_on_timeout(settings, examples):
    with make_client(settings, examples, [TranslatorError("timeout")]) as c:
        r = c.post("/translate", json={"glosses": [{"gloss": "화장실"}, {"gloss": "어디"}]})
        assert r.status_code == 200
        assert r.json()["backend"] == "fallback(timeout)"
        assert r.json()["sentence"] == "화장실 어디?"


def test_translate_with_context(settings, examples):
    with make_client(settings, examples, [OK_JSON]) as c:
        r = c.post("/translate", json={
            "glosses": [{"gloss": "신분증"}, {"gloss": "없다"}],
            "context": [{"speaker": "staff", "text": "신분증 보여주시겠어요?"}],
        })
        assert r.status_code == 200


def test_validation_errors(settings, examples):
    with make_client(settings, examples, [OK_JSON]) as c:
        assert c.post("/translate", json={"glosses": []}).status_code == 422
        assert c.post("/translate", json={"glosses": [{"gloss": "a"}], "num_candidates": 4}).status_code == 422
        assert c.post("/translate", json={"glosses": [{"gloss": "a", "confidence": 1.5}]}).status_code == 422
        assert c.post("/translate", json={"glosses": [{"gloss": "a"}], "scenario": "bank"}).status_code == 422
