"""job_mgr 单测: 全生命周期(queued→running→done/failed/timeout)、输出缓冲截头、
审计调用、同栈串行、全局并发上限、流式回放/实时增量、启动恢复与清理。

子进程用 monkeypatch asyncio.create_subprocess_exec 换 FakeProc(可控输出/退出码/挂起);
审计用 monkeypatch audit_svc.record 收集调用。
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import delete

from app.config import settings
from app.db import session
from app.errors import ApiError
from app.models import Job
from app.services import audit_svc, job_mgr

TERMINAL = ("done", "failed", "timeout")


# ---------- 假子进程 ----------


class FakeStreamReader:
    """pre 行立即输出;有 gate 时阻塞,放行后输出 post 行,再 EOF。"""

    def __init__(self, pre: list[bytes], post: list[bytes], gate: asyncio.Event | None) -> None:
        self._pre = pre
        self._post = post
        self._gate = gate

    async def readline(self) -> bytes:
        if self._pre:
            return self._pre.pop(0)
        if self._gate is not None and not self._gate.is_set():
            await self._gate.wait()
        if self._post:
            return self._post.pop(0)
        return b""


class FakeProc:
    def __init__(
        self,
        lines: list[bytes],
        post_lines: list[bytes],
        exit_code: int,
        gate: asyncio.Event | None,
        delay: float,
        tracker: Tracker | None,
    ) -> None:
        self.stdout: Any = FakeStreamReader(lines, post_lines, gate)
        self._exit_code = exit_code
        self._gate = gate
        self._delay = delay
        self._tracker = tracker
        self._waited = False
        self.killed = False
        self.returncode: int | None = None
        self.spawn_t: float = 0.0

    def kill(self) -> None:
        self.killed = True
        if self._gate is not None:
            self._gate.set()

    async def wait(self) -> int | None:
        if not self._waited:
            self._waited = True
            if self._delay and not self.killed:
                await asyncio.sleep(self._delay)
            if self._gate is not None and not self._gate.is_set() and not self.killed:
                await self._gate.wait()
            if self.returncode is None:
                self.returncode = -9 if self.killed else self._exit_code
            if self._tracker is not None:
                self._tracker.record_exit(self)
        return self.returncode


class Tracker:
    """记录进程执行区间(spawn→wait 返回)与并发峰值。"""

    def __init__(self) -> None:
        self.intervals: list[tuple[float, float]] = []
        self.active = 0
        self.max_active = 0

    def record_spawn(self, proc: FakeProc) -> None:
        proc.spawn_t = time.monotonic()
        self.active += 1
        self.max_active = max(self.max_active, self.active)

    def record_exit(self, proc: FakeProc) -> None:
        self.active -= 1
        self.intervals.append((proc.spawn_t, time.monotonic()))


# ---------- 夹具 ----------


@pytest.fixture()
async def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "stacks_dir", tmp_path)
    (tmp_path / "demo").mkdir()

    # 清空前序测试留下的任务行,保证隔离
    db = session()
    db.execute(delete(Job))
    db.commit()
    db.close()

    cfg: dict[str, Any] = {
        "lines": ["default output\n"],
        "post_lines": [],
        "exit_code": 0,
        "gate": None,
        "delay": 0.0,
    }
    tracker = Tracker()
    procs: list[FakeProc] = []
    cmds: list[tuple[tuple, dict]] = []

    def configure(
        lines: list[str] | None = None,
        exit_code: int = 0,
        gate: asyncio.Event | None = None,
        delay: float = 0.0,
        post_lines: list[str] | None = None,
    ) -> None:
        cfg["lines"] = [ln.encode() for ln in (lines if lines is not None else ["default output\n"])]
        cfg["post_lines"] = [ln.encode() for ln in (post_lines or [])]
        cfg["exit_code"] = exit_code
        cfg["gate"] = gate
        cfg["delay"] = delay

    async def fake_exec(*cmd: str, **kw) -> FakeProc:
        cmds.append((cmd, kw))
        proc = FakeProc(
            list(cfg["lines"]), list(cfg["post_lines"]), cfg["exit_code"], cfg["gate"], cfg["delay"], tracker
        )
        tracker.record_spawn(proc)
        procs.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    audit_calls: list[dict] = []
    monkeypatch.setattr(audit_svc, "record", lambda db, **kw: audit_calls.append(kw))

    e = SimpleNamespace(
        configure=configure, tracker=tracker, procs=procs, cmds=cmds, audit=audit_calls
    )
    yield e
    await job_mgr.stop()


async def _wait_terminal(job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d = await job_mgr.get(job_id)
        if d["status"] in TERMINAL:
            return d
        await asyncio.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach terminal state")


def _submit(job_type: str, stack: str, actor: dict | None = None):
    return job_mgr.submit_compose(
        job_type, stack, ["-f", f"/stacks/{stack}/compose.yaml", "-p", stack, "up", "-d"], actor
    )


# ---------- 生命周期 ----------


async def test_job_done_lifecycle_and_audit(env):
    env.configure(lines=["line one\n", "line two\n"], exit_code=0)
    jid = await _submit("stack.up", "demo", actor={"type": "user", "name": "admin", "ip": "1.2.3.4"})
    d = await _wait_terminal(jid)

    assert d["status"] == "done"
    assert d["exit_code"] == 0
    assert "line one" in d["output"] and "line two" in d["output"]
    assert d["type"] == "stack.up" and d["stack"] == "demo"
    assert d["created_at"] and d["started_at"] and d["finished_at"]

    cmd, kw = env.cmds[0]
    assert cmd[:2] == ("docker", "compose")
    assert kw.get("cwd") == str(settings.stacks_dir / "demo")
    assert set(kw.get("env", {}).keys()) <= {"PATH", "DOCKER_HOST", "HOME"}
    assert kw.get("stdout") == asyncio.subprocess.PIPE
    assert kw.get("stderr") == asyncio.subprocess.STDOUT

    recs = [a for a in env.audit if a["action"] == "stack.up"]
    assert len(recs) == 1
    rec = recs[0]
    assert rec["result"] == "success"
    assert rec["actor_type"] == "user" and rec["actor_name"] == "admin" and rec["ip"] == "1.2.3.4"
    assert rec["target_type"] == "stack" and rec["target_id"] == "demo"
    assert f"job={jid}" in rec["detail"] and "exit=0" in rec["detail"]


async def test_job_failed_nonzero_exit(env):
    env.configure(lines=["pulling...\n", "error: no such service\n"], exit_code=17)
    jid = await _submit("stack.down", "demo")
    d = await _wait_terminal(jid)
    assert d["status"] == "failed"
    assert d["exit_code"] == 17
    assert "no such service" in d["output"]
    rec = [a for a in env.audit if a["action"] == "stack.down"][0]
    assert rec["result"] == "error" and "exit=17" in rec["detail"]


async def test_job_timeout_kills_and_double_audit(env, monkeypatch):
    monkeypatch.setattr(settings, "compose_job_timeout", 0.2)
    env.configure(lines=["starting...\n"], exit_code=0, gate=asyncio.Event())
    jid = await _submit("stack.restart", "demo")
    d = await _wait_terminal(jid)
    assert d["status"] == "timeout"
    assert env.procs[0].killed is True
    assert "[job timeout, process killed]" in d["output"]

    actions = [a["action"] for a in env.audit]
    assert "job.timeout" in actions and "stack.restart" in actions
    jrec = [a for a in env.audit if a["action"] == "job.timeout"][0]
    assert jrec["result"] == "error"
    assert jrec["target_type"] == "job" and jrec["target_id"] == jid
    assert jrec["actor_type"] == "system"
    srec = [a for a in env.audit if a["action"] == "stack.restart"][0]
    assert srec["result"] == "error"


async def test_job_default_actor_is_system(env):
    jid = await _submit("stack.up", "demo", actor=None)
    await _wait_terminal(jid)
    rec = [a for a in env.audit if a["action"] == "stack.up"][0]
    assert rec["actor_type"] == "system" and rec["actor_name"] == "system"
    assert rec["ip"] is None


async def test_missing_stack_dir_marks_failed(env):
    env.configure(lines=["x\n"], exit_code=0)
    jid = await _submit("stack.up", "ghost")  # ghost 目录不存在
    d = await _wait_terminal(jid)
    assert d["status"] == "failed"
    assert "stack directory not found" in d["output"]
    rec = [a for a in env.audit if a["action"] == "stack.up"][0]
    assert rec["result"] == "error"
    assert env.cmds == []  # 未 spawn 子进程


async def test_output_buffer_head_trim(env):
    big = "A" * 300_000 + "\n"
    env.configure(lines=[big, "tail-marker\n"], exit_code=0)
    jid = await _submit("stack.up", "demo")
    d = await _wait_terminal(jid)
    limit = job_mgr.MAX_OUTPUT
    assert limit == 256 * 1024
    # 期望 = 原始全量输出截头保最后 256KB
    expected = (big + "tail-marker\n")[-limit:]
    assert len(d["output"]) == limit
    assert d["output"] == expected
    assert d["output"].endswith("tail-marker\n")
    assert not d["output"].startswith("A" * 300_000)  # 头部确被截掉


# ---------- 并发约束 ----------


async def test_same_stack_serialized(env):
    (settings.stacks_dir / "demo").mkdir(exist_ok=True)
    env.configure(lines=["work\n"], exit_code=0, delay=0.08)
    jids = [await _submit("stack.up", "demo") for _ in range(3)]
    for jid in jids:
        await _wait_terminal(jid)
    intervals = sorted(env.tracker.intervals)
    assert len(intervals) == 3
    for (_s1, e1), (s2, _e2) in zip(intervals, intervals[1:]):
        assert s2 >= e1 - 0.005, "same-stack compose jobs must not overlap"


async def test_global_concurrency_capped_at_three(env):
    for i in range(5):
        (settings.stacks_dir / f"s{i}").mkdir()
    env.configure(lines=["work\n"], exit_code=0, delay=0.25)
    jids = [
        await job_mgr.submit_compose("stack.up", f"s{i}", ["-p", f"s{i}", "up", "-d"]) for i in range(5)
    ]
    for jid in jids:
        await _wait_terminal(jid, timeout=10)
    assert env.tracker.max_active <= 3
    assert env.tracker.max_active >= 2  # 跨栈确实并行(不是全局串行)


# ---------- 查询 ----------


async def test_get_unknown_job_404(env):
    with pytest.raises(ApiError) as err:
        await job_mgr.get("j_nope")
    assert err.value.status == 404 and err.value.code == "job_not_found"
    with pytest.raises(ApiError):
        async for _ev in job_mgr.stream("j_nope"):
            pass


async def test_list_jobs_pagination_desc(env):
    env.configure(lines=["x\n"], exit_code=0)
    jids = [await _submit("stack.up", "demo") for _ in range(3)]
    for jid in jids:
        await _wait_terminal(jid)
    result = await job_mgr.list_jobs(page=1, page_size=2)
    assert result["total"] == 3
    assert len(result["items"]) == 2
    all_page = await job_mgr.list_jobs(page=1, page_size=20)
    ids = [item["id"] for item in all_page["items"]]
    assert set(jids) <= set(ids)
    assert ids == sorted(ids, reverse=True)  # id 倒序(ULID 时间有序)
    assert all(item["status"] == "done" for item in all_page["items"])


# ---------- 流式 ----------


async def test_stream_replay_after_finish(env):
    env.configure(lines=["out1\n", "out2\n"], exit_code=0)
    jid = await _submit("stack.up", "demo")
    await _wait_terminal(jid)
    events = [ev async for ev in job_mgr.stream(jid)]
    assert events[0] == {"data": {"chunk": "out1\nout2\n"}}
    assert events[-1] == {"event": "end", "data": {"status": "done", "exit_code": 0}}
    assert len(events) == 2


async def test_stream_live_incremental(env):
    gate = asyncio.Event()
    env.configure(lines=["first\n"], post_lines=["second\n"], exit_code=0, gate=gate)
    jid = await _submit("stack.up", "demo")
    collected: list[dict] = []

    async def consume() -> None:
        async for ev in job_mgr.stream(jid):
            collected.append(ev)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.15)
    assert {"data": {"chunk": "first\n"}} in collected  # 已实时收到第一行
    gate.set()  # 放行第二行并退出
    await asyncio.wait_for(task, timeout=3)
    assert {"data": {"chunk": "second\n"}} in collected
    assert collected[-1] == {"event": "end", "data": {"status": "done", "exit_code": 0}}
    assert job_mgr._subscribers.get(jid) is None  # 订阅者已清理


async def test_stream_failed_end_event(env):
    env.configure(lines=["bad\n"], exit_code=3)
    jid = await _submit("stack.up", "demo")
    await _wait_terminal(jid)
    events = [ev async for ev in job_mgr.stream(jid)]
    assert events[-1] == {"event": "end", "data": {"status": "failed", "exit_code": 3}}


# ---------- 启动恢复与清理 ----------


async def test_start_recovers_stale_jobs(env):
    db = session()
    db.add(Job(id="j_stale_running", type="stack.up", stack="demo", status="running", output="partial"))
    db.add(Job(id="j_stale_queued", type="stack.up", stack="demo", status="queued"))
    db.add(Job(id="j_kept_done", type="stack.up", stack="demo", status="done",
               finished_at="2026-08-25T00:00:00Z"))
    db.commit()

    await job_mgr.start()  # 触发恢复
    r1 = db.get(Job, "j_stale_running")
    r2 = db.get(Job, "j_stale_queued")
    r3 = db.get(Job, "j_kept_done")
    db.close()

    assert r1.status == "failed" and "interrupted" in r1.output and r1.finished_at
    assert r2.status == "failed" and "interrupted" in r2.output
    assert r3.status == "done"  # 已终结不受影响
    await job_mgr.stop()


async def test_start_idempotent(env):
    await job_mgr.start()
    first = job_mgr._worker_task
    await job_mgr.start()
    assert job_mgr._worker_task is first
    await job_mgr.stop()
    assert job_mgr._worker_task is None
    await job_mgr.stop()  # 重复 stop 无害


async def test_cleanup_once_removes_old_finished_jobs(env, monkeypatch):
    old = (
        (datetime.now(timezone.utc) - timedelta(days=8))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    db = session()
    db.add(Job(id="j_old_done", type="stack.up", stack="demo", status="done", finished_at=old))
    db.add(Job(id="j_old_failed", type="stack.up", stack="demo", status="failed", finished_at=old))
    db.add(Job(id="j_new_done", type="stack.up", stack="demo", status="done",
               finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")))
    db.add(Job(id="j_running", type="stack.up", stack="demo", status="running"))
    db.commit()
    db.close()

    cleanup_calls: list[tuple] = []
    monkeypatch.setattr(audit_svc, "cleanup", lambda db, days: cleanup_calls.append(days))
    job_mgr._cleanup_once()

    db = session()
    assert db.get(Job, "j_old_done") is None
    assert db.get(Job, "j_old_failed") is None
    assert db.get(Job, "j_new_done") is not None  # 7 天内保留
    assert db.get(Job, "j_running") is not None  # 未终结不清理
    db.close()
    assert cleanup_calls == [settings.audit_retention_days]
