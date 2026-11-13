"""클라이언트 역할별 API 키 (헤더 X-API-Key).

- teacher_app: /agent/teacher, /approvals
- temi:        /agent/child
- rule_engine: /events
"""

from __future__ import annotations

import hmac
import logging
from typing import Literal

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from app.config import Settings

log = logging.getLogger(__name__)

Role = Literal["teacher_app", "temi", "rule_engine"]
ROLES: tuple[Role, ...] = ("teacher_app", "temi", "rule_engine")

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def role_for_key(settings: Settings, key: str) -> Role | None:
    configured = {
        "teacher_app": settings.teacher_app_api_key,
        "temi": settings.temi_api_key,
        "rule_engine": settings.rule_engine_api_key,
    }
    matched: Role | None = None
    for role, secret in configured.items():
        if secret is None or not secret.get_secret_value():
            continue
        # 시간 차이로 키를 추측하지 못하게 모든 키를 끝까지 비교한다
        if hmac.compare_digest(key.encode(), secret.get_secret_value().encode()):
            matched = role  # type: ignore[assignment]
    return matched


def require_role(*allowed: Role):
    async def dependency(request: Request, api_key: str | None = Depends(_api_key_header)) -> Role | None:
        settings: Settings = request.app.state.settings
        if not settings.auth_enabled:
            return None
        if not api_key:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "X-API-Key 헤더가 필요합니다")
        role = role_for_key(settings, api_key)
        if role is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API 키가 올바르지 않습니다")
        if role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"'{role}' 역할은 이 API를 쓸 수 없습니다")
        return role

    return dependency


def check_auth_config(settings: Settings) -> None:
    if not settings.auth_enabled:
        log.warning("AUTH_ENABLED=false: API 키 검사를 끈 상태입니다. 공개된 서버에서는 절대 쓰지 마세요.")
        return
    keys = [settings.teacher_app_api_key, settings.temi_api_key, settings.rule_engine_api_key]
    values = [k.get_secret_value() for k in keys if k is not None and k.get_secret_value()]
    missing = [r for r, k in zip(ROLES, keys) if k is None or not k.get_secret_value()]
    if missing:
        log.warning("API 키가 설정되지 않은 역할: %s (해당 역할의 요청은 모두 거부됩니다)", ", ".join(missing))
    if len(values) != len(set(values)):
        from app.config import ConfigError
        raise ConfigError("역할별 API 키가 서로 같습니다. 역할마다 다른 키를 쓰세요.")
