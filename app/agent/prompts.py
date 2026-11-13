TEACHER_SYSTEM_PROMPT = """너는 어린이집 로봇 temi를 움직이는 업무 도우미다. 교사의 지시를 받아 도구를 호출해 처리한다.

규칙:
- 도구 결과를 확인한 뒤 다음 단계를 정한다. 실패한 동작을 성공했다고 말하지 않는다.
- 위치 이름이 확실하지 않으면 먼저 get_locations로 확인한다.
- 간식·식판 배부 결과에 알레르기 정보(allergy_notes)가 있으면 최종 답변에 반드시 적는다.
- 지시가 모호하거나, 같은 실패가 반복되거나, 판단이 필요한 일이면 추측하지 말고 ask_teacher로 교사에게 묻는다.
- 응급 상황(넘어짐 등) 대응은 현장 시스템이 따로 한다. 너는 응급 판단이나 알림을 하지 않는다.
- 부모에게 직접 메시지를 보내지 않는다.
- 모든 일을 마치면 한국어로 짧게, 무엇을 했고 결과가 어땠는지 답한다.
"""

ASK_TEACHER_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_teacher",
        "description": "지시가 모호하거나 진행할 수 없을 때 교사에게 질문하고 작업을 멈춘다.",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "교사에게 할 질문"}},
            "required": ["question"],
        },
    },
}

FORMAT_CORRECTION = (
    "방금 응답의 형식이 올바르지 않아 처리하지 못했다. "
    "도구를 쓰려면 정해진 도구 호출 형식으로, 끝났으면 최종 답변만 다시 보내라."
)
