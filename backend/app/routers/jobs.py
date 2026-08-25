"""长任务端点,见 docs/api.md。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.deps import require_scope
from app.services import job_mgr

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(page: int = 1, page_size: int = 20, principal=Depends(require_scope("view"))) -> dict:
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    return await job_mgr.list_jobs(page=page, page_size=page_size)


@router.get("/{job_id}")
async def get_job(job_id: str, principal=Depends(require_scope("view"))) -> dict:
    return await job_mgr.get(job_id)


@router.get("/{job_id}/stream")
async def stream_job(job_id: str, principal=Depends(require_scope("view"))):
    from sse_starlette.sse import EventSourceResponse

    from app.sse import sse_json_frames

    return EventSourceResponse(sse_json_frames(job_mgr.stream(job_id)))
