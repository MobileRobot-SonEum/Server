"""실제 uvicorn 서버 + 가짜 temi(scripts/fake_temi.py)로 /ws/temi 전체 흐름 확인."""

import asyncio
import importlib.util
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from websockets.exceptions import ConnectionClosed, InvalidStatus

from app.config import LLMSettings, Settings
from app.main import create_app

from .conftest import TEACHER_KEY, TEMI_KEY, ScriptedLLM, call, text_reply, tools_reply

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fake_temi", ROOT / "scripts" / "fake_temi.py")
fake_temi = importlib.util.module_from_spec(spec)
sys.modules["fake_temi"] = fake_temi
spec.loader.exec_module(fake_temi)
FakeTemi = fake_temi.FakeTemi


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    def __init__(self, **settings_kw):
        self.port = free_port()
        self.teacher_llm = ScriptedLLM(model="scripted-teacher")
        self.child_llm = ScriptedLLM(model="scripted-child")
        settings = Settings(
            _env_file=None,
            teacher_llm=LLMSettings(model="t"), child_llm=LLMSettings(model="c"),
            teacher_app_api_key=TEACHER_KEY, temi_api_key=TEMI_KEY, rule_engine_api_key="rule",
            db_path=":memory:", log_level="WARNING",
            **({"control_ack_timeout_s": 1.0, "robot_executor": "control"} | settings_kw),
        )
        self.app = create_app(settings, teacher_llm=self.teacher_llm, child_llm=self.child_llm)
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning",
                                                    lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def http(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws/temi"

    def __enter__(self):
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                return self
            time.sleep(0.02)
        raise RuntimeError("서버가 시작되지 않음")

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(5)


@pytest.fixture
def live():
    with LiveServer() as s:
        yield s


async def start_temi(live, device="temi-a", **kw) -> tuple[FakeTemi, asyncio.Task]:
    temi = FakeTemi(live.ws, TEMI_KEY, device, **kw)
    task = asyncio.create_task(temi.run_once())
    await asyncio.wait_for(temi.connected.wait(), 5)
    return temi, task


async def stop_temi(temi, task):
    await temi.close()
    try:
        await asyncio.wait_for(task, 5)
    except Exception:
        pass


async def wait_job(client, job_id, statuses, timeout=5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        r = await client.get(f"/robot/jobs/{job_id}", headers={"X-API-Key": TEACHER_KEY})
        if r.status_code == 200 and r.json()["status"] in statuses:
            return r.json()
        await asyncio.sleep(0.05)
    raise AssertionError(f"작업 {job_id}가 {statuses}이 되지 않음: {r.json()}")


def test_wrong_key_rejected_at_handshake(live):
    async def scenario():
        for key, device in [("wrong", "temi-a"), (TEACHER_KEY, "temi-a"), (TEMI_KEY, ""), (TEMI_KEY, "bad id!")]:
            temi = FakeTemi(live.ws, key, device)
            with pytest.raises(InvalidStatus) as ei:
                await temi.run_once()
            assert ei.value.response.status_code == 403
    asyncio.run(scenario())


def test_command_ack_result_end_to_end(live):
    async def scenario():
        temi, task = await start_temi(live, delay_s=1.5)
        live.teacher_llm.script += [tools_reply(call("go_to", location="놀이방")), text_reply("놀이방으로 이동을 시작했습니다.")]
        async with httpx.AsyncClient(base_url=live.http) as client:
            health = (await client.get("/health")).json()
            assert health["executor"] == "control" and health["temi_connected"] == ["temi-a"]

            t0 = time.monotonic()
            r = await client.post("/agent/teacher", json={"instruction": "놀이방으로 가"}, headers={"X-API-Key": TEACHER_KEY})
            elapsed = time.monotonic() - t0
            body = r.json()
            assert body["status"] == "completed"
            assert elapsed < 1.2  # 로봇 동작 완료(1.5초)를 기다리지 않음
            tool = next(s for s in body["trace"] if s["type"] == "tool")
            assert tool["ok"] and tool["result"]["status"] == "started"
            job_id = tool["result"]["job_id"]
            assert temi.received[0]["id"] == job_id and temi.received[0]["args"] == {"location": "놀이방"}

            job = await wait_job(client, job_id, {"completed"})
            assert job["action"] == "go_to" and job["source_mode"] == "teacher" and job["finished_at"]
            jobs = (await client.get("/robot/jobs", headers={"X-API-Key": TEACHER_KEY})).json()
            assert [j["id"] for j in jobs] == [job_id]
            devices = (await client.get("/robot/devices", headers={"X-API-Key": TEACHER_KEY})).json()
            assert devices[0]["device_id"] == "temi-a" and devices[0]["app_version"] == "fake-0.1"
        await stop_temi(temi, task)
    asyncio.run(scenario())


def test_not_connected_tells_teacher(live):
    async def scenario():
        live.teacher_llm.script.append(tools_reply(call("speak", text="정리할 시간이에요")))
        async with httpx.AsyncClient(base_url=live.http) as client:
            body = (await client.post("/agent/teacher", json={"instruction": "정리하라고 말해"},
                                      headers={"X-API-Key": TEACHER_KEY})).json()
        assert body["status"] == "needs_teacher" and "연결되어 있지 않아요" in body["question"]
        assert len(live.teacher_llm.requests) == 1  # 더 진행하지 않고 바로 교사에게
    asyncio.run(scenario())


def test_ack_timeout(live):
    async def scenario():
        temi, task = await start_temi(live, no_ack={"speak"})
        live.teacher_llm.script.append(tools_reply(call("speak", text="안녕")))
        async with httpx.AsyncClient(base_url=live.http) as client:
            body = (await client.post("/agent/teacher", json={"instruction": "인사해"},
                                      headers={"X-API-Key": TEACHER_KEY})).json()
            assert body["status"] == "needs_teacher" and "응답하지 않아요" in body["question"]
            jobs = (await client.get("/robot/jobs", headers={"X-API-Key": TEACHER_KEY})).json()
            assert jobs[0]["status"] == "ack_timeout"
        await stop_temi(temi, task)
    asyncio.run(scenario())


def test_failed_and_rejected_results(live):
    async def scenario():
        temi, task = await start_temi(live, delay_s=0.1, fail={"go_to"}, reject={"start_game"})
        live.teacher_llm.script += [tools_reply(call("go_to", location="식당")), text_reply("시작"),
                                    tools_reply(call("start_game", name="숨바꼭질")), text_reply("시작")]
        async with httpx.AsyncClient(base_url=live.http) as client:
            ids = []
            for instr in ("식당 가", "숨바꼭질 해"):
                body = (await client.post("/agent/teacher", json={"instruction": instr},
                                          headers={"X-API-Key": TEACHER_KEY})).json()
                ids.append(next(s for s in body["trace"] if s["type"] == "tool")["result"]["job_id"])
            assert (await wait_job(client, ids[0], {"failed"}))["detail"]["message"].startswith("장애물")
            assert (await wait_job(client, ids[1], {"rejected"}))["status"] == "rejected"
        await stop_temi(temi, task)
    asyncio.run(scenario())


def test_disconnect_mid_job(live):
    async def scenario():
        temi, task = await start_temi(live, delay_s=10)
        live.teacher_llm.script += [tools_reply(call("go_to", location="현관")), text_reply("시작")]
        async with httpx.AsyncClient(base_url=live.http) as client:
            body = (await client.post("/agent/teacher", json={"instruction": "현관 가"},
                                      headers={"X-API-Key": TEACHER_KEY})).json()
            job_id = next(s for s in body["trace"] if s["type"] == "tool")["result"]["job_id"]
            await stop_temi(temi, task)  # 동작 중 연결 끊김
            job = await wait_job(client, job_id, {"disconnected"})
            assert "끊겨" in job["detail"]["message"]
            assert (await client.get("/health")).json()["temi_connected"] == []
    asyncio.run(scenario())


def test_reconnect_replaces_old_connection(live):
    async def scenario():
        first, t1 = await start_temi(live, device="temi-a")
        second, t2 = await start_temi(live, device="temi-a")
        with pytest.raises(ConnectionClosed) as ei:  # 첫 연결은 서버가 4000으로 닫음
            await asyncio.wait_for(t1, 5)
        assert ei.value.rcvd.code == 4000
        live.teacher_llm.script += [tools_reply(call("speak", text="안녕")), text_reply("시작")]
        async with httpx.AsyncClient(base_url=live.http) as client:
            assert (await client.get("/health")).json()["temi_connected"] == ["temi-a"]
            await client.post("/agent/teacher", json={"instruction": "인사"}, headers={"X-API-Key": TEACHER_KEY})
        assert len(second.received) == 1 and first.received == []
        await stop_temi(second, t2)
    asyncio.run(scenario())


def test_child_mode_only_start_game_reaches_temi(live):
    async def scenario():
        temi, task = await start_temi(live, device="temi-a", delay_s=0.1)
        live.child_llm.script += [
            tools_reply(call("start_game", name="숨바꼭질")), text_reply("숨바꼭질 시작했어!"),
            tools_reply(call("go_to", location="현관")), text_reply("그건 선생님께 물어보자."),
        ]
        async with httpx.AsyncClient(base_url=live.http) as client:
            h = {"X-API-Key": TEMI_KEY}
            r1 = (await client.post("/agent/child", json={"text": "숨바꼭질 하자", "device_id": "temi-a"}, headers=h)).json()
            r2 = (await client.post("/agent/child", json={"text": "밖에 나가자", "device_id": "temi-a"}, headers=h)).json()
        assert r1["game_started"] == "숨바꼭질" and r2["game_started"] is None
        assert [c["action"] for c in temi.received] == ["start_game"]
        await stop_temi(temi, task)
    asyncio.run(scenario())


def test_fake_temi_runs_commands_in_order(live):
    async def scenario():
        temi, task = await start_temi(live, delay_s=0.3)
        live.teacher_llm.script += [tools_reply(call("go_to", location="식당")),
                                    tools_reply(call("start_delivery", kind="snack", targets=["햇살반"])),
                                    text_reply("시작했습니다.")]
        async with httpx.AsyncClient(base_url=live.http) as client:
            body = (await client.post("/agent/teacher", json={"instruction": "식당 가서 햇살반 간식"},
                                      headers={"X-API-Key": TEACHER_KEY})).json()
            ids = [s["result"]["job_id"] for s in body["trace"] if s["type"] == "tool"]
            first = await wait_job(client, ids[0], {"completed"})
            second = await wait_job(client, ids[1], {"completed"})
        assert temi.finished_order == ["go_to", "start_delivery"]
        assert first["finished_at"] <= second["finished_at"]
        await stop_temi(temi, task)
    asyncio.run(scenario())


# --- POST /robot/stop (교사 앱 정지 버튼) ---

def test_robot_stop_all_connected(live):
    async def scenario():
        a, ta = await start_temi(live, device="temi-a", delay_s=0.1)
        b, tb = await start_temi(live, device="temi-b", delay_s=0.1)
        async with httpx.AsyncClient(base_url=live.http) as client:
            r = await client.post("/robot/stop", headers={"X-API-Key": TEACHER_KEY})
            assert r.status_code == 200
            body = r.json()
            assert body["all_acked"] is True
            assert sorted(x["device_id"] for x in body["results"]) == ["temi-a", "temi-b"]
            for x in body["results"]:
                job = await wait_job(client, x["job_id"], {"completed"})
                assert job["action"] == "stop" and job["source_mode"] == "teacher_app"
        assert [c["action"] for c in a.received] == ["stop"] and [c["action"] for c in b.received] == ["stop"]
        assert live.teacher_llm.requests == []  # LLM을 거치지 않음
        await stop_temi(a, ta)
        await stop_temi(b, tb)
    asyncio.run(scenario())


def test_robot_stop_one_device(live):
    async def scenario():
        a, ta = await start_temi(live, device="temi-a")
        b, tb = await start_temi(live, device="temi-b")
        async with httpx.AsyncClient(base_url=live.http) as client:
            body = (await client.post("/robot/stop", json={"device_id": "temi-b"},
                                      headers={"X-API-Key": TEACHER_KEY})).json()
        assert [x["device_id"] for x in body["results"]] == ["temi-b"]
        assert a.received == [] and [c["action"] for c in b.received] == ["stop"]
        await stop_temi(a, ta)
        await stop_temi(b, tb)
    asyncio.run(scenario())


def test_robot_stop_not_connected_409(live):
    async def scenario():
        async with httpx.AsyncClient(base_url=live.http) as client:
            r = await client.post("/robot/stop", headers={"X-API-Key": TEACHER_KEY})
            assert r.status_code == 409 and "연결된 temi가 없어" in r.json()["detail"]
            a, ta = await start_temi(live, device="temi-a")
            r = await client.post("/robot/stop", json={"device_id": "temi-z"}, headers={"X-API-Key": TEACHER_KEY})
            assert r.status_code == 409 and "temi-z" in r.json()["detail"]
            await stop_temi(a, ta)
    asyncio.run(scenario())


def test_robot_stop_ack_timeout_reported(live):
    async def scenario():
        a, ta = await start_temi(live, device="temi-a", no_ack={"stop"})
        b, tb = await start_temi(live, device="temi-b")
        async with httpx.AsyncClient(base_url=live.http) as client:
            body = (await client.post("/robot/stop", headers={"X-API-Key": TEACHER_KEY})).json()
        res = {x["device_id"]: x for x in body["results"]}
        assert body["all_acked"] is False
        assert res["temi-a"]["status"] == "ack_timeout" and not res["temi-a"]["ok"]
        assert res["temi-b"]["ok"]
        await stop_temi(a, ta)
        await stop_temi(b, tb)
    asyncio.run(scenario())


def test_robot_stop_auth(live):
    async def scenario():
        async with httpx.AsyncClient(base_url=live.http) as client:
            assert (await client.post("/robot/stop")).status_code == 401
            assert (await client.post("/robot/stop", headers={"X-API-Key": TEMI_KEY})).status_code == 403
            assert (await client.post("/robot/stop", json={"device_id": "bad id!"},
                                      headers={"X-API-Key": TEACHER_KEY})).status_code == 422
    asyncio.run(scenario())


def test_robot_stop_works_in_mock_mode():
    """실행기가 mock이어도 연결된 temi가 있으면 정지 명령은 보낸다 (안전 우선)."""
    async def scenario(live):
        a, ta = await start_temi(live, device="temi-a")
        async with httpx.AsyncClient(base_url=live.http) as client:
            body = (await client.post("/robot/stop", headers={"X-API-Key": TEACHER_KEY})).json()
        assert body["all_acked"] and [c["action"] for c in a.received] == ["stop"]
        await stop_temi(a, ta)
    with LiveServer(robot_executor="mock") as live:
        asyncio.run(scenario(live))
