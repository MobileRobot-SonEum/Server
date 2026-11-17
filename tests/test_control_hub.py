"""TemiHub·ControlExecutor 단위 테스트 (가짜 연결, 실제 WebSocket 없음)."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.control import AckTimeout, TemiAmbiguous, TemiHub, TemiNotConnected
from app.executors import ControlExecutor
from app.storage import SQLiteRepository


def now():
    return datetime.now(ZoneInfo("Asia/Seoul"))


class FakeConnIO:
    def __init__(self):
        self.sent: list[dict] = []
        self.closed: tuple[int, str] | None = None

    async def send(self, msg):
        self.sent.append(msg)

    async def close(self, code, reason):
        self.closed = (code, reason)

    def commands(self):
        return [m for m in self.sent if m["type"] == "command"]


def make_hub(**kw):
    repo = SQLiteRepository(":memory:", now)
    return TemiHub(repo, now, **kw), repo


async def wait_for_command(io, n=1):
    for _ in range(200):
        if len(io.commands()) >= n:
            return io.commands()[n - 1]
        await asyncio.sleep(0.005)
    raise AssertionError("명령이 전송되지 않음")


def test_command_ack_result_flow():
    async def scenario():
        hub, repo = make_hub()
        io = FakeConnIO()
        conn = await hub.connect("temi-1", io.send, io.close)
        task = asyncio.create_task(hub.send_command("temi-1", "go_to", {"location": "식당"}, "teacher"))
        cmd = await wait_for_command(io)
        assert cmd["action"] == "go_to" and cmd["args"] == {"location": "식당"} and cmd["timeout_s"] == 120.0
        assert repo.get_job(cmd["id"])["status"] == "sent"
        await hub.handle_message(conn, {"type": "ack", "id": cmd["id"]})
        job = await task
        assert job["status"] == "acked" and job["acked_at"]
        await hub.handle_message(conn, {"type": "result", "id": cmd["id"], "status": "completed",
                                        "detail": {"message": "도착"}})
        done = repo.get_job(cmd["id"])
        assert done["status"] == "completed" and done["detail"] == {"message": "도착"} and done["finished_at"]
        await hub.shutdown()
    asyncio.run(scenario())


def test_ack_timeout_then_late_result_recorded():
    async def scenario():
        hub, repo = make_hub(ack_timeout_s=0.05)
        io = FakeConnIO()
        conn = await hub.connect("temi-1", io.send, io.close)
        with pytest.raises(AckTimeout):
            await hub.send_command("temi-1", "speak", {"text": "안녕"}, "teacher")
        job_id = io.commands()[0]["id"]
        assert repo.get_job(job_id)["status"] == "ack_timeout"
        await hub.handle_message(conn, {"type": "result", "id": job_id, "status": "completed"})
        assert repo.get_job(job_id)["status"] == "completed"
    asyncio.run(scenario())


def test_disconnect_before_ack():
    async def scenario():
        hub, repo = make_hub(ack_timeout_s=2)
        io = FakeConnIO()
        conn = await hub.connect("temi-1", io.send, io.close)
        task = asyncio.create_task(hub.send_command("temi-1", "go_to", {"location": "식당"}, "teacher"))
        cmd = await wait_for_command(io)
        await hub.disconnect(conn)
        with pytest.raises(TemiNotConnected):
            await task
        assert repo.get_job(cmd["id"])["status"] == "disconnected"
        assert not hub.is_connected("temi-1")
    asyncio.run(scenario())


def test_disconnect_after_ack_marks_open_jobs():
    async def scenario():
        hub, repo = make_hub()
        io = FakeConnIO()
        conn = await hub.connect("temi-1", io.send, io.close)
        task = asyncio.create_task(hub.send_command("temi-1", "start_delivery", {"kind": "snack", "targets": ["햇살반"]}, "teacher"))
        cmd = await wait_for_command(io)
        await hub.handle_message(conn, {"type": "ack", "id": cmd["id"]})
        await task
        await hub.disconnect(conn)
        job = repo.get_job(cmd["id"])
        assert job["status"] == "disconnected" and "끊겨" in job["detail"]["message"]
        await hub.shutdown()
    asyncio.run(scenario())


def test_result_timeout():
    async def scenario():
        hub, repo = make_hub(action_timeouts={"go_to": 0.05}, result_grace_s=0.0)
        io = FakeConnIO()
        conn = await hub.connect("temi-1", io.send, io.close)
        task = asyncio.create_task(hub.send_command("temi-1", "go_to", {"location": "식당"}, "teacher"))
        cmd = await wait_for_command(io)
        await hub.handle_message(conn, {"type": "ack", "id": cmd["id"]})
        await task
        await asyncio.sleep(0.15)
        assert repo.get_job(cmd["id"])["status"] == "timeout"
    asyncio.run(scenario())


def test_result_without_ack_counts_as_ack():
    async def scenario():
        hub, repo = make_hub()
        io = FakeConnIO()
        conn = await hub.connect("temi-1", io.send, io.close)
        task = asyncio.create_task(hub.send_command("temi-1", "stop", {}, "teacher"))
        cmd = await wait_for_command(io)
        await hub.handle_message(conn, {"type": "result", "id": cmd["id"], "status": "rejected", "detail": "바쁨"})
        await task
        job = repo.get_job(cmd["id"])
        assert job["status"] == "rejected" and job["detail"] == {"message": "바쁨"}
        await hub.shutdown()
    asyncio.run(scenario())


def test_reconnect_replaces_previous():
    async def scenario():
        hub, _ = make_hub()
        old_io, new_io = FakeConnIO(), FakeConnIO()
        old = await hub.connect("temi-1", old_io.send, old_io.close)
        new = await hub.connect("temi-1", new_io.send, new_io.close)
        assert old_io.closed == (4000, "replaced by new connection")
        await hub.disconnect(old)  # 옛 연결의 정리 코드가 늦게 돌아도 새 연결은 유지
        assert hub.is_connected("temi-1") and hub._conns["temi-1"] is new
    asyncio.run(scenario())


def test_resolve_device_rules():
    async def scenario():
        hub, _ = make_hub()
        with pytest.raises(TemiNotConnected):
            hub.resolve_device(None)
        io = FakeConnIO()
        await hub.connect("a", io.send, io.close)
        assert hub.resolve_device(None) == "a"
        await hub.connect("b", io.send, io.close)
        with pytest.raises(TemiAmbiguous):
            hub.resolve_device(None)
        assert hub.resolve_device("b") == "b"
        with pytest.raises(TemiNotConnected):
            hub.resolve_device("c")
        hub.default_device_id = "a"
        assert hub.resolve_device(None) == "a"
    asyncio.run(scenario())


def test_bad_messages_get_error_replies():
    async def scenario():
        hub, _ = make_hub()
        io = FakeConnIO()
        conn = await hub.connect("temi-1", io.send, io.close)
        await hub.handle_message(conn, {"type": "ack"})                       # id 없음
        await hub.handle_message(conn, {"type": "dance", "id": "x"})           # 모르는 type
        await hub.handle_message(conn, {"type": "event", "id": "e1"})          # 이벤트는 /events로
        await hub.handle_message(conn, {"type": "result", "id": "nope", "status": "completed"})
        await hub.handle_message(conn, {"type": "result", "id": "nope", "status": "done"})
        await hub.handle_message(conn, {"type": "ping", "id": "p1"})
        errors = [m["message"] for m in io.sent if m["type"] == "error"]
        assert len(errors) == 5 and any("/events" in e for e in errors)
        assert {"type": "pong", "id": "p1"} in io.sent
    asyncio.run(scenario())


def test_job_from_other_device_rejected():
    async def scenario():
        hub, repo = make_hub()
        io_a, io_b = FakeConnIO(), FakeConnIO()
        a = await hub.connect("a", io_a.send, io_a.close)
        b = await hub.connect("b", io_b.send, io_b.close)
        task = asyncio.create_task(hub.send_command("a", "speak", {"text": "x"}, "teacher"))
        cmd = await wait_for_command(io_a)
        await hub.handle_message(a, {"type": "ack", "id": cmd["id"]})
        await task
        await hub.handle_message(b, {"type": "result", "id": cmd["id"], "status": "completed"})
        assert repo.get_job(cmd["id"])["status"] == "acked"
        await hub.shutdown()
    asyncio.run(scenario())


def test_ping_timeout_closes_connection():
    async def scenario():
        hub, repo = make_hub(ping_interval_s=0.02)
        io = FakeConnIO()
        conn = await hub.connect("temi-1", io.send, io.close)
        conn.last_seen -= 10  # 오래 조용했음
        await asyncio.wait_for(hub.ping_loop(conn), 1)
        assert io.closed == (4001, "ping timeout") and not hub.is_connected("temi-1")
    asyncio.run(scenario())


# --- ControlExecutor ---

def test_executor_not_connected_asks_teacher():
    async def scenario():
        hub, _ = make_hub()
        r = await ControlExecutor(hub, ["식당"]).go_to("식당")
        assert not r.ok and "연결 안 됨" in r.error and "연결되어 있지 않아요" in r.ask_teacher
    asyncio.run(scenario())


def test_executor_child_mode_only_start_game():
    async def scenario():
        hub, repo = make_hub()
        io = FakeConnIO()
        conn = await hub.connect("temi-1", io.send, io.close)
        child = ControlExecutor(hub, ["식당"]).bind("temi-1", "child")
        r = await child.go_to("식당")
        assert not r.ok and "start_game만" in r.error and io.commands() == []
        task = asyncio.create_task(child.start_game("숨바꼭질"))
        cmd = await wait_for_command(io)
        await hub.handle_message(conn, {"type": "ack", "id": cmd["id"]})
        r = await task
        assert r.ok and r.detail["status"] == "started" and r.detail["job_id"] == cmd["id"]
        assert repo.get_job(cmd["id"])["source_mode"] == "child"
        await hub.shutdown()
    asyncio.run(scenario())
