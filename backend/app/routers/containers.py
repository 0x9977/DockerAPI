"""容器端点,见 docs/api.md。变更操作落审计。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.auth.deps import require_scope
from app.auth.principals import Principal, TYPE_SYSTEM
from app.db import get_db
from app.services import audit_svc, container_svc, log_svc, stats_sampler

router = APIRouter(prefix="/containers", tags=["containers"])


def _actor(request: Request) -> tuple[str, str]:
    p: Principal | None = getattr(request.state, "principal", None)
    if p is None:
        return TYPE_SYSTEM, "system"
    return p.type, p.name


def _audit(
    request: Request,
    db: Session,
    action: str,
    target: str,
    result: str,
    detail: str | None = None,
) -> None:
    actor_type, actor_name = _actor(request)
    audit_svc.record(
        db,
        actor_type=actor_type,
        actor_name=actor_name,
        action=action,
        target_type="container",
        target_id=target,
        result=result,
        detail=detail,
        ip=request.client.host if request.client else None,
    )


@router.get("")
async def list_containers(principal=Depends(require_scope("view"))) -> list[dict]:
    return await container_svc.list_containers()


@router.get("/{cid}")
async def inspect_container(cid: str, principal=Depends(require_scope("view"))) -> dict:
    return await container_svc.inspect_container(cid)


@router.post("/{cid}/start")
async def start_container(
    cid: str, request: Request, principal=Depends(require_scope("start")), db: Session = Depends(get_db)
) -> dict:
    try:
        r = await container_svc.start_container(cid)
    except Exception as e:
        _audit(request, db, "container.start", cid, "error", str(e)[:200])
        raise
    _audit(request, db, "container.start", cid, "success")
    return r


@router.post("/{cid}/stop")
async def stop_container(
    cid: str,
    request: Request,
    t: int = Query(10, ge=0, le=600),
    principal=Depends(require_scope("stop")),
    db: Session = Depends(get_db),
) -> dict:
    try:
        r = await container_svc.stop_container(cid, t=t)
    except Exception as e:
        _audit(request, db, "container.stop", cid, "error", str(e)[:200])
        raise
    _audit(request, db, "container.stop", cid, "success", f"t={t}")
    return r


@router.post("/{cid}/restart")
async def restart_container(
    cid: str,
    request: Request,
    t: int = Query(10, ge=0, le=600),
    principal=Depends(require_scope("start")),
    db: Session = Depends(get_db),
) -> dict:
    try:
        r = await container_svc.restart_container(cid, t=t)
    except Exception as e:
        _audit(request, db, "container.restart", cid, "error", str(e)[:200])
        raise
    _audit(request, db, "container.restart", cid, "success", f"t={t}")
    return r


@router.post("/{cid}/pause")
async def pause_container(
    cid: str, request: Request, principal=Depends(require_scope("stop")), db: Session = Depends(get_db)
) -> dict:
    try:
        r = await container_svc.pause_container(cid)
    except Exception as e:
        _audit(request, db, "container.pause", cid, "error", str(e)[:200])
        raise
    _audit(request, db, "container.pause", cid, "success")
    return r


@router.post("/{cid}/unpause")
async def unpause_container(
    cid: str, request: Request, principal=Depends(require_scope("start")), db: Session = Depends(get_db)
) -> dict:
    try:
        r = await container_svc.unpause_container(cid)
    except Exception as e:
        _audit(request, db, "container.unpause", cid, "error", str(e)[:200])
        raise
    _audit(request, db, "container.unpause", cid, "success")
    return r


@router.delete("/{cid}")
async def remove_container(
    cid: str,
    request: Request,
    force: bool = False,
    principal=Depends(require_scope("delete")),
    db: Session = Depends(get_db),
) -> dict:
    try:
        r = await container_svc.remove_container(cid, force=force)
    except Exception as e:
        _audit(request, db, "container.remove", cid, "error", str(e)[:200])
        raise
    _audit(request, db, "container.remove", cid, "success", f"force={force}")
    return r


@router.get("/{cid}/logs")
async def container_logs(
    cid: str,
    tail: int = Query(200, ge=1, le=10000),
    timestamps: bool = False,
    principal=Depends(require_scope("view")),
) -> dict:
    return await log_svc.fetch_logs(cid, tail=tail, timestamps=timestamps)


@router.get("/{cid}/logs/stream")
async def container_logs_stream(
    cid: str, tail: int = Query(200, ge=1, le=10000), principal=Depends(require_scope("view"))
):
    from sse_starlette.sse import EventSourceResponse

    from app.sse import sse_json_frames

    return EventSourceResponse(sse_json_frames(log_svc.stream_logs(cid, tail=tail)))


@router.get("/{cid}/stats")
async def container_stats(
    cid: str, since: str | None = None, principal=Depends(require_scope("view"))
) -> list[dict]:
    return await stats_sampler.get_stats(cid, since=since)
