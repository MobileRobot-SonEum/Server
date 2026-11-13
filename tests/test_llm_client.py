"""OpenAICompatClient: httpx.MockTransport로 서버 응답을 흉내 낸다."""

import asyncio
import json

import httpx
import pytest

from app.config import LLMSettings
from app.llm import LLMError, OpenAICompatClient, ToolFormatError
from app.llm.json_fallback import parse_json_action, strip_think, to_text_messages

TOOLS = [{"type": "function", "function": {"name": "go_to", "description": "이동",
                                           "parameters": {"type": "object", "properties": {"location": {"type": "string"}}}}}]
MSGS = [{"role": "system", "content": "sys"}, {"role": "user", "content": "식당 가"}]


def completion(message: dict) -> dict:
    return {"model": "m", "choices": [{"index": 0, "message": {"role": "assistant", **message}}]}


def make_client(handler, **cfg):
    seen: list[dict] = []

    def wrapped(request: httpx.Request):
        body = json.loads(request.content) if request.content else {}
        seen.append(body)
        return handler(request, body, len(seen))

    http = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    client = OpenAICompatClient(LLMSettings(base_url="http://localhost:11434/v1", model="m", **cfg),
                                http=http, retry_backoff_s=0)
    return client, seen


def test_native_tool_call_parsed_and_echoed():
    def h(req, body, n):
        assert req.url.path == "/v1/chat/completions"
        return httpx.Response(200, json=completion({"content": None, "tool_calls": [{
            "id": "abc", "type": "function", "extra_content": {"google": {"thought_signature": "sig"}},
            "function": {"name": "go_to", "arguments": json.dumps({"location": "식당"})}}]}))
    c, seen = make_client(h)
    r = asyncio.run(c.chat(MSGS, TOOLS))
    assert [(t.name, t.arguments) for t in r.tool_calls] == [("go_to", {"location": "식당"})]
    assert not r.used_json_fallback
    assert seen[0]["tools"] == TOOLS
    # 제공자가 붙인 필드(thought signature)를 다음 요청용 메시지에 보존
    assert r.assistant_message["tool_calls"][0]["extra_content"] == {"google": {"thought_signature": "sig"}}


def test_name_field_stripped_and_reasoning_effort_sent():
    c, seen = make_client(lambda req, body, n: httpx.Response(200, json=completion({"content": "ok"})),
                          reasoning_effort="none")
    msgs = MSGS + [{"role": "tool", "tool_call_id": "x", "name": "go_to", "content": "{}"}]
    asyncio.run(c.chat(msgs))
    assert all("name" not in m for m in seen[0]["messages"])
    assert seen[0]["reasoning_effort"] == "none"


def test_reasoning_effort_omitted_by_default():
    c, seen = make_client(lambda req, body, n: httpx.Response(200, json=completion({"content": "ok"})))
    asyncio.run(c.chat(MSGS))
    assert "reasoning_effort" not in seen[0]


def test_think_tags_stripped():
    c, _ = make_client(lambda req, body, n: httpx.Response(200, json=completion({"content": "<think>음...</think>안녕"})))
    assert asyncio.run(c.chat(MSGS)).content == "안녕"


def test_broken_native_arguments_fall_back_to_json_mode():
    def h(req, body, n):
        if n == 1:
            return httpx.Response(200, json=completion({"tool_calls": [{"id": "1", "type": "function",
                                  "function": {"name": "go_to", "arguments": "{location: 식당"}}]}))
        assert "tools" not in body  # JSON 방식은 tools 없이 프롬프트로 보낸다
        assert "[도구 사용 형식]" in body["messages"][0]["content"]
        return httpx.Response(200, json=completion({"content": '{"tool_calls":[{"name":"go_to","arguments":{"location":"식당"}}]}'}))
    c, seen = make_client(h)
    r = asyncio.run(c.chat(MSGS, TOOLS))
    assert r.used_json_fallback and r.tool_calls[0].arguments == {"location": "식당"}
    assert len(seen) == 2


def test_broken_native_arguments_raise_in_native_mode():
    def h(req, body, n):
        return httpx.Response(200, json=completion({"tool_calls": [{"id": "1", "type": "function",
                              "function": {"name": "go_to", "arguments": "{bad"}}]}))
    c, _ = make_client(h, tool_mode="native")
    with pytest.raises(ToolFormatError):
        asyncio.run(c.chat(MSGS, TOOLS))


def test_tools_unsupported_switches_to_json_and_remembers():
    def h(req, body, n):
        if "tools" in body:
            return httpx.Response(400, json={"error": {"message": "registry.ollama.ai/library/exaone3.5:7.8b does not support tools"}})
        return httpx.Response(200, json=completion({"content": '```json\n{"final": "다 했어요"}\n```'}))
    c, seen = make_client(h)
    r1 = asyncio.run(c.chat(MSGS, TOOLS))
    assert r1.content == "다 했어요" and r1.used_json_fallback
    asyncio.run(c.chat(MSGS, TOOLS))
    assert ["tools" in b for b in seen] == [True, False, False]  # 두 번째 호출부터는 바로 JSON


def test_tool_call_written_in_content_is_recovered():
    c, _ = make_client(lambda req, body, n: httpx.Response(200, json=completion(
        {"content": '{"name": "go_to", "arguments": {"location": "현관"}}'})))
    r = asyncio.run(c.chat(MSGS, TOOLS))
    assert r.tool_calls[0].name == "go_to" and r.content is None


def test_timeout_retried_once_then_error():
    def h(req, body, n):
        raise httpx.ReadTimeout("slow", request=req)
    c, seen = make_client(h)
    with pytest.raises(LLMError) as ei:
        asyncio.run(c.chat(MSGS))
    assert ei.value.kind == "timeout" and len(seen) == 2


def test_5xx_then_success():
    def h(req, body, n):
        return httpx.Response(503, text="busy") if n == 1 else httpx.Response(200, json=completion({"content": "ok"}))
    c, seen = make_client(h)
    assert asyncio.run(c.chat(MSGS)).content == "ok" and len(seen) == 2


def test_4xx_not_retried():
    c, seen = make_client(lambda req, body, n: httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(LLMError) as ei:
        asyncio.run(c.chat(MSGS))
    assert ei.value.status == 401 and "bad key" in ei.value.message and len(seen) == 1


def test_connection_error():
    def h(req, body, n):
        raise httpx.ConnectError("refused", request=req)
    c, seen = make_client(h)
    with pytest.raises(LLMError) as ei:
        asyncio.run(c.chat(MSGS))
    assert ei.value.kind == "connection" and len(seen) == 2


def test_missing_choices_is_bad_response():
    c, _ = make_client(lambda req, body, n: httpx.Response(200, json={"oops": 1}))
    with pytest.raises(LLMError) as ei:
        asyncio.run(c.chat(MSGS))
    assert ei.value.kind == "bad_response"


# --- JSON 대체 경로 파서 ---

def test_parse_json_action_variants():
    assert parse_json_action('{"final": "끝"}') == ("끝", [])
    final, calls = parse_json_action('알겠습니다.\n{"tool_calls": [{"name": "speak", "arguments": "{\\"text\\": \\"안녕\\"}"}]} 이상')
    assert final is None and calls[0].arguments == {"text": "안녕"}
    _, calls = parse_json_action('<think>생각</think>{"tool_calls": {"name": "get_locations"}}')
    assert calls[0].name == "get_locations" and calls[0].arguments == {}


@pytest.mark.parametrize("bad", ["", "그냥 문장", "[1,2]", '{"foo": 1}', '{"tool_calls": []}',
                                 '{"tool_calls": [{"arguments": {}}]}'])
def test_parse_json_action_errors(bad):
    with pytest.raises(ToolFormatError):
        parse_json_action(bad)


def test_strip_think_unclosed():
    assert strip_think("답<think>끝나지 않은 생각") == "답"


def test_to_text_messages_converts_tool_roles():
    msgs = MSGS + [
        {"role": "assistant", "tool_calls": [{"id": "1", "type": "function",
                                              "function": {"name": "go_to", "arguments": '{"location": "식당"}'}}]},
        {"role": "tool", "tool_call_id": "1", "name": "go_to", "content": '{"ok": true}'},
    ]
    out = to_text_messages(msgs, TOOLS)
    assert {m["role"] for m in out} <= {"system", "user", "assistant"}
    assert json.loads(out[2]["content"]) == {"tool_calls": [{"name": "go_to", "arguments": {"location": "식당"}}]}
    assert out[3]["content"].startswith("[도구 결과: go_to]")


def test_reasoning_effort_dropped_when_model_rejects_it():
    def h(req, body, n):
        if "reasoning_effort" in body:
            return httpx.Response(400, json={"error": {"message": '"exaone3.5:7.8b" does not support thinking'}})
        return httpx.Response(200, json=completion({"content": "ok"}))
    c, seen = make_client(h, reasoning_effort="none")
    assert asyncio.run(c.chat(MSGS)).content == "ok"
    asyncio.run(c.chat(MSGS))
    assert ["reasoning_effort" in b for b in seen] == [True, False, False]
    assert c.reasoning_dropped
