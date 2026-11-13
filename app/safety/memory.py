"""아이 모드 단기 대화 기억 (프로세스 메모리 전용).

- temi(device_id)별로 최근 N턴만 들고 있다가 TTL 동안 대화가 없으면 지운다.
- DB·로그·파일에는 절대 쓰지 않는다. 서버를 재시작하면 사라진다.
- turns=0 또는 ttl_s=0이면 기억하지 않는다.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from app.llm import Message


@dataclass
class _Session:
    turns: deque = field(default_factory=deque)
    last_seen: float = 0.0


class ChildConversationMemory:
    def __init__(self, max_turns: int = 3, ttl_s: float = 120.0, clock: Callable[[], float] = time.monotonic):
        self.max_turns = max(0, max_turns)
        self.ttl_s = max(0.0, ttl_s)
        self._clock = clock
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.max_turns > 0 and self.ttl_s > 0

    def _purge(self, now: float) -> None:
        expired = [k for k, s in self._sessions.items() if now - s.last_seen > self.ttl_s]
        for k in expired:
            del self._sessions[k]

    def history(self, device_id: str | None) -> list[Message]:
        if not self.enabled or not device_id:
            return []
        with self._lock:
            self._purge(self._clock())
            s = self._sessions.get(device_id)
            if not s:
                return []
            out: list[Message] = []
            for user, assistant in s.turns:
                out += [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}]
            return out

    def add(self, device_id: str | None, user: str, assistant: str) -> None:
        if not self.enabled or not device_id:
            return
        with self._lock:
            now = self._clock()
            self._purge(now)
            s = self._sessions.setdefault(device_id, _Session(turns=deque(maxlen=self.max_turns)))
            s.turns.append((user, assistant))
            s.last_seen = now

    def forget(self, device_id: str) -> None:
        with self._lock:
            self._sessions.pop(device_id, None)

    def active_devices(self) -> int:
        with self._lock:
            self._purge(self._clock())
            return len(self._sessions)
