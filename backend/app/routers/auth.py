"""认证端点,见 docs/auth.md 与 docs/api.md(auth 节)。

- POST /auth/login   豁免  body {username,password} → {token, expires_at};
  失败调 rate_limit.record_login_failure 并审计 auth.login(result=error),
  成功调 reset_login_failures;setup 模式下返回 503 setup_required。
- POST /auth/setup   豁免(仅 users 表空时可用,否则 409)→ 创建管理员,
  成功即视为登录返回 token;审计 auth.setup(result=success)。
- GET  /auth/me      任意已认证 → {type, name, scopes}(用户主体给 ["admin"],
  admin 隐含全部 scope)。
- PATCH /auth/password 任意已认证,仅 JWT 用户(key 主体 403);
  old_password 错误返回 400 invalid_password(信封格式)。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_principal, users_empty
from app.auth.jwt_utils import (
    create_token,
    dummy_verify,
    hash_password,
    verify_password,
)
from app.auth.principals import TYPE_USER, Principal
from app.db import get_db
from app.errors import ApiError
from app.middleware import rate_limit
from app.models import User
from app.services import audit_svc

router = APIRouter(prefix="/auth", tags=["auth"])


def _pw_bytes_ok(v: str) -> str:
    if len(v.encode("utf-8")) > 72:
        raise ValueError("password must be at most 72 bytes (bcrypt limit)")
    return v


class CredentialsBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return _pw_bytes_ok(v)


class PasswordChangeBody(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)

    @field_validator("old_password", "new_password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return _pw_bytes_ok(v)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _get_user(db: Session, username: str) -> User | None:
    return db.execute(select(User).where(User.username == username)).scalar_one_or_none()


def _audit_login_failure(db: Session, username: str, ip: str) -> None:
    name = username[:64] or "unknown"
    audit_svc.record(
        db,
        actor_type="user",
        actor_name=name,
        action="auth.login",
        target_type="auth",
        target_id=name,
        result="error",
        detail="invalid username or password",
        ip=ip,
    )


@router.post("/login")
async def login(
    body: CredentialsBody, request: Request, db: Session = Depends(get_db)
) -> dict:
    if users_empty(db):
        raise ApiError(503, "setup_required", "setup required: create the admin account first")
    ip = _client_ip(request)
    user = _get_user(db, body.username)
    if user is None:
        dummy_verify(body.password)  # 与真实校验等耗时,避免用户名枚举
        rate_limit.record_login_failure(ip)
        _audit_login_failure(db, body.username, ip)
        raise ApiError(401, "unauthorized", "invalid username or password")
    if not verify_password(body.password, user.password_hash):
        rate_limit.record_login_failure(ip)
        _audit_login_failure(db, user.username, ip)
        raise ApiError(401, "unauthorized", "invalid username or password")
    rate_limit.reset_login_failures(ip)
    token, expires_at = create_token(user.username)
    return {"token": token, "expires_at": expires_at}


@router.post("/setup")
async def setup(
    body: CredentialsBody, request: Request, db: Session = Depends(get_db)
) -> dict:
    if not users_empty(db):
        raise ApiError(409, "conflict", "setup already completed")
    user = User(username=body.username, password_hash=hash_password(body.password), is_admin=True)
    db.add(user)
    db.flush()
    audit_svc.record(
        db,
        actor_type="user",
        actor_name=user.username,
        action="auth.setup",
        target_type="auth",
        target_id=user.username,
        result="success",
        detail="initial admin account created",
        ip=_client_ip(request),
    )
    token, expires_at = create_token(user.username)
    return {"token": token, "expires_at": expires_at}


@router.get("/me")
async def me(principal: Principal = Depends(get_current_principal)) -> dict:
    scopes = ["admin"] if principal.type == TYPE_USER else sorted(principal.scopes)
    return {"type": principal.type, "name": principal.name, "scopes": scopes}


@router.patch("/password")
async def change_password(
    body: PasswordChangeBody,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    if principal.type != TYPE_USER:
        raise ApiError(403, "forbidden", "password change is only available to user principals")
    user = _get_user(db, principal.name)
    ok = user is not None and await asyncio.to_thread(
        verify_password, body.old_password, user.password_hash
    )
    if not ok:
        audit_svc.record(
            db,
            actor_type="user",
            actor_name=principal.name,
            action="auth.password",
            target_type="auth",
            target_id=principal.name,
            result="error",
            detail="old password mismatch",
            ip=_client_ip(request),
        )
        raise ApiError(400, "invalid_password", "old password is incorrect")
    user.password_hash = await asyncio.to_thread(hash_password, body.new_password)
    db.commit()
    audit_svc.record(
        db,
        actor_type="user",
        actor_name=principal.name,
        action="auth.password",
        target_type="auth",
        target_id=principal.name,
        result="success",
        ip=_client_ip(request),
    )
    return {"status": "ok"}
