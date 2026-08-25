"""API Key 管理测试: 创建(仅存哈希)→ 认证使用 → scope/禁用 → 主体限制 → 审计。"""
from __future__ import annotations

import hashlib
import string

import pytest
from sqlalchemy import delete, select

from app import models
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


def _admin_token(client) -> str:
    r = client.post("/api/v1/auth/setup", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _create_key(client, token: str, name: str, scopes: list[str]) -> tuple[int, str]:
    r = client.post("/api/v1/keys", json={"name": name, "scopes": scopes}, headers=_bearer(token))
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"id", "key"}
    return body["id"], body["key"]


class FakeDockerClient:
    """version 端点所需的最小 fake。"""

    def version(self) -> dict:
        return {"Version": "27.0.3", "ApiVersion": "1.46", "Os": "linux"}

    def info(self) -> dict:
        return {
            "Driver": "overlay2",
            "Images": 5,
            "Volumes": None,
            "Containers": {"Running": 2, "Paused": 0, "Stopped": 1},
        }


@pytest.fixture()
def fake_docker(monkeypatch):
    from app.services import docker_client

    fake = FakeDockerClient()
    monkeypatch.setattr(docker_client, "get_client", lambda: fake)
    return fake


def _get_key_row(key_id: int) -> models.ApiKey | None:
    s = db_session()
    try:
        return s.get(models.ApiKey, key_id)
    finally:
        s.close()


def _audit_rows(action: str, result: str | None = None) -> list[models.AuditLog]:
    s = db_session()
    try:
        q = select(models.AuditLog).where(models.AuditLog.action == action)
        if result:
            q = q.where(models.AuditLog.result == result)
        return s.execute(q).scalars().all()
    finally:
        s.close()


# ---------- 完整生命周期 ----------


def test_full_flow_setup_login_key_use_disable(client, fake_docker):
    token = _admin_token(client)

    # 建 key: 响应含一次性明文
    key_id, plaintext = _create_key(client, token, "ci-key", ["view"])
    assert plaintext.startswith("dka_")
    assert len(plaintext) == 47  # dka_ + 43 位 base62
    body62 = plaintext[4:]
    assert all(c in string.ascii_letters + string.digits for c in body62)

    # 库里只有哈希 + 前缀,无明文
    row = _get_key_row(key_id)
    assert row is not None
    assert row.key_hash == hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    assert row.key_prefix == plaintext[:8]
    assert row.scopes == '["view"]'
    assert row.enabled is True
    s = db_session()
    try:
        assert plaintext not in str(
            s.execute(select(models.ApiKey).where(models.ApiKey.id == key_id)).all()
        )
    finally:
        s.close()

    # 列表不带哈希/明文
    r = client.get("/api/v1/keys", headers=_bearer(token))
    assert r.status_code == 200
    listing = r.json()
    assert listing["total"] == 1
    item = listing["items"][0]
    assert item["id"] == key_id
    assert item["key_prefix"] == plaintext[:8]
    assert item["scopes"] == ["view"]
    assert set(item) == {"id", "name", "key_prefix", "scopes", "enabled", "created_at", "last_used_at"}

    # 用 key 调需要 view scope 的 /api/v1/version
    r = client.get("/api/v1/version", headers=_bearer(plaintext))
    assert r.status_code == 200, r.text
    assert r.json()["docker"] == "27.0.3"
    row = _get_key_row(key_id)
    assert row is not None and row.last_used_at is not None  # 认证即刷新

    # scope 不足: 只有 start 的 key 访问 view 端点 → 403
    _, start_key = _create_key(client, token, "ops-key", ["start"])
    r = client.get("/api/v1/version", headers=_bearer(start_key))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"

    # admin scope 的 key 拥有全部能力(view 端点可用)
    _, admin_key = _create_key(client, token, "root-key", ["admin"])
    r = client.get("/api/v1/version", headers=_bearer(admin_key))
    assert r.status_code == 200

    # 禁用后 → 401 key_disabled
    r = client.patch(f"/api/v1/keys/{key_id}", json={"enabled": False}, headers=_bearer(token))
    assert r.status_code == 200
    r = client.get("/api/v1/version", headers=_bearer(plaintext))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "key_disabled"


def test_invalid_api_key_401(client):
    _admin_token(client)
    r = client.get("/api/v1/auth/me", headers=_bearer("dka_" + "x" * 43))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


# ---------- 主体限制: keys 写操作仅 JWT 用户 ----------


def test_keys_writes_rejected_for_api_key_principal(client, fake_docker):
    token = _admin_token(client)
    admin_key_id, admin_key = _create_key(client, token, "automation", ["admin"])

    # GET 对 admin scope 的任意主体开放
    r = client.get("/api/v1/keys", headers=_bearer(admin_key))
    assert r.status_code == 200

    # 写操作一律 403
    r = client.post(
        "/api/v1/keys", json={"name": "child", "scopes": ["view"]}, headers=_bearer(admin_key)
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"

    r = client.patch(
        f"/api/v1/keys/{admin_key_id}", json={"enabled": False}, headers=_bearer(admin_key)
    )
    assert r.status_code == 403

    r = client.delete(f"/api/v1/keys/{admin_key_id}", headers=_bearer(admin_key))
    assert r.status_code == 403

    # 被拒的写操作也落审计(result=error)
    assert len(_audit_rows("key.create", "error")) == 1
    assert len(_audit_rows("key.update", "error")) == 1
    assert len(_audit_rows("key.delete", "error")) == 1


def test_get_keys_requires_admin_scope(client):
    token = _admin_token(client)
    _, view_key = _create_key(client, token, "viewer", ["view"])
    r = client.get("/api/v1/keys", headers=_bearer(view_key))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


# ---------- 校验与增删改 ----------


def test_create_key_invalid_scopes(client):
    token = _admin_token(client)
    r = client.post(
        "/api/v1/keys", json={"name": "bad", "scopes": ["view", "root"]}, headers=_bearer(token)
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_scope"
    assert len(_audit_rows("key.create", "error")) == 1

    r = client.post("/api/v1/keys", json={"name": "bad", "scopes": []}, headers=_bearer(token))
    assert r.status_code == 400


def test_patch_and_delete_key(client):
    token = _admin_token(client)
    key_id, plaintext = _create_key(client, token, "old-name", ["view"])

    r = client.patch(
        f"/api/v1/keys/{key_id}",
        json={"name": "new-name", "scopes": ["view", "stop"]},
        headers=_bearer(token),
    )
    assert r.status_code == 200
    row = _get_key_row(key_id)
    assert row is not None
    assert row.name == "new-name"
    assert row.scope_list() == ["view", "stop"]

    # 空 body → 400
    r = client.patch(f"/api/v1/keys/{key_id}", json={}, headers=_bearer(token))
    assert r.status_code == 400

    # 不存在的 key → 404 + 审计 error
    r = client.patch("/api/v1/keys/99999", json={"enabled": False}, headers=_bearer(token))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "key_not_found"
    assert len(_audit_rows("key.update", "error")) == 2

    # 删除 → 硬删除
    r = client.delete(f"/api/v1/keys/{key_id}", headers=_bearer(token))
    assert r.status_code == 200
    assert _get_key_row(key_id) is None

    r = client.delete(f"/api/v1/keys/{key_id}", headers=_bearer(token))
    assert r.status_code == 404
    assert len(_audit_rows("key.delete", "error")) == 1

    # 删除后凭证立即失效
    r = client.get("/api/v1/auth/me", headers=_bearer(plaintext))
    assert r.status_code == 401


def test_audit_never_contains_plaintext(client):
    token = _admin_token(client)
    _, plaintext = _create_key(client, token, "audit-check", ["view"])
    client.patch("/api/v1/keys/1", json={"enabled": False}, headers=_bearer(token))

    s = db_session()
    try:
        rows = s.execute(select(models.AuditLog)).scalars().all()
        assert rows
        for row in rows:
            assert plaintext not in (row.detail or "")
            assert plaintext not in row.target_id
            assert plaintext not in row.actor_name
    finally:
        s.close()

    success = [r for r in _audit_rows("key.create") if r.result == "success"]
    assert len(success) == 1
    assert success[0].target_type == "api_key"
