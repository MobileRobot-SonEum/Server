"""ApiTranslator용 프롬프트 생성."""
from __future__ import annotations

import json
from typing import Sequence

from app.prompting.retriever import Example
from app.schemas import Scenario, SentenceType, TranslationRequest

SYSTEM_PROMPT = """당신은 한국수어(KSL) gloss 나열을 자연스러운 한국어 문장으로 옮기는 통역 보조입니다.
병원 접수, 주민센터, 대학 행정실 같은 창구에서 수어 사용자가 직원에게 하는 말을 번역합니다.
번역 결과는 곧바로 음성으로 읽혀 직원에게 전달됩니다.

[한국수어 문법 특징]
- 조사(이/가, 을/를, 에서, 으로 등)가 없습니다. 문맥에 맞게 보충하세요.
- 어순이 한국어와 다를 수 있습니다. 주제·시간·장소가 앞에 오고 서술어가 뒤에 오는 경향이 있습니다.
- 시제와 어미는 별도 gloss나 맥락으로 나타나며, gloss는 기본형(예: 원하다, 없다)으로 적혀 있습니다.
- 의문문은 손이 아니라 눈썹·고개 같은 비수지 신호로 표시됩니다. 그래서 gloss만으로는 평서문인지 의문문인지 알 수 없을 수 있습니다.
  요청에 문장 유형이 주어지면 반드시 따르고, '알 수 없음'이면 gloss와 대화 맥락으로 판단하세요.
- '나' 같은 1인칭 gloss는 한국어에서 자연스럽게 생략해도 됩니다.

[규칙]
1. gloss에 없는 사실을 절대 추가하지 마세요. 날짜, 시간, 증상, 이름, 숫자, 장소, 서류명, 이유 등이 해당합니다.
   허용되는 것은 조사, 어미, 연결 표현(~때문에, ~는데, ~해서), 어순 조정, 1인칭 생략뿐입니다.
2. 창구 대화에 맞는 존댓말 해요체로 쓰세요. (예: "~하고 싶어요", "~해도 되나요?")
3. 대화 맥락은 생략된 대상을 이해하는 데만 쓰세요. 맥락에 있던 내용을 문장에 새로 끼워 넣지 마세요.
4. '(불확실)' 표시가 붙은 gloss는 인식이 잘못됐을 수 있습니다. 후보를 여러 개 요청받으면 그 gloss의 해석이나 문장 구조가 서로 다른 후보를 만드세요.
5. 의문문이면 문장을 물음표로 끝내세요.
6. 출력은 아래 형식의 JSON 객체 하나만 내보내세요. 설명이나 코드블록은 넣지 마세요.
{"sentence": "가장 자연스러운 문장", "candidates": ["후보1", "후보2"], "sentence_type": "declarative 또는 interrogative"}
candidates의 첫 번째 항목은 sentence와 같아야 하고, 요청한 개수만큼 서로 다른 문장을 넣으세요."""

_SCENARIO_KO = {
    Scenario.hospital: "병원 접수",
    Scenario.civil_office: "주민센터 민원 창구",
    Scenario.university: "대학 행정실",
    Scenario.general: "일반 창구",
}
_TYPE_KO = {
    SentenceType.declarative: "평서문",
    SentenceType.interrogative: "의문문",
    SentenceType.unknown: "알 수 없음 (직접 판단)",
}
_SPEAKER_KO = {"staff": "직원", "user": "수어 사용자"}


def format_example(ex: Example) -> str:
    out = {"sentence": ex.sentence, "candidates": [ex.sentence], "sentence_type": ex.type}
    return f"gloss: {' / '.join(ex.glosses)}\n출력: {json.dumps(out, ensure_ascii=False)}"


def build_user_prompt(
    request: TranslationRequest,
    examples: Sequence[Example],
    *,
    num_candidates: int,
    low_conf_threshold: float,
) -> str:
    parts = []
    if examples:
        parts.append("[예시]\n" + "\n\n".join(format_example(e) for e in examples))

    parts.append(f"[상황] {_SCENARIO_KO[request.scenario]}")
    if request.context:
        lines = [f"- {_SPEAKER_KO.get(t.speaker, t.speaker)}: {t.text}" for t in request.context]
        parts.append("[직전 대화]\n" + "\n".join(lines))

    gloss_strs = []
    for g in request.glosses:
        s = g.gloss.strip()
        if g.confidence is not None and g.confidence < low_conf_threshold:
            s += "(불확실)"
        gloss_strs.append(s)

    parts.append(
        "[번역할 입력]\n"
        f"gloss: {' / '.join(gloss_strs)}\n"
        f"문장 유형: {_TYPE_KO[request.sentence_type]}\n"
        f"후보 수: {num_candidates}"
    )
    return "\n\n".join(parts)
