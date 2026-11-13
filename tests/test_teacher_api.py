from .conftest import TEACHER_KEY, call, text_reply, tools_reply

H = {"X-API-Key": TEACHER_KEY}


def test_teacher_end_to_end(client, teacher_llm, executor):
    teacher_llm.script += [
        tools_reply(call("go_to", location="식당")),
        tools_reply(call("start_delivery", kind="snack", targets=["햇살반"])),
        text_reply("식당에서 햇살반 간식 배부를 시작했습니다. 알레르기: 가온(땅콩), 다솜(우유, 달걀)."),
    ]
    r = client.post("/agent/teacher", json={"instruction": "식당 가서 햇살반 간식 배부해 줘"}, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["model"] == "scripted-teacher"
    assert [s["name"] for s in body["trace"] if s["type"] == "tool"] == ["go_to", "start_delivery"]
    assert executor.current_location == "식당"
    # 교사 모드에는 아이 모드에서 막힌 도구도 제공되어야 한다
    offered = teacher_llm.tool_names_offered(0)
    assert {"go_to", "start_delivery", "get_child_info", "ask_teacher"} <= set(offered)


def test_teacher_validation_error(client):
    assert client.post("/agent/teacher", json={"instruction": ""}, headers=H).status_code == 422


def test_teacher_needs_teacher_response(client, teacher_llm):
    teacher_llm.script.append(tools_reply(call("ask_teacher", question="어느 반 간식인가요?")))
    body = client.post("/agent/teacher", json={"instruction": "간식 줘"}, headers=H).json()
    assert body["status"] == "needs_teacher" and body["question"] == "어느 반 간식인가요?"
