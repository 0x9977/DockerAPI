"""API Key 管理端点,见 docs/auth.md 与 docs/api.md(keys 节)。

- GET    /keys        admin scope(任意主体)→ {total, items:[...]}(不含哈希/明文)
- POST   /keys        admin + 仅 JWT 用户(key 主体 403)→ 201 {id, key:"dka_..."}
                       (明文仅此一次;库里只存 SHA-256 hex + key_prefix)
- PATCH  /keys/{id}   admin + 仅 JWT 用户 → {status:"ok"}
- DELETE /keys/{id}   admin + 仅 JWT 用户 → {status:"ok"}

全部写操作(成功与失败)审计 key.create / key.update / key.delete;
审计 detail 永不含明文 key。scopes 校验 ⊆ {view,start,stop,delete,admin}。
"""
from __future__ import annotations

import hashlib
import json
import secrets
import string

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import require_scope
from app.auth.principals import ALL_SCOPES, TYPE_USER, Principal
from app.db import get_db
from app.errors import ApiError, not_found
from app.models import ApiKey
from app.services import audit_svc
from app.util import now_iso

router = APIRouter(prefix="/keys", tags=["keys"])

_BASE62 = string.ascii_letters + string.digits


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _audit(
    db: Session,
    principal: Principal,
    ip: str,
    action: str,
    target_id: object,
    result: str,
    detail: str | None,
) -> None:
    audit_svc.record(
        db,
        actor_type=principal.type,
        actor_name=principal.name,
        action=action,
        target_type="api_key",
        target_id=str(target_id),
        result=result,
        detail=detail,
        ip=ip,
    )


def _reject_api_key_actor(db: Session, principal: Principal, ip: str, action: str) -> None:
    """keys 写操作仅接受 JWT 用户主体;API Key 主体即使有 admin scope 也 403。"""
    if principal.type != TYPE_USER:
        _audit(db, principal, ip, action, "-", "error", "api key principal is not allowed to manage keys")
        raise ApiError(403, "forbidden", "api key management writes require a user principal")


def _validate_scopes(scopes: list[str]) -> list[str]:
    invalid = sorted({s for s in scopes if s not in ALL_SCOPES})
    if not scopes or invalid:
        raise ApiError(
            400,
            "invalid_scope",
            f"scopes must be a non-empty subset of {list(ALL_SCOPES)}; invalid: {invalid}",
        )
    seen: list[str] = []
    for s in scopes:
        if s not in seen:
            seen.append(s)
    return seen


class KeyCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    scopes: list[str]


class KeyPatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    scopes: list[str] | None = None
    enabled: bool | None = None


@router.get("")
async def list_keys(
    principal: Principal = Depends(require_scope("admin")), db: Session = Depends(get_db)
) -> dict:
    rows = db.execute(select(ApiKey).order_by(ApiKey.id)).scalars().all()
    items = [
        {
            "id": r.id,
            "name": r.name,
            "key_prefix": r.key_prefix,
            "scopes": r.scope_list(),
            "enabled": r.enabled,
            "created_at": r.created_at,
            "last_used_at": r.last_used_at,
        }
        for r in rows
    ]
    return {"total": len(items), "items": items}


@router.post("", status_code=201)
async def create_key(
    body: KeyCreateBody,
    request: Request,
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
) -> dict:
    ip = _client_ip(request)
    _reject_api_key_actor(db, principal, ip, "key.create")
    try:
        scopes = _validate_scopes(body.scopes)
    except ApiError as e:
        _audit(db, principal, ip, "key.create", "-", "error", f"invalid scopes: {body.scopes!r}")
        raise e
    plaintext = "dka_" + "".join(secrets.choice(_BASE62) for _ in range(43))
    row = ApiKey(
        name=body.name,
        key_hash=hashlib.sha256(plaintext.encode("utf-8")).hexdigest(),
        key_prefix=plaintext[:8],
        scopes=json.dumps(scopes),
        enabled=True,
        created_at=now_iso(),
    )
    db.add(row)
    db.flush()
    _audit(
        db, principal, ip, "key.create", row.id, "success",
        f"name={row.name} scopes={','.join(scopes)}",
    )
    return {"id": row.id, "key": plaintext}


@router.patch("/{key_id}")
async def update_key(
    key_id: int,
    body: KeyPatchBody,
    request: Request,
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
) -> dict:
    ip = _client_ip(request)
    _reject_api_key_actor(db, principal, ip, "key.update")
    row = db.get(ApiKey, key_id)
    if row is None:
        _audit(db, principal, ip, "key.update", key_id, "error", "key not found")
        raise not_found("key_not_found", f"no such api key: {key_id}")
    changed: list[str] = []
    if body.name is not None:
        row.name = body.name
        changed.append("name")
    if body.scopes is not None:
        try:
            scopes = _validate_scopes(body.scopes)
        except ApiError as e:
            _audit(db, principal, ip, "key.update", key_id, "error", f"invalid scopes: {body.scopes!r}")
            raise e
        row.scopes = json.dumps(scopes)
        changed.append("scopes")
    if body.enabled is not None:
        row.enabled = body.enabled
        changed.append("enabled")
    if not changed:
        _audit(db, principal, ip, "key.update", key_id, "error", "no fields to update")
        raise ApiError(400, "validation_error", "no fields to update")
    _audit(db, principal, ip, "key.update", key_id, "success", ",".join(changed))
    return {"status": "ok"}


@router.delete("/{key_id}")
async def delete_key(
    key_id: int,
    request: Request,
    principal: Principal = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
) -> dict:
    ip = _client_ip(request)
    _reject_api_key_actor(db, principal, ip, "key.delete")
    row = db.get(ApiKey, key_id)
    if row is None:
        _audit(db, principal, ip, "key.delete", key_id, "error", "key not found")
        raise not_found("key_not_found", f"no such api key: {key_id}")
    name = row.name
    db.delete(row)
    _audit(db, principal, ip, "key.delete", key_id, "success", f"name={name}")
    return {"status": "ok"}
