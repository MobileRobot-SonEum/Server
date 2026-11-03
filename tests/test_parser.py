import pytest

from app.prompting.parser import parse_model_output
from app.schemas import GlossItem, SentenceType, TranslationRequest
from app.translators.api import ApiTranslator
from app.translators.base import TranslatorError
from tests.conftest import FakeProvider, OK_JSON


def test_parse_plain_json():
    r = parse_model_output(OK_JSON, num_candidates=2)
    assert r.sentence == "감기 때문에 진료를 받고 싶어요."
    assert r.candidates == ["감기 때문에 진료를 받고 싶어요.", "감기 진료를 원해요."]
    assert r.sentence_type == SentenceType.declarative


def test_parse_code_fence_and_chatter():
    text = '네, 번역입니다.\n```json\n{"sentence": "화장실이 어디예요?", "candidates": [], "sentence_type": "interrogative"}\n```'
    r = parse_model_output(text)
    assert r.sentence == "화장실이 어디예요?"
    assert r.candidates == ["화장실이 어디예요?"]


def test_parse_bad_type_inferred_from_question_mark():
    r = parse_model_output('{"sentence": "되나요?", "candidates": "x", "sentence_type": "question"}')
    assert r.sentence_type == SentenceType.interrogative
    assert r.candidates == ["되나요?"]


def test_parse_truncates_candidates():
    r = parse_model_output('{"sentence": "a", "candidates": ["a", "b", "c", "d"], "sentence_type": "declarative"}', 2)
    assert r.candidates == ["a", "b"]


@pytest.mark.parametrize("bad", ["", "not json", "{broken", "[1, 2]", '{"sentence": "", "candidates": []}'])
def test_parse_failure_raises(bad):
    with pytest.raises(TranslatorError) as e:
        parse_model_output(bad)
    assert e.value.reason == "parse_error"


def test_api_translator_retries_once_after_parse_error(settings, examples):
    provider = FakeProvider(["garbage", OK_JSON])
    t = ApiTranslator(provider, examples, settings.api)
    r = t.translate(TranslationRequest(glosses=[GlossItem(gloss="감기")]))
    assert r.sentence.startswith("감기")
    assert len(provider.calls) == 2


def test_api_translator_gives_up_after_retry(settings, examples):
    provider = FakeProvider(["garbage"])
    t = ApiTranslator(provider, examples, settings.api)
    with pytest.raises(TranslatorError):
        t.translate(TranslationRequest(glosses=[GlossItem(gloss="감기")]))
    assert len(provider.calls) == 2  # 1회 + 재시도 1회


def test_api_translator_does_not_retry_auth_error(settings, examples):
    provider = FakeProvider([TranslatorError("auth")])
    t = ApiTranslator(provider, examples, settings.api)
    with pytest.raises(TranslatorError):
        t.translate(TranslationRequest(glosses=[GlossItem(gloss="감기")]))
    assert len(provider.calls) == 1


def test_request_sentence_type_overrides_model(settings, examples):
    provider = FakeProvider([OK_JSON])  # 모델은 declarative라고 답함
    t = ApiTranslator(provider, examples, settings.api)
    r = t.translate(TranslationRequest(glosses=[GlossItem(gloss="감기")], sentence_type="interrogative"))
    assert r.sentence_type == SentenceType.interrogative
