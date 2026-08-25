"""认证模块测试: setup 模式 → setup → login → me → password → rate limit。

TestClient 的来源 IP 固定为 "testclient",rate limit 钩子按此清理。
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from app import models
from app.config import settings
from app.db import session as db_session
from app.middleware.rate_limit import reset_login_failures

USERNAME = "admin"
PASSWORD = "S3cret-Passw0rd"


def _reset_state() -> None:
    reset_login_failures("testclient")
    s = db_session()
    try:
        s.execute(delete(models.AuditLog))
        s.execute(delete(models.ApiKey))
        s.execute(delete(models.User))
        s.commit()
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _clean_state():
    _reset_state()
    yield
    _reset_state()


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _setup(client, username: str = USERNAME, password: str = PASSWORD) -> str:
    r = client.post("/api/v1/auth/setup", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token"]
    assert body["expires_at"].endswith("Z")
    return body["token"]


def _login(client, username: str = USERNAME, password: str = PASSWORD):
    return client.post("/api/v1/auth/login", json={"username": username, "password": password})


def _audit_rows(action: str, result: str | None = None) -> list[models.AuditLog]:
    s = db_session()
    try:
        q = select(models.AuditLog).where(models.AuditLog.action == action)
        if result:
            q = q.where(models.AuditLog.result == result)
        return s.execute(q).scalars().all()
    finally:
        s.close()


# ---------- setup 模式 ----------


def test_setup_mode_blocks_everything_except_setup_and_health(client):
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "setup_required"

    r = client.get("/api/v1/containers")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "setup_required"

    r = client.post("/api/v1/auth/login", json={"username": "x", "password": "y"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "setup_required"

    r = client.get("/api/health")
    assert r.status_code == 200

    r = client.get("/api/v1/version")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "setup_required"


def test_setup_creates_admin_and_secret(client):
    token = _setup(client)
    assert token

    s = db_session()
    try:
        users = s.execute(select(models.User)).scalars().all()
        assert len(users) == 1
        assert users[0].username == USERNAME
        assert users[0].is_admin is True
        assert users[0].password_hash != PASSWORD
        assert users[0].password_hash.startswith("$2")  # bcrypt
    finally:
        s.close()

    rows = _audit_rows("auth.setup", "success")
    assert len(rows) == 1
    assert rows[0].actor_name == USERNAME
    assert rows[0].target_type == "auth"

    # JWT 签名密钥已生成并持久化(64 字节 → 128 hex 字符)
    assert settings.secret_path.exists()
    assert len(settings.secret_path.read_text(encoding="utf-8").strip()) == 128


def test_setup_twice_conflict(client):
    _setup(client)
    r = client.post("/api/v1/auth/setup", json={"username": "other", "password": "another-pass"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"

    s = db_session()
    try:
        users = s.execute(select(models.User)).scalars().all()
        assert len(users) == 1  # 只有初始管理员
    finally:
        s.close()


# ---------- login / me ----------


def test_login_success_and_me(client):
    _setup(client)
    r = _login(client)
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert r.json()["expires_at"].endswith("Z")

    r = client.get("/api/v1/auth/me", headers=_bearer(token))
    assert r.status_code == 200
    assert r.json() == {"type": "user", "name": USERNAME, "scopes": ["admin"]}


def test_login_wrong_password_401_and_audited(client):
    _setup(client)
    r = _login(client, password="wrong-password")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"

    rows = _audit_rows("auth.login", "error")
    assert len(rows) == 1
    assert rows[0].actor_name == USERNAME
    assert rows[0].ip == "testclient"
    assert PASSWORD not in (rows[0].detail or "")


def test_login_unknown_user_401_and_audited(client):
    _setup(client)
    r = _login(client, username="nobody")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    assert len(_audit_rows("auth.login", "error")) == 1


def test_missing_or_invalid_credentials_401(client):
    _setup(client)
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"

    r = client.get("/api/v1/auth/me", headers={"Authorization": "Token abc"})
    assert r.status_code == 401

    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401

    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer "})
    assert r.status_code == 401


def test_expired_token_401(client, monkeypatch):
    _setup(client)
    from app.auth.jwt_utils import create_token

    monkeypatch.setattr(settings, "jwt_expire_hours", -1)
    token, _ = create_token(USERNAME)
    r = client.get("/api/v1/auth/me", headers=_bearer(token))
    assert r.status_code == 401


# ---------- password ----------


def test_change_password_flow(client):
    _setup(client)
    token = _login(client).json()["token"]

    r = client.patch(
        "/api/v1/auth/password",
        json={"old_password": "wrong-old", "new_password": "New-Passw0rd"},
        headers=_bearer(token),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_password"

    r = client.patch(
        "/api/v1/auth/password",
        json={"old_password": PASSWORD, "new_password": "New-Passw0rd"},
        headers=_bearer(token),
    )
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

    # v1 不做全局吊销: 旧 token 仍有效
    assert client.get("/api/v1/auth/me", headers=_bearer(token)).status_code == 200

    assert _login(client).status_code == 401  # 旧密码失效
    r = _login(client, password="New-Passw0rd")
    assert r.status_code == 200


def test_change_password_rejects_api_key_principal(client):
    _setup(client)
    token = _login(client).json()["token"]
    r = client.post("/api/v1/keys", json={"name": "k", "scopes": ["admin"]}, headers=_bearer(token))
    assert r.status_code == 201
    api_key = r.json()["key"]

    r = client.patch(
        "/api/v1/auth/password",
        json={"old_password": PASSWORD, "new_password": "New-Passw0rd"},
        headers=_bearer(api_key),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


# ---------- rate limit ----------


def test_rate_limit_locks_after_5_failures(client):
    _setup(client)
    for _ in range(5):
        r = _login(client, password="bad-password")
        assert r.status_code == 401

    # 第 6 次: 即使密码正确也被中间件拦截为 429
    r = _login(client)
    assert r.status_code == 429
    body = r.json()
    assert body["error"]["code"] == "rate_limited"
    assert "error" in body and "message" in body["error"]

    # 锁定期间其他端点不受影响
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401

    # 模拟 60 秒锁到期(直接清零,等价于时间流逝)
    reset_login_failures("testclient")
    r = _login(client)
    assert r.status_code == 200
