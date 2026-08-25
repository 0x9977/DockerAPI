"""审计查询(admin)。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.deps import require_scope
from app.db import get_db
from app.services import audit_svc

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
async def list_audit(
    actor: str | None = None,
    action: str | None = None,
    target: str | None = None,
    since: str | None = None,
    until: str | None = None,
    page: int = 1,
    page_size: int = 20,
    principal=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
) -> dict:
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    return audit_svc.list_audit(
        db,
        actor=actor,
        action=action,
        target=target,
        since=since,
        until=until,
        page=page,
        page_size=page_size,
    )
