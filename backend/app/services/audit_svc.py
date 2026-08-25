"""审计服务,见 docs/data-model.md 与 docs/logging.md。

记录点约定: 变更类操作由路由层调用 record();审计只增不改。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog
from app.util import now_iso

MAX_DETAIL = 500


def record(
    db: Session,
    *,
    actor_type: str,
    actor_name: str,
    action: str,
    target_type: str,
    target_id: str,
    result: str,
    detail: str | None = None,
    ip: str | None = None,
) -> None:
    """写一条审计。失败不阻断业务(由调用方决定),detail 截断 500 字符,永不含密钥明文。"""
    if detail and len(detail) > MAX_DETAIL:
        detail = detail[:MAX_DETAIL] + "..."
    db.add(
        AuditLog(
            actor_type=actor_type,
            actor_name=actor_name,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            result=result,
            detail=detail,
            ip=ip,
        )
    )
    db.commit()


def list_audit(
    db: Session,
    *,
    actor: str | None = None,
    action: str | None = None,
    target: str | None = None,
    since: str | None = None,
    until: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    from sqlalchemy import func

    q = select(AuditLog).order_by(AuditLog.id.desc())
    if actor:
        q = q.where(AuditLog.actor_name == actor)
    if action:
        q = q.where(AuditLog.action == action)
    if target:
        q = q.where(AuditLog.target_id.contains(target))
    if since:  # ts 为 ISO 8601 UTC TEXT,字符串比较即时间序
        q = q.where(AuditLog.ts >= since)
    if until:
        q = q.where(AuditLog.ts <= until)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar() or 0
    rows = db.execute(q.offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return {
        "total": total,
        "items": [
            {
                "id": r.id,
                "ts": r.ts,
                "actor_type": r.actor_type,
                "actor_name": r.actor_name,
                "action": r.action,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "result": r.result,
                "detail": r.detail,
                "ip": r.ip,
            }
            for r in rows
        ],
    }


def cleanup(db: Session, retention_days: int) -> int:
    """删除超期审计,返回删除条数。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat(timespec="seconds")
    rows = db.execute(select(AuditLog).where(AuditLog.ts < cutoff)).scalars().all()
    for r in rows:
        db.delete(r)
    db.commit()
    return len(rows)
