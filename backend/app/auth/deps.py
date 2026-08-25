"""认证依赖 — 实现负责人: 认证 subagent。

契约(路由层按此使用,不得改函数名/签名):

get_current_principal(request) -> Principal  (async, FastAPI 依赖)
    1. setup 模式(users 表空)时:除 /api/v1/auth/setup 与 /api/health 外,
       一律抛 ApiError(503, "setup_required")。
    2. Authorization: Bearer <jwt> → 验签(HS256,密钥 settings.secret_path 文件
       内容,sub=username)→ Principal(type=user, scopes=ALL)。
    3. Bearer dka_... → SHA-256 哈希查 api_keys 表,enabled=False 抛
       ApiError(401, "key_disabled"),查不到抛 ApiError(401, "unauthorized"),
       命中更新 last_used_at → Principal(type=api_key, scopes=set(key.scope_list()))。
    4. 其他一律 ApiError(401, "unauthorized")。
    成功时 request.state.principal = principal(请求日志与审计用)。

require_scope(scope: str) -> Callable  (返回依赖函数,路由层写 Depends(require_scope("view")))
    取 principal,无权限抛 ApiError(403, "forbidden"),返回 principal。
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable, Coroutine

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.jwt_utils import decode_token
from app.auth.principals import ALL_SCOPES, TYPE_API_KEY, TYPE_USER, Principal
from app.errors import ApiError

_SETUP_EXEMPT_PATHS = frozenset({"/api/v1/auth/setup", "/api/health"})


def users_empty(db: Session) -> bool:
    """users 表为空(= setup 模式)判定。"""
    from app.models import User

    return db.execute(select(User.id).limit(1)).first() is None


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _principal_from_api_key(db: Session, token: str, request: Request) -> Principal:
    from loguru import logger

    from app.models import ApiKey
    from app.util import now_iso

    key_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash)).scalar_one_or_none()
    if row is None:
        # 排障日志: 仅来源 IP + key 前 8 字符,永不含完整明文
        logger.warning(
            "invalid api key: ip={} key_prefix={}", _client_ip(request), token[:8]
        )
        raise ApiError(401, "unauthorized", "invalid credentials")
    if not row.enabled:
        raise ApiError(401, "key_disabled", "api key is disabled")
    row.last_used_at = now_iso()
    db.commit()
    return Principal(type=TYPE_API_KEY, name=row.name, scopes=set(row.scope_list()))


def _principal_from_jwt(token: str) -> Principal:
    payload = decode_token(token)
    if payload is None:
        raise ApiError(401, "unauthorized", "invalid credentials")
    return Principal(type=TYPE_USER, name=str(payload["sub"]), scopes=set(ALL_SCOPES))


async def get_current_principal(request: Request) -> Principal:
    from app.db import session as db_session

    db = db_session()
    try:
        if users_empty(db) and request.url.path not in _SETUP_EXEMPT_PATHS:
            raise ApiError(503, "setup_required", "setup required: create the admin account first")

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise ApiError(401, "unauthorized", "missing bearer credentials")
        token = auth[7:].strip()
        if not token:
            raise ApiError(401, "unauthorized", "missing bearer credentials")
        if token.startswith("dka_"):
            principal = _principal_from_api_key(db, token, request)
        else:
            principal = _principal_from_jwt(token)
        request.state.principal = principal
        return principal
    finally:
        db.close()


def require_scope(scope: str) -> Callable[[Request], Coroutine[Any, Any, Principal]]:
    async def dep(request: Request) -> Principal:
        principal = await get_current_principal(request)
        if not principal.has_scope(scope):
            raise ApiError(403, "forbidden", f"scope '{scope}' required")
        return principal

    return dep
