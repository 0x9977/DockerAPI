"""系统端点: /api/health(豁免,无版本前缀) 与 /api/v1/version。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from app import __version__
from app.auth.deps import require_scope
from app.errors import ApiError

health_router = APIRouter(prefix="/api", tags=["system"])
router = APIRouter(prefix="/api/v1", tags=["system"])


@health_router.get("/health")
async def health() -> dict:
    from app.db import get_engine

    def _ping() -> None:
        conn = get_engine().connect()
        conn.close()

    db_ok = True
    try:
        await asyncio.to_thread(_ping)
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "db": db_ok}


def _daemon_summary(c) -> dict:
    """docker-py 同步调用,须在线程池执行(architecture.md 决策 8)。"""
    dv = c.version()
    try:
        info = c.info()
    except Exception:
        info = {}
    containers = info.get("Containers") or {}
    if isinstance(containers, int):  # 某些版本直接给总数
        containers = {}
    vols = info.get("Volumes")
    return {
        "panel": __version__,
        "docker": dv.get("Version"),
        "api_version": dv.get("ApiVersion"),
        "os": dv.get("Os"),
        "storage_driver": info.get("Driver"),
        "images_count": info.get("Images"),
        "volumes_count": len(vols) if isinstance(vols, list) else vols,
        "containers_summary": {
            "running": containers.get("Running"),
            "paused": containers.get("Paused"),
            "stopped": containers.get("Stopped"),
            "all": info.get("Containers"),
        },
    }


@router.get("/version")
async def version(principal=Depends(require_scope("view"))) -> dict:
    from app.config import settings
    from app.services import docker_client

    try:
        c = docker_client.get_client()
        summary = await asyncio.to_thread(_daemon_summary, c)
    except ApiError:
        raise
    except Exception as e:  # daemon 不可达也要返回面板版本
        return {
            "panel": __version__,
            "docker": None,
            "error": str(e)[:200],
            "docker_host": settings.docker_host,
        }
    summary["docker_host"] = settings.docker_host
    return summary
