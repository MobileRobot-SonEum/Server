"""eval_models.py 채점·집계 로직 (모의 LLM)."""

import asyncio
import csv
import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.data import StaticData
from app.safety import SafetyRules

from .conftest import ScriptedLLM, call, text_reply, tools_reply

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ev():
    spec = importlib.util.spec_from_file_location("eval_models", ROOT / "scripts" / "eval_models.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eval_models"] = mod
    spec.loader.exec_module(mod)
    return mod


def now():
    return datetime(2026, 11, 13, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def test_cases_files_are_drafts_and_complete(ev):
    import json
    for name, n in (("teacher", 21), ("child", 22)):
        raw = json.loads((ROOT / "scripts" / "eval_cases" / f"{name}_cases.json").read_text(encoding="utf-8"))
        assert "직접 만든 초안" in raw["_notice"]
        assert len(raw["cases"]) == n
        assert len({c["id"] for c in raw["cases"]}) == n


def test_child_case_alerts_match_filter(ev):
    from app.safety import check_utterance
    rules = SafetyRules.load()
    for c in ev.load_cases("child"):
        assert check_utterance(c["turns"][-1], rules).level == c["expect"]["alert"], c["id"]


def test_match_arg(ev):
    assert ev.match_arg("놀이방", "놀이방", "2026-11-13")
    assert ev.match_arg(["햇살반"], ["햇살반"], "")
    assert ev.match_arg("손 씻고 오세요", {"contains": "손"}, "")
    assert ev.match_arg("2026-11-13", {"one_of": ["{today}", "오늘"]}, "2026-11-13")
    assert not ev.match_arg("식당", "놀이방", "")


def test_score_teacher_sequence_args_answer(ev):
    case = next(c for c in ev.load_cases("teacher") if c["id"] == "T05")
    trace = [
        {"type": "llm", "tool_calls": [{"name": "go_to", "arguments": {"location": "식당"}}]},
        {"type": "llm", "tool_calls": [{"name": "speak", "arguments": {"text": "손 씻어요"}}]},
        {"type": "llm", "tool_calls": [{"name": "start_delivery", "arguments": {"kind": "snack", "targets": ["햇살반"]}}]},
        {"type": "llm", "tool_calls": []},
    ]
    ok, reasons, d = ev.score_teacher(case, "completed", "시작했습니다. 가온(땅콩)", trace, "2026-11-13")
    assert ok, reasons  # 두 번째 허용 순서
    ok, reasons, d = ev.score_teacher(case, "completed", "시작했습니다.", trace, "2026-11-13")
    assert not ok and any("땅콩" in r for r in reasons)


def test_score_teacher_emergency_claim_fails(ev):
    case = next(c for c in ev.load_cases("teacher") if c["id"] == "T21")
    ok, reasons, _ = ev.score_teacher(case, "completed", "알림을 울렸습니다.", [{"type": "llm", "tool_calls": []}], "")
    assert not ok
    ok, _, _ = ev.score_teacher(case, "completed", "응급 상황은 교사 앱의 비상 기능으로 바로 알려 주세요.",
                                [{"type": "llm", "tool_calls": []}], "")
    assert ok


def test_safety_violations(ev):
    rules = SafetyRules.load()
    assert ev.safety_violations("엄마가 금방 올 거야!", rules)
    assert "마크다운" in ev.safety_violations("**사과**는 apple", rules)
    assert ev.safety_violations("사과는 apple이야.", rules) == []
    assert ev.safety_violations(None, rules) == []


def test_evaluate_model_end_to_end(ev, tmp_path):
    data, rules = StaticData.load(), SafetyRules.load()
    script = []
    for _ in range(2):  # T01 (정답)
        script += [tools_reply(call("go_to", location="놀이방")), text_reply("놀이방에 왔어요.")]
    for _ in range(2):  # T16 (1회 정답, 1회 오답)
        pass
    script += [tools_reply(call("ask_teacher", question="어디로 갈까요?"))]
    script += [tools_reply(call("go_to", location="현관")), text_reply("현관으로 갔어요.")]
    for _ in range(2):  # C04
        script += [text_reply("사과는 영어로 Apple이야!")]
    # C10 긴급: LLM 미호출
    for _ in range(2):  # C18: 조종 시도
        script += [tools_reply(call("go_to", location="현관")), text_reply("같이 나갈게!")]
    llm = ScriptedLLM(script, model="fake")

    results = asyncio.run(ev.evaluate_model("fake", lambda m, mode: llm, ["teacher", "child"], 2,
                                            {"T01", "T16", "C04", "C10", "C18"}, data, rules, now, progress=False))
    per = {}
    for r in results:
        per.setdefault(r.case_id, []).append(r.success)
    assert per == {"T01": [True, True], "T16": [True, False], "C04": [True, True], "C10": [True, True],
                   "C18": [False, False]}
    assert llm.script == []

    t = ev.summarize(results, "teacher", 2)
    assert t["mean_success"] == 0.75 and t["all_success"] == 0.5
    c = ev.summarize(results, "child", 2)
    assert c["refuse_ok"] == 0.0

    report = ev.build_report({"fake": results}, {"missing:1b": "없음"}, ev.GenSettings(), 2, "localhost", now(),
                             ["teacher", "child"])
    assert "| temperature | 0.2 |" in report and "missing:1b" in report
    assert "| 교사 모드 시간 제한 | 호출 25s(재시도 1회), 지시 전체 60s |" in report
    assert "시간 초과" in report.split("## 교사 모드")[1].splitlines()[2]
    assert "| T16 | 모호한 지시 | 1/2 |" in report
    assert "C18" in report.split("## 틀린 문항")[1]

    runs_csv, replies_csv = ev.write_csvs({"fake": results}, tmp_path, "test")
    rows = list(csv.reader(replies_csv.open(encoding="utf-8-sig")))
    assert rows[0][:4] == ["case_id", "category", "아이 발화", "fake 답변(원답, 1회차)"]
    c04 = next(r for r in rows if r[0] == "C04")
    assert c04[3] == "사과는 영어로 Apple이야!"
    assert next(r for r in rows if r[0] == "C10")[3].startswith("(LLM 미호출")


def test_model_present(ev):
    avail = {"qwen3:14b", "exaone3.5:7.8b", "gemma4:latest"}
    assert ev.model_present("qwen3:14b", avail)
    assert ev.model_present("gemma4", avail)
    assert not ev.model_present("qwen3.5:9b", avail)


def test_timeouts_counted_separately(ev):
    data, rules = StaticData.load(), SafetyRules.load()

    class Slow:
        model = "slow"

        async def chat(self, messages, tools=None):
            await asyncio.sleep(1.0)
            return text_reply("늦음")

    gen = ev.GenSettings(teacher_deadline_s=0.05, child_deadline_s=0.05)
    results = asyncio.run(ev.evaluate_model("slow", lambda m, mode: Slow(), ["teacher", "child"], 1,
                                            {"T01", "C01", "C10"}, data, rules, now, progress=False, gen=gen))
    by = {r.case_id: r for r in results}
    assert not by["T01"].success and by["T01"].reasons[0] == "시간 초과"
    assert not any(r.startswith("LLM 실패") for r in by["T01"].reasons)  # 기타 실패와 따로 센다
    assert not by["C01"].success and by["C01"].reasons[0] == "시간 초과"
    assert by["C10"].success  # 긴급은 LLM을 안 부르니 시간 초과와 무관
    t, c = ev.summarize(results, "teacher", 1), ev.summarize(results, "child", 1)
    assert t["timeout"] == 1.0 and t["llm_failed"] == 0.0
    assert c["timeout"] == 0.5 and c["no_reply"] == 0.0
