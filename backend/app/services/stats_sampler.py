"""stats 采样器 — 见 docs/api.md「stats 采样」与 docs/logging.md。

后台 asyncio 任务每 ``_INTERVAL`` 秒(默认 10s;测试 monkeypatch 成 0.05)对
全部 running 容器各采一帧 ``container.stats(stream=False)``(docker-py 同步
调用一律 ``asyncio.to_thread`` 桥接;单容器采集异常只 loguru debug 跳过),
存入每容器 ``collections.deque(maxlen=_MAX_POINTS)``(360 点 ≈ 1h,仅内存)。

cpu 公式(daemon 差分):
    (cpu_stats.cpu_usage.total_usage - precpu_stats.cpu_usage.total_usage)
    / (cpu_stats.system_cpu_usage - precpu_stats.system_cpu_usage)
    * online_cpus * 100
precpu 缺失或分母 <= 0 → 0.0;online_cpus 缺失时回退
len(cpu_usage.percpu_usage)。mem: usage/limit 各除 1048576。
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

from docker.errors import NotFound
from loguru import logger

from app.errors import ApiError, map_daemon_error
from app.services import docker_client
from app.util import now_iso

_INTERVAL = 10.0          # 采样间隔(秒);测试可 monkeypatch
_MAX_POINTS = 360         # 每容器保留点数(约 1h)
_MIB = 1048576.0

_buffers: dict[str, deque[dict]] = {}
_task: asyncio.Task[None] | None = None


# ---------------------------------------------------------------- 生命周期


async def start() -> None:
    """启动后台采样任务(main.py lifespan 调用);幂等。"""
    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop())
    logger.info("stats sampler started (interval={}s)", _INTERVAL)


async def stop() -> None:
    """取消后台任务;幂等。"""
    global _task
    t, _task = _task, None
    if t is None:
        return
    t.cancel()
    await asyncio.gather(t, return_exceptions=True)
    logger.info("stats sampler stopped")


async def _loop() -> None:
    while True:
        await asyncio.sleep(_INTERVAL)
        try:
            await _sample_all()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - 单轮失败不终止采样
            logger.debug("stats sample cycle failed: {}", e)


async def _sample_all() -> None:
    def _list() -> list[Any]:
        return docker_client.get_client().containers.list(filters={"status": "running"})

    try:
        containers = await asyncio.to_thread(_list)
    except Exception as e:  # noqa: BLE001 - daemon 不可用,本轮跳过
        logger.debug("stats: list running containers failed: {}", e)
        return

    # 缓冲收割(审计 C9): 条目过多时,清掉已不存在容器的缓冲
    if len(_buffers) > 500:
        alive = {getattr(c, "id", None) for c in containers}
        for k in [k for k in _buffers if k not in alive]:
            _buffers.pop(k, None)
    for c in containers:
        cid = getattr(c, "id", None) or getattr(c, "name", "?")
        try:
            stats = await asyncio.to_thread(c.stats, stream=False)
            point = _make_point(stats)
        except Exception as e:  # noqa: BLE001 - 单容器失败只跳过
            logger.debug("stats: sample {} failed: {}", cid, e)
            continue
        _append(getattr(c, "id", None) or cid, point)


# ---------------------------------------------------------------- 点构造


def _online_cpus(cpu_stats: dict) -> int:
    online = cpu_stats.get("online_cpus")
    if online:
        return int(online)
    percpu = (cpu_stats.get("cpu_usage") or {}).get("percpu_usage")
    return len(percpu) if percpu else 0


def _make_point(stats: dict) -> dict:
    """daemon 单帧 stats → 采样点(公式见模块 docstring)。"""
    cpu = stats.get("cpu_stats") or {}
    pre = stats.get("precpu_stats") or {}
    usage = (cpu.get("cpu_usage") or {}).get("total_usage")
    pre_usage = (pre.get("cpu_usage") or {}).get("total_usage")
    system_now = cpu.get("system_cpu_usage")
    system_pre = pre.get("system_cpu_usage")
    denom = None if (system_now is None or system_pre is None) else system_now - system_pre

    if (
        usage is None
        or pre_usage is None
        or denom is None
        or denom <= 0
        or usage < pre_usage  # 计数器回绕/容器重启,差值无意义
    ):
        cpu_percent = 0.0
    else:
        cpu_percent = (usage - pre_usage) / denom * _online_cpus(cpu) * 100

    mem = stats.get("memory_stats") or {}
    return {
        "ts": now_iso(),
        "cpu_percent": float(cpu_percent),
        "mem_mb": float((mem.get("usage") or 0) / _MIB),
        "mem_limit_mb": float((mem.get("limit") or 0) / _MIB),
    }


def _append(cid: str, point: dict) -> None:
    buf = _buffers.get(cid)
    if buf is None:
        buf = deque(maxlen=_MAX_POINTS)
        _buffers[cid] = buf
    buf.append(point)


# ---------------------------------------------------------------- 查询


async def get_stats(cid: str, since: str | None = None) -> list[dict]:
    """``GET /containers/{id}/stats`` 的 Service 层。

    有缓冲 → 返回缓冲序列;无缓冲时容器 running → 现采一帧返回(并补入
    缓冲),非 running → [];容器不存在 → 404。since 给定时按 ts >= since
    过滤(ISO 8601 UTC 字符串序即时间序)。
    """
    try:
        container = await asyncio.to_thread(docker_client.get_client().containers.get, cid)
    except NotFound as e:
        raise ApiError(404, "container_not_found", f"No such container: {cid}") from e
    except ApiError:
        raise
    except Exception as e:  # noqa: BLE001 - daemon 不可达/超时(审计 C2)
        raise map_daemon_error(e) from e

    key: str = getattr(container, "id", cid)
    buf = _buffers.get(key)
    points: list[dict] = list(buf) if buf else []

    if not points:
        attrs = getattr(container, "attrs", None) or {}
        running = bool(attrs.get("State", {}).get("Running"))
        if not running:
            return []
        try:
            stats = await asyncio.to_thread(container.stats, stream=False)
        except NotFound as e:
            raise ApiError(404, "container_not_found", f"No such container: {cid}") from e
        except Exception as e:  # noqa: BLE001 - daemon 采集失败
            raise map_daemon_error(e) from e
        point = _make_point(stats)
        _append(key, point)
        points = [point]

    if since is not None:
        points = [p for p in points if p["ts"] >= since]
    return points


def get_recent(cid: str, n: int = 30) -> list[dict]:
    """最近 n 点同步内存快照(container_svc 列表内嵌用)。

    不触发采集、不抛 404;无缓冲返回 []。
    """
    buf = _buffers.get(cid)
    if not buf or n <= 0:
        return []
    return list(buf)[-n:]
