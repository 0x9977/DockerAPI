"""Compose 栈端点,见 docs/api.md。变更操作为长任务,202 + job_id。

审计时序(docs 架构决策 9): 提交时不写审计,JobMgr 在任务终态时写,
actor 在此捕获并随任务传递。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.auth.deps import require_scope
from app.auth.principals import Principal
from app.errors import ApiError, not_found
from app.services import compose_svc

router = APIRouter(prefix="/stacks", tags=["stacks"])


def _actor(request: Request) -> dict:
    p: Principal | None = getattr(request.state, "principal", None)
    if p is None:
        return {"type": "system", "name": "system", "ip": None}
    return {
        "type": p.type,
        "name": p.name,
        "ip": request.client.host if request.client else None,
    }


@router.get("")
async def list_stacks(principal=Depends(require_scope("view"))) -> list[dict]:
    return await compose_svc.list_stacks()


@router.get("/{name}")
async def get_stack(name: str, principal=Depends(require_scope("view"))) -> dict:
    return await compose_svc.get_stack(name)


@router.post("/{name}/up", status_code=202)
async def stack_up(
    name: str, request: Request, principal=Depends(require_scope("start"))
) -> dict:
    return {"job_id": await compose_svc.stack_up(name, actor=_actor(request))}


@router.post("/{name}/down", status_code=202)
async def stack_down(
    name: str,
    request: Request,
    volumes: bool = False,
    principal=Depends(require_scope("stop")),
) -> dict:
    if volumes and not principal.has_scope("delete"):
        raise ApiError(403, "forbidden", "volumes=true requires delete scope")
    return {"job_id": await compose_svc.stack_down(name, volumes=volumes, actor=_actor(request))}


@router.post("/{name}/restart", status_code=202)
async def stack_restart(
    name: str, request: Request, principal=Depends(require_scope("start"))
) -> dict:
    return {"job_id": await compose_svc.stack_restart(name, actor=_actor(request))}


@router.get("/{name}/logs")
async def stack_logs(name: str, tail: int = 200, principal=Depends(require_scope("view"))) -> dict:
    return await compose_svc.stack_logs(name, tail=tail)
