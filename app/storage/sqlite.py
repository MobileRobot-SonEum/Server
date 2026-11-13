"""SQLite 구현. db_path=":memory:"면 테스트용 메모리 DB."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from typing import Any, Callable

from .base import Row

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    type TEXT NOT NULL,
    phase TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    location TEXT,
    confidence REAL,
    source TEXT,
    alert_channels TEXT,
    resolved_at TEXT,
    resolved_by TEXT,
    resolution_method TEXT,
    child TEXT,
    note TEXT,
    received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_incident ON events(incident_id);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(substr(timestamp, 1, 10));

CREATE TABLE IF NOT EXISTS incident_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    content TEXT NOT NULL,
    fields TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_incident ON incident_reports(incident_id);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child TEXT,
    text TEXT NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    incident_id TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    reject_reason TEXT,
    sent_at TEXT,
    send_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);

CREATE TABLE IF NOT EXISTS teacher_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL,
    category TEXT NOT NULL,
    matched TEXT NOT NULL,
    utterance TEXT NOT NULL,
    device_id TEXT,
    created_at TEXT NOT NULL,
    acknowledged_at TEXT,
    acknowledged_by TEXT
);
"""

_EVENT_COLS = ["event_id", "incident_id", "type", "phase", "timestamp", "location", "confidence", "source",
               "alert_channels", "resolved_at", "resolved_by", "resolution_method", "child", "note", "received_at"]
_APPROVAL_UPDATABLE = {"child", "text", "status", "decided_at", "decided_by", "reject_reason", "sent_at", "send_error"}


class SQLiteRepository:
    def __init__(self, db_path: str, now: Callable[[], datetime]):
        self._now = now
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def _ts(self) -> str:
        return self._now().isoformat(timespec="seconds")

    def _q(self, sql: str, params: tuple = ()) -> list[Row]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple = ()) -> Row | None:
        rows = self._q(sql, params)
        return rows[0] if rows else None

    def _exec(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.lastrowid

    # ------------------------------------------------------------ events
    def add_event(self, event: Row) -> bool:
        row = {c: event.get(c) for c in _EVENT_COLS}
        if isinstance(row["alert_channels"], list):
            row["alert_channels"] = json.dumps(row["alert_channels"], ensure_ascii=False)
        row["received_at"] = self._ts()
        with self._lock:
            cur = self._conn.execute(
                f"INSERT OR IGNORE INTO events ({', '.join(_EVENT_COLS)}) VALUES ({', '.join('?' * len(_EVENT_COLS))})",
                tuple(row[c] for c in _EVENT_COLS),
            )
            self._conn.commit()
            return cur.rowcount == 1

    @staticmethod
    def _event_out(r: Row) -> Row:
        if r.get("alert_channels"):
            r["alert_channels"] = json.loads(r["alert_channels"])
        return r

    def get_event(self, event_id: str) -> Row | None:
        r = self._one("SELECT * FROM events WHERE event_id = ?", (event_id,))
        return self._event_out(r) if r else None

    def events_by_incident(self, incident_id: str) -> list[Row]:
        return [self._event_out(r) for r in
                self._q("SELECT * FROM events WHERE incident_id = ? ORDER BY timestamp, received_at", (incident_id,))]

    def events_by_date(self, date: str) -> list[Row]:
        return [self._event_out(r) for r in
                self._q("SELECT * FROM events WHERE substr(timestamp, 1, 10) = ? ORDER BY timestamp", (date,))]

    # ------------------------------------------------------------ reports
    def add_report(self, incident_id: str, content: str, fields: dict, created_by: str) -> Row:
        with self._lock:
            cur = self._conn.execute("SELECT COALESCE(MAX(version), 0) FROM incident_reports WHERE incident_id = ?",
                                     (incident_id,))
            version = cur.fetchone()[0] + 1
            self._conn.execute("UPDATE incident_reports SET status = 'superseded' WHERE incident_id = ? AND status = 'draft'",
                               (incident_id,))
            cur = self._conn.execute(
                "INSERT INTO incident_reports (incident_id, version, status, content, fields, created_by, created_at) "
                "VALUES (?, ?, 'draft', ?, ?, ?, ?)",
                (incident_id, version, content, json.dumps(fields, ensure_ascii=False), created_by, self._ts()),
            )
            self._conn.commit()
            report_id = cur.lastrowid
        return self.get_report(report_id)  # type: ignore[return-value]

    @staticmethod
    def _report_out(r: Row) -> Row:
        r["fields"] = json.loads(r["fields"])
        return r

    def get_report(self, report_id: int) -> Row | None:
        r = self._one("SELECT * FROM incident_reports WHERE id = ?", (report_id,))
        return self._report_out(r) if r else None

    def latest_report(self, incident_id: str) -> Row | None:
        r = self._one("SELECT * FROM incident_reports WHERE incident_id = ? ORDER BY version DESC LIMIT 1", (incident_id,))
        return self._report_out(r) if r else None

    def list_reports(self, incident_id: str | None = None) -> list[Row]:
        if incident_id:
            rows = self._q("SELECT * FROM incident_reports WHERE incident_id = ? ORDER BY version", (incident_id,))
        else:
            rows = self._q("SELECT * FROM incident_reports ORDER BY id")
        return [self._report_out(r) for r in rows]

    # ------------------------------------------------------------ approvals
    def add_approval(self, child: str | None, text: str, source: str, incident_id: str | None = None) -> Row:
        aid = self._exec(
            "INSERT INTO approvals (child, text, status, source, incident_id, created_at) VALUES (?, ?, 'pending', ?, ?, ?)",
            (child, text, source, incident_id, self._ts()),
        )
        return self.get_approval(aid)  # type: ignore[return-value]

    def get_approval(self, approval_id: int) -> Row | None:
        return self._one("SELECT * FROM approvals WHERE id = ?", (approval_id,))

    def list_approvals(self, status: str | None = None) -> list[Row]:
        if status:
            return self._q("SELECT * FROM approvals WHERE status = ? ORDER BY id", (status,))
        return self._q("SELECT * FROM approvals ORDER BY id")

    def update_approval(self, approval_id: int, **fields: Any) -> Row | None:
        bad = set(fields) - _APPROVAL_UPDATABLE
        if bad:
            raise ValueError(f"수정할 수 없는 필드: {bad}")
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            self._exec(f"UPDATE approvals SET {sets} WHERE id = ?", (*fields.values(), approval_id))
        return self.get_approval(approval_id)

    def approval_for_incident(self, incident_id: str) -> Row | None:
        return self._one("SELECT * FROM approvals WHERE incident_id = ? ORDER BY id LIMIT 1", (incident_id,))

    # ------------------------------------------------------------ alerts
    def add_alert(self, level: str, category: str, matched: str, utterance: str, device_id: str | None) -> Row:
        aid = self._exec(
            "INSERT INTO teacher_alerts (level, category, matched, utterance, device_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (level, category, matched, utterance, device_id, self._ts()),
        )
        return self.get_alert(aid)  # type: ignore[return-value]

    def get_alert(self, alert_id: int) -> Row | None:
        return self._one("SELECT * FROM teacher_alerts WHERE id = ?", (alert_id,))

    def list_alerts(self, level: str | None = None, unacked_only: bool = False) -> list[Row]:
        sql, params = "SELECT * FROM teacher_alerts WHERE 1=1", []
        if level:
            sql += " AND level = ?"
            params.append(level)
        if unacked_only:
            sql += " AND acknowledged_at IS NULL"
        # 긴급 먼저, 그다음 최신 순
        sql += " ORDER BY CASE level WHEN 'urgent' THEN 0 ELSE 1 END, id DESC"
        return self._q(sql, tuple(params))

    def ack_alert(self, alert_id: int, by: str | None) -> Row | None:
        if not self.get_alert(alert_id):
            return None
        self._exec("UPDATE teacher_alerts SET acknowledged_at = COALESCE(acknowledged_at, ?), "
                   "acknowledged_by = COALESCE(acknowledged_by, ?) WHERE id = ?", (self._ts(), by, alert_id))
        return self.get_alert(alert_id)

    # ------------------------------------------------------------ debug
    def dump_all_text(self) -> str:
        """테스트용: 모든 테이블의 모든 값을 이어 붙인 문자열 (원문 미저장 검사)."""
        parts = []
        for table in ("events", "incident_reports", "approvals", "teacher_alerts"):
            for r in self._q(f"SELECT * FROM {table}"):
                parts.extend(str(v) for v in r.values())
        return "\n".join(parts)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
