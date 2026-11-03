from app.prompting.retriever import load_examples, select_examples
from app.prompting.system_prompt import SYSTEM_PROMPT, build_user_prompt
from app.config import ROOT
from app.schemas import GlossItem, Scenario, SentenceType, TranslationRequest
from app.translators.api import ApiTranslator
from tests.conftest import FakeProvider, OK_JSON


def test_select_examples_prefers_overlap(examples):
    picked = select_examples(["신분증", "없다", "되다"], examples, k=2)
    assert picked[0].glosses == ("신분증", "없다", "휴대폰", "되다")
    assert len(picked) == 2


def test_select_examples_scenario_bonus_breaks_ties(examples):
    picked = select_examples(["얼마"], examples, k=1, scenario="civil_office")
    assert picked[0].sentence == "수수료가 얼마예요?"


def test_select_examples_k_zero(examples):
    assert select_examples(["감기"], examples, k=0) == []


def test_user_prompt_marks_low_confidence_and_type(examples):
    req = TranslationRequest(
        glosses=[GlossItem(gloss="신분증", confidence=0.9), GlossItem(gloss="없다", confidence=0.3)],
        sentence_type=SentenceType.interrogative,
        scenario=Scenario.hospital,
        context=[{"speaker": "staff", "text": "신분증 보여주시겠어요?"}],
    )
    p = build_user_prompt(req, examples[:1], num_candidates=3, low_conf_threshold=0.6)
    assert "없다(불확실)" in p
    assert "신분증(불확실)" not in p
    assert "의문문" in p
    assert "후보 수: 3" in p
    assert "직원: 신분증 보여주시겠어요?" in p
    assert "병원 접수" in p
    assert "감기 때문에 진료를 받고 싶어요." in p  # few-shot 예시 포함


def test_system_prompt_has_core_rules():
    for phrase in ["조사", "비수지", "추가하지 마세요", "해요체", "JSON"]:
        assert phrase in SYSTEM_PROMPT


def test_api_translator_puts_selected_examples_in_prompt(settings, examples):
    provider = FakeProvider([OK_JSON])
    t = ApiTranslator(provider, examples, settings.api)
    req = TranslationRequest(glosses=[GlossItem(gloss=g) for g in ["화장실", "어디"]])
    t.translate(req)
    user = provider.calls[0]["user"]
    assert "화장실이 어디예요?" in user


def test_examples_file_loads_and_is_marked_manual():
    path = ROOT / "data" / "examples.jsonl"
    exs = load_examples(path)
    assert len(exs) >= 20
    assert "직접 작성한 임시 예시" in path.read_text(encoding="utf-8").splitlines()[0]
    assert all(e.type in {"declarative", "interrogative"} for e in exs)
