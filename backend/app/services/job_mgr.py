"""长任务管理 — 实现负责人: compose/job subagent(见 docs/api.md 长任务、data-model.md jobs、architecture.md 决策9)。

接口契约(路由层已按此调用,不得改签名):

submit_compose(job_type: str, stack: str, args: list[str], actor: dict | None = None) -> str
    建 Job 行(id="j_"+util.ulid_new(),type=job_type,stack,status=queued)→ 入队 → 返回 job_id。
    执行: asyncio.create_subprocess_exec("docker","compose",*args,...),
    环境白名单 PATH/DOCKER_HOST/HOME,cwd=栈目录(由调用方在 args 中带 -f 全路径,
    cwd 用 settings.stacks_dir/<stack>),超时 settings.compose_job_timeout → 杀进程标 timeout。
    输出合并 stdout+stderr 写内存滚动缓冲(超 256KB 截头)并持久化到 Job.output。
    并发约束: 同栈串行(dict[stack,asyncio.Lock]),全局并发上限 3(asyncio.Semaphore)。

审计(决策9): 任务终态时写审计行——done→audit action=job_type result=success;
failed→result=error;timeout→额外一条 action="job.timeout" + job_type 行 result=error。
detail 携带 job_id;actor 用提交时传入的 dict(type/name/ip),缺省 system。

start() / stop()
    main.py lifespan 调用。start(): 启动 worker 循环 + 每日清理任务
    (终结任务保留 7 天;顺带 audit_svc.cleanup(db, settings.audit_retention_days));
    启动时把 queued/running 残留(进程重启)标 failed("interrupted")。
    stop(): 取消后台任务。幂等: 重复调用不重复启动。

get(job_id) -> dict
    {id,type,stack,status,exit_code,output,created_at,started_at,finished_at}
    不存在抛 ApiError(404, "job_not_found")。

list_jobs(page=1, page_size=20) -> dict {"total":N,"items":[...]}(按 id 倒序)

stream(job_id) -> AsyncGenerator[dict, None]
    SSE 事件源: 先回放已有输出({"chunk": ...}),再实时增量;终结时 yield
    {"event":"end","data":{"status":...,"exit_code":...}}。job 不存在抛 404。
    每任务用订阅者 asyncio.Queue 列表广播;订阅者断开即移除。
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

from loguru import logger
from sqlalchemy import delete, func, select

from app.config import settings
from app.db import session
from app.errors import not_found
from app.models import Job
from app.services import audit_svc
from app.util import now_iso, ulid_new

MAX_OUTPUT = 256 * 1024
TERMINAL = ("done", "failed", "timeout")
JOB_RETENTION_DAYS = 7
_CLEANUP_INTERVAL_S = 86400
_GLOBAL_CONCURRENCY = 3
_ENV_WHITELIST = ("PATH", "DOCKER_HOST", "HOME")

# ---- 模块级运行态(事件循环切换时自动重建,避免跨 loop 复用原语) ----
_worker_task: asyncio.Task | None = None
_cleanup_task: asyncio.Task | None = None
_queue: asyncio.Queue | None = None
_active_tasks: set[asyncio.Task] = set()

_stack_locks: dict[str, asyncio.Lock] = {}
_locks_loop: asyncio.AbstractEventLoop | None = None
_semaphore: asyncio.Semaphore | None = None
_sem_loop: asyncio.AbstractEventLoop | None = None

# 输出广播: _written 为累计写入字符数(截头不减),用于 stream 去重
_buffers: dict[str, str] = {}
_written: dict[str, int] = {}
_subscribers: dict[str, list[asyncio.Queue]] = {}


# ---------- 生命周期 ----------


def _ensure_started() -> None:
    """幂等启动 worker + 每日清理;事件循环变更时重建(测试多 loop 场景)。"""
    global _worker_task, _cleanup_task, _queue
    loop = asyncio.get_running_loop()
    if (
        _worker_task is not None
        and not _worker_task.done()
        and _worker_task.get_loop() is loop
    ):
        return
    _queue = asyncio.Queue()
    try:
        _recover_stale_jobs()
    except Exception:
        logger.warning("job recovery failed", exc_info=True)
    _worker_task = asyncio.create_task(_worker_loop(), name="job-mgr-worker")
    _cleanup_task = asyncio.create_task(_cleanup_loop(), name="job-mgr-cleanup")


async def start() -> None:
    _ensure_started()


async def stop() -> None:
    global _worker_task, _cleanup_task
    tasks = [t for t in (_worker_task, _cleanup_task) if t is not None and not t.done()]
    tasks.extend(t for t in _active_tasks if not t.done())
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    _worker_task = None
    _cleanup_task = None
    _active_tasks.clear()


def _recover_stale_jobs() -> None:
    """进程重启恢复: queued/running 残留一律标 failed("interrupted")。"""
    db = session()
    try:
        rows = db.execute(select(Job).where(Job.status.in_(["queued", "running"]))).scalars().all()
        for row in rows:
            row.status = "failed"
            row.finished_at = now_iso()
            row.output = (row.output or "") + "\n[interrupted: service restarted]\n"
        if rows:
            db.commit()
            logger.warning("recovered {} stale job(s) as failed", len(rows))
    finally:
        db.close()


async def _worker_loop() -> None:
    queue = _queue
    assert queue is not None
    while True:
        item = await queue.get()
        # 每个 job 独立 task: 并发约束由 同栈 Lock + 全局 Semaphore 落实,
        # worker 只负责消费队列;task 内部异常统一走 _fail_crashed 兜底。
        task = asyncio.create_task(_run_job_safe(item))
        _active_tasks.add(task)
        task.add_done_callback(_active_tasks.discard)
        task.add_done_callback(lambda _t: queue.task_done())


async def _run_job_safe(item: dict) -> None:
    try:
        await _run_job(item)
    except Exception as exc:
        logger.exception("job {} execution crashed", item.get("id"))
        _fail_crashed(item, exc)


async def _cleanup_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(_cleanup_once)
        except Exception:
            logger.warning("job cleanup failed", exc_info=True)
        await asyncio.sleep(_CLEANUP_INTERVAL_S)


def _cleanup_once() -> None:
    """终结超 7 天的 Job 删除 + 审计过期清理。"""
    cutoff = (
        (datetime.now(timezone.utc) - timedelta(days=JOB_RETENTION_DAYS))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    db = session()
    try:
        result = db.execute(
            delete(Job).where(
                Job.status.in_(TERMINAL),
                Job.finished_at.is_not(None),
                Job.finished_at < cutoff,
            )
        )
        db.commit()
        removed = result.rowcount or 0
        audit_svc.cleanup(db, settings.audit_retention_days)
        if removed:
            logger.info("job cleanup removed {} finished job(s)", removed)
    finally:
        db.close()


# ---------- 并发原语(loop 感知) ----------


def _stack_lock(stack: str) -> asyncio.Lock:
    global _locks_loop
    loop = asyncio.get_running_loop()
    if _locks_loop is not loop:
        _stack_locks.clear()
        _locks_loop = loop
    lock = _stack_locks.get(stack)
    if lock is None:
        lock = asyncio.Lock()
        _stack_locks[stack] = lock
    return lock


def _global_sem() -> asyncio.Semaphore:
    global _semaphore, _sem_loop
    loop = asyncio.get_running_loop()
    if _semaphore is None or _sem_loop is not loop:
        _semaphore = asyncio.Semaphore(_GLOBAL_CONCURRENCY)
        _sem_loop = loop
    return _semaphore


# ---------- 提交与执行 ----------


async def submit_compose(job_type: str, stack: str, args: list[str], actor: dict | None = None) -> str:
    job_id = "j_" + ulid_new()
    _ensure_started()  # 先确保 worker 在,再做行,避免恢复逻辑误伤新行
    db = session()
    try:
        db.add(Job(id=job_id, type=job_type, stack=stack, status="queued", output=""))
        db.commit()
    finally:
        db.close()
    assert _queue is not None
    await _queue.put(
        {"id": job_id, "type": job_type, "stack": stack, "args": list(args), "actor": actor}
    )
    logger.info("job {} submitted: {} {}", job_id, job_type, stack)
    return job_id


async def _run_job(item: dict) -> None:
    job_id: str = item["id"]
    stack: str = item["stack"]
    args: list[str] = item["args"]
    actor: dict = item.get("actor") or {"type": "system", "name": "system", "ip": None}
    async with _stack_lock(stack):
        async with _global_sem():
            db = session()
            try:
                job = db.get(Job, job_id)
                if job is None or job.status != "queued":
                    return
                job.status = "running"
                job.started_at = now_iso()
                db.commit()
            finally:
                db.close()
            exit_code, timed_out = await _execute(job_id, stack, args)
            status = "timeout" if timed_out else ("done" if exit_code == 0 else "failed")
            db = session()
            try:
                job = db.get(Job, job_id)
                if job is not None:
                    job.status = status
                    job.exit_code = exit_code
                    job.finished_at = now_iso()
                    db.commit()
            finally:
                db.close()
            _broadcast_end(job_id, status, exit_code)
            _write_audit(item["type"], stack, job_id, status, exit_code, actor, timed_out)
            logger.info("job {} finished: {} exit={}", job_id, status, exit_code)


async def _execute(job_id: str, stack: str, args: list[str]) -> tuple[int | None, bool]:
    """跑子进程;返回 (exit_code, timed_out)。输出逐行入缓冲/广播/落库。"""
    cwd = settings.stacks_dir / stack
    if not cwd.is_dir():
        _append_output(job_id, f"stack directory not found: {cwd}\n")
        return None, False
    env = {key: os.environ[key] for key in _ENV_WHITELIST if key in os.environ}
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "compose",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(cwd),
            env=env,
        )
    except OSError as exc:
        _append_output(job_id, f"failed to spawn docker compose: {exc}\n")
        return None, False
    try:
        async with asyncio.timeout(settings.compose_job_timeout):
            assert proc.stdout is not None
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                _append_output(job_id, raw.decode("utf-8", errors="replace"))
            code = await proc.wait()
            return code, False
    except TimeoutError:
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
        try:
            code = await proc.wait()
        except Exception:
            code = None
        _append_output(job_id, "\n[job timeout, process killed]\n")
        return code, True


def _fail_crashed(item: dict, exc: Exception) -> None:
    job_id = str(item.get("id", ""))
    db = session()
    try:
        job = db.get(Job, job_id)
        if job is not None and job.status not in TERMINAL:
            job.status = "failed"
            job.finished_at = now_iso()
            job.output = (job.output or "") + f"\n[internal error: {exc}]\n"
            db.commit()
    finally:
        db.close()
    _broadcast_end(job_id, "failed", None)
    actor = item.get("actor") or {"type": "system", "name": "system", "ip": None}
    _write_audit(
        str(item.get("type") or "job.unknown"),
        str(item.get("stack") or ""),
        job_id,
        "failed",
        None,
        actor,
        False,
    )


# ---------- 输出缓冲与广播 ----------

_FLUSH_INTERVAL = 2.0          # 输出落库节流间隔(秒);高频输出不逐行 commit
_last_flush: dict[str, float] = {}


def _append_output(job_id: str, text: str) -> None:
    """同步原子(无 await): 更新内存滚动缓冲 → 广播订阅者;落库按时间节流。

    逐行 commit 会在事件循环上造成 O(n²) 的 SQLite 写放大(审计 C4),
    运行中任务每 _FLUSH_INTERVAL 秒落一次库,终态时 _flush_output 收尾。
    """
    buf = _buffers.get(job_id, "") + text
    if len(buf) > MAX_OUTPUT:
        buf = buf[-MAX_OUTPUT:]
    _buffers[job_id] = buf
    _written[job_id] = _written.get(job_id, 0) + len(text)
    import time as _time

    now = _time.monotonic()
    if now - _last_flush.get(job_id, 0.0) >= _FLUSH_INTERVAL:
        _last_flush[job_id] = now
        _persist_output(job_id, buf)
    total = _written[job_id]
    for queue in list(_subscribers.get(job_id, [])):
        try:
            queue.put_nowait({"chunk": text, "_total": total})
            queue._lag = False  # type: ignore[attr-defined]
        except asyncio.QueueFull:
            if not getattr(queue, "_lag", False):  # 背压: 丢行 + 一次性标记(审计 C8)
                queue._lag = True  # type: ignore[attr-defined]
                try:
                    queue.put_nowait(
                        {"chunk": "\n[...实时输出过快,部分行丢弃,完整输出见任务输出...]\n", "_total": total}
                    )
                except Exception:
                    pass
        except Exception:
            pass


def _flush_output(job_id: str) -> None:
    """终态收尾: 把内存缓冲最终落库(任务结束时调用一次)。"""
    _last_flush.pop(job_id, None)
    buf = _buffers.get(job_id)
    if buf is not None:
        _persist_output(job_id, buf)


def _persist_output(job_id: str, buf: str) -> None:
    db = session()
    try:
        job = db.get(Job, job_id)
        if job is not None:
            job.output = buf
            db.commit()
    except Exception:
        logger.warning("job {} output persist failed", job_id)
    finally:
        db.close()


def _broadcast_end(job_id: str, status: str, exit_code: int | None) -> None:
    _flush_output(job_id)  # 终态先把内存缓冲完整落库(节流补尾)
    for queue in list(_subscribers.get(job_id, [])):
        try:
            queue.put_nowait({"__end__": {"status": status, "exit_code": exit_code}})
        except Exception:
            pass
    _buffers.pop(job_id, None)
    _written.pop(job_id, None)


def _unsubscribe(job_id: str, queue: asyncio.Queue) -> None:
    subs = _subscribers.get(job_id)
    if subs is None:
        return
    if queue in subs:
        subs.remove(queue)
    if not subs:
        _subscribers.pop(job_id, None)


# ---------- 审计(决策9) ----------


def _write_audit(
    job_type: str,
    stack: str,
    job_id: str,
    status: str,
    exit_code: int | None,
    actor: dict,
    timed_out: bool,
) -> None:
    detail = f"job={job_id} exit={exit_code}"
    db = session()
    try:
        if timed_out:
            audit_svc.record(
                db,
                actor_type="system",
                actor_name="system",
                action="job.timeout",
                target_type="job",
                target_id=job_id,
                result="error",
                detail=detail,
            )
        audit_svc.record(
            db,
            actor_type=str(actor.get("type") or "system"),
            actor_name=str(actor.get("name") or "system"),
            action=job_type,
            target_type="stack",
            target_id=stack,
            result="success" if status == "done" else "error",
            detail=detail,
            ip=actor.get("ip"),
        )
    except Exception:
        logger.warning("job audit write failed: {}", job_id)
    finally:
        db.close()


# ---------- 查询与流 ----------


def _to_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "type": job.type,
        "stack": job.stack,
        "status": job.status,
        "exit_code": job.exit_code,
        "output": job.output,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


async def get(job_id: str) -> dict:
    db = session()
    try:
        job = db.get(Job, job_id)
        if job is None:
            raise not_found("job_not_found", f"no such job: {job_id}")
        return _to_dict(job)
    finally:
        db.close()


async def list_jobs(page: int = 1, page_size: int = 20) -> dict:
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    db = session()
    try:
        total = db.execute(select(func.count()).select_from(Job)).scalar() or 0
        rows = (
            db.execute(select(Job).order_by(Job.id.desc()).offset((page - 1) * page_size).limit(page_size))
            .scalars()
            .all()
        )
        return {"total": total, "items": [_to_dict(row) for row in rows]}
    finally:
        db.close()


async def stream(job_id: str) -> AsyncGenerator[dict, None]:
    db = session()
    try:
        job = db.get(Job, job_id)
    finally:
        db.close()
    if job is None:
        raise not_found("job_not_found", f"no such job: {job_id}")
    if job.status in TERMINAL:
        if job.output:
            yield {"data": {"chunk": job.output}}
        yield {"event": "end", "data": {"status": job.status, "exit_code": job.exit_code}}
        return
    # 订阅 + 快照计数 + 重读输出在同一同步块完成(无 await),保证不丢行不重复
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _subscribers.setdefault(job_id, []).append(queue)
    snapshot_len = _written.get(job_id, 0)
    db = session()
    try:
        current = db.get(Job, job_id)
    finally:
        db.close()
    replay = (current.output if current is not None else "") or ""
    status = current.status if current is not None else "failed"
    exit_code = current.exit_code if current is not None else None
    if status in TERMINAL:
        _unsubscribe(job_id, queue)
        if replay:
            yield {"data": {"chunk": replay}}
        yield {"event": "end", "data": {"status": status, "exit_code": exit_code}}
        return
    if replay:
        yield {"data": {"chunk": replay}}
    try:
        while True:
            event = await queue.get()
            if "__end__" in event:
                yield {"event": "end", "data": event["__end__"]}
                return
            if event.get("_total", 0) <= snapshot_len:
                continue
            yield {"data": {"chunk": event["chunk"]}}
    finally:
        _unsubscribe(job_id, queue)
