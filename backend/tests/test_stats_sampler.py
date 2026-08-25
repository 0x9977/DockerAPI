"""stats_sampler 单测: cpu 公式、环形缓冲 maxlen、后台循环、get_stats/get_recent。"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from docker.errors import NotFound

from app.errors import ApiError
from app.services import docker_client, stats_sampler


# ---------------------------------------------------------------- fakes


def make_stats(
    total: int = 1500,
    pre_total: int = 1000,
    sys_now: int = 2000,
    sys_pre: int = 1000,
    online: int | None = 4,
    percpu: list[int] | None = None,
    mem_usage: int = 2097152,
    mem_limit: int = 52428800,
    with_precpu: bool = True,
) -> dict:
    cpu_usage: dict[str, Any] = {"total_usage": total}
    if percpu is not None:
        cpu_usage["percpu_usage"] = percpu
    cpu: dict[str, Any] = {"cpu_usage": cpu_usage, "system_cpu_usage": sys_now}
    if online is not None:
        cpu["online_cpus"] = online
    stats: dict[str, Any] = {"cpu_stats": cpu, "memory_stats": {"usage": mem_usage, "limit": mem_limit}}
    if with_precpu:
        stats["precpu_stats"] = {
            "cpu_usage": {"total_usage": pre_total},
            "system_cpu_usage": sys_pre,
        }
    return stats


class FakeStatsContainer:
    def __init__(
        self,
        cid: str,
        stats: dict | None = None,
        stats_exc: Exception | None = None,
        running: bool = True,
    ) -> None:
        self.id = cid
        self.name = cid
        self.attrs = {"State": {"Running": running}}
        self._stats = stats
        self._exc = stats_exc
        self.calls = 0

    def stats(self, **kwargs: Any) -> dict:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        assert self._stats is not None
        return self._stats


class FakeContainers:
    def __init__(
        self,
        cmap: dict[str, FakeStatsContainer],
        running: list[FakeStatsContainer],
        list_exc: Exception | None = None,
    ) -> None:
        self._cmap = cmap
        self._running = running
        self._list_exc = list_exc
        self.list_calls: list[dict] = []

    def get(self, cid: str) -> FakeStatsContainer:
        if cid not in self._cmap:
            raise NotFound(f"No such container: {cid}")
        return self._cmap[cid]

    def list(self, **kwargs: Any) -> list[FakeStatsContainer]:
        self.list_calls.append(kwargs)
        if self._list_exc is not None:
            raise self._list_exc
        return self._running


class FakeDocker:
    def __init__(
        self,
        cmap: dict[str, FakeStatsContainer] | None = None,
        running: list[FakeStatsContainer] | None = None,
        list_exc: Exception | None = None,
    ) -> None:
        self.containers = FakeContainers(cmap or {}, running or [], list_exc=list_exc)


@pytest.fixture()
def clean_buffers():
    stats_sampler._buffers.clear()
    yield
    stats_sampler._buffers.clear()


@pytest.fixture()
async def fast_sampler(monkeypatch: pytest.MonkeyPatch, clean_buffers):
    """后台循环加速到 0.05s 一轮,用例结束停任务。"""
    monkeypatch.setattr(stats_sampler, "_INTERVAL", 0.05)
    yield stats_sampler
    await stats_sampler.stop()
    assert stats_sampler._task is None


def install(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> FakeDocker:
    fake = FakeDocker(**kwargs)
    monkeypatch.setattr(docker_client, "get_client", lambda: fake)
    return fake


# ---------------------------------------------------------------- cpu 公式


def test_make_point_cpu_formula() -> None:
    # delta 500 / denom 1000 = 0.5,online 4 → 0.5*4*100 = 200.0(二进制精确)
    p = stats_sampler._make_point(make_stats())
    assert p["cpu_percent"] == 200.0
    assert p["mem_mb"] == 2.0            # 2097152 / 1048576
    assert p["mem_limit_mb"] == 50.0     # 52428800 / 1048576
    assert set(p) == {"ts", "cpu_percent", "mem_mb", "mem_limit_mb"}
    assert p["ts"].endswith("Z")


def test_make_point_no_precpu_or_zero_denominator() -> None:
    # precpu 整段缺失 → 0.0
    p = stats_sampler._make_point(make_stats(with_precpu=False))
    assert p["cpu_percent"] == 0.0
    # system_cpu_usage 相等(分母 0)→ 0.0
    p = stats_sampler._make_point(make_stats(sys_now=2000, sys_pre=2000))
    assert p["cpu_percent"] == 0.0
    # 内存字段仍在
    assert p["mem_mb"] == 2.0 and p["mem_limit_mb"] == 50.0


def test_make_point_online_cpus_fallback() -> None:
    # online_cpus 缺失 → 回退 percpu_usage 数量: delta 1000/1000=1.0 * 3 * 100
    p = stats_sampler._make_point(
        make_stats(total=2000, pre_total=1000, online=None, percpu=[1, 2, 3])
    )
    assert p["cpu_percent"] == 300.0


# ---------------------------------------------------------------- 环形缓冲


def test_buffer_ring_maxlen(clean_buffers) -> None:
    for i in range(400):
        stats_sampler._append("c1", {"ts": f"t{i:04d}", "cpu_percent": 0.0, "mem_mb": 0.0, "mem_limit_mb": 0.0})
    buf = stats_sampler._buffers["c1"]
    assert len(buf) == stats_sampler._MAX_POINTS == 360
    assert stats_sampler.get_recent("c1", 1) == [{"ts": "t0399", "cpu_percent": 0.0, "mem_mb": 0.0, "mem_limit_mb": 0.0}]
    # 最早保留的是第 40 个点(0-based: 400-360=40 被挤出,首点是 t0040)
    assert list(buf)[0]["ts"] == "t0040"


def test_get_recent_snapshot(clean_buffers) -> None:
    for i in range(35):
        stats_sampler._append("c1", {"ts": f"t{i:02d}", "cpu_percent": float(i), "mem_mb": 0.0, "mem_limit_mb": 0.0})
    recent = stats_sampler.get_recent("c1", 30)
    assert len(recent) == 30
    assert recent[0]["ts"] == "t05" and recent[-1]["ts"] == "t34"
    assert stats_sampler.get_recent("c1", 5) == [
        {"ts": "t30", "cpu_percent": 30.0, "mem_mb": 0.0, "mem_limit_mb": 0.0},
        {"ts": "t31", "cpu_percent": 31.0, "mem_mb": 0.0, "mem_limit_mb": 0.0},
        {"ts": "t32", "cpu_percent": 32.0, "mem_mb": 0.0, "mem_limit_mb": 0.0},
        {"ts": "t33", "cpu_percent": 33.0, "mem_mb": 0.0, "mem_limit_mb": 0.0},
        {"ts": "t34", "cpu_percent": 34.0, "mem_mb": 0.0, "mem_limit_mb": 0.0},
    ]
    # 无缓冲 / 非法 n: 不采集、不抛 404
    assert stats_sampler.get_recent("ghost") == []
    assert stats_sampler.get_recent("c1", 0) == []
    assert stats_sampler.get_recent("c1", -3) == []


# ---------------------------------------------------------------- 后台循环


async def test_loop_samples_running_containers(monkeypatch: pytest.MonkeyPatch, fast_sampler) -> None:
    c1 = FakeStatsContainer("c1", make_stats())
    c2 = FakeStatsContainer("c2", make_stats(total=3000, pre_total=1000, sys_now=2000, sys_pre=1000, online=2))
    install(monkeypatch, cmap={"c1": c1, "c2": c2}, running=[c1, c2])

    await stats_sampler.start()
    await asyncio.sleep(0.4)  # 0.05s 一轮 → 至少 3 轮
    p1 = stats_sampler.get_recent("c1", 100)
    p2 = stats_sampler.get_recent("c2", 100)
    assert len(p1) >= 3 and len(p2) >= 3
    assert p1[0]["cpu_percent"] == 200.0
    assert p2[0]["cpu_percent"] == 400.0  # delta 2000/1000 * 2 cpus * 100
    # running 过滤参数
    fake = docker_client.get_client()
    assert fake.containers.list_calls[0] == {"filters": {"status": "running"}}


async def test_start_is_idempotent(monkeypatch: pytest.MonkeyPatch, fast_sampler) -> None:
    install(monkeypatch, running=[])
    await stats_sampler.start()
    t1 = stats_sampler._task
    await stats_sampler.start()
    assert stats_sampler._task is t1
    await stats_sampler.stop()
    await stats_sampler.stop()  # 幂等
    assert stats_sampler._task is None


async def test_loop_skips_failing_container(
    monkeypatch: pytest.MonkeyPatch, fast_sampler
) -> None:
    good = FakeStatsContainer("good", make_stats())
    bad = FakeStatsContainer("bad", stats_exc=RuntimeError("stats boom"))
    install(monkeypatch, cmap={"good": good, "bad": bad}, running=[good, bad])

    await stats_sampler.start()
    await asyncio.sleep(0.3)
    # 单容器失败只跳过,不拖垮整轮
    assert len(stats_sampler.get_recent("good", 100)) >= 2
    assert stats_sampler.get_recent("bad") == []
    assert bad.calls >= 2


async def test_loop_survives_list_failure(monkeypatch: pytest.MonkeyPatch, fast_sampler) -> None:
    install(monkeypatch, list_exc=RuntimeError("docker.sock gone"))
    await stats_sampler.start()
    await asyncio.sleep(0.15)  # 至少一轮失败
    await stats_sampler.stop()
    # 任务未被异常终结(stop 时能正常 cancel 即说明还活着)
    assert stats_sampler._task is None


# ---------------------------------------------------------------- get_stats


async def test_get_stats_live_sample_when_no_buffer(
    monkeypatch: pytest.MonkeyPatch, clean_buffers
) -> None:
    c = FakeStatsContainer("c1", make_stats())
    install(monkeypatch, cmap={"c1": c}, running=[c])

    pts = await stats_sampler.get_stats("c1")
    assert c.calls == 1  # 无缓冲 → 现采一帧
    assert len(pts) == 1
    assert pts[0]["cpu_percent"] == 200.0
    assert pts[0]["mem_mb"] == 2.0

    # 已入缓冲 → 再查不再采集
    pts2 = await stats_sampler.get_stats("c1")
    assert c.calls == 1
    assert pts2 == pts


async def test_get_stats_stopped_no_buffer(
    monkeypatch: pytest.MonkeyPatch, clean_buffers
) -> None:
    c = FakeStatsContainer("c1", make_stats(), running=False)
    install(monkeypatch, cmap={"c1": c}, running=[])
    assert await stats_sampler.get_stats("c1") == []
    assert c.calls == 0


async def test_get_stats_stopped_with_buffer_returns_history(
    monkeypatch: pytest.MonkeyPatch, clean_buffers
) -> None:
    c = FakeStatsContainer("c1", make_stats(), running=False)
    install(monkeypatch, cmap={"c1": c}, running=[])
    stats_sampler._append("c1", {"ts": "2026-08-25T03:00:00Z", "cpu_percent": 1.0, "mem_mb": 1.0, "mem_limit_mb": 2.0})
    pts = await stats_sampler.get_stats("c1")
    assert len(pts) == 1 and pts[0]["cpu_percent"] == 1.0


async def test_get_stats_not_found(monkeypatch: pytest.MonkeyPatch, clean_buffers) -> None:
    install(monkeypatch)
    with pytest.raises(ApiError) as ei:
        await stats_sampler.get_stats("nope")
    assert ei.value.status == 404
    assert ei.value.code == "container_not_found"


async def test_get_stats_since_filter(
    monkeypatch: pytest.MonkeyPatch, clean_buffers
) -> None:
    c = FakeStatsContainer("c1", make_stats())
    install(monkeypatch, cmap={"c1": c}, running=[c])
    for ts in ("2026-08-25T03:00:00Z", "2026-08-25T03:05:00Z", "2026-08-25T03:10:00Z"):
        stats_sampler._append("c1", {"ts": ts, "cpu_percent": 1.0, "mem_mb": 1.0, "mem_limit_mb": 2.0})

    all_pts = await stats_sampler.get_stats("c1")
    assert len(all_pts) == 3
    # ts >= since(含等号)
    pts = await stats_sampler.get_stats("c1", since="2026-08-25T03:05:00Z")
    assert [p["ts"] for p in pts] == ["2026-08-25T03:05:00Z", "2026-08-25T03:10:00Z"]
    # since 晚于全部点
    assert await stats_sampler.get_stats("c1", since="2026-08-25T04:00:00Z") == []


async def test_get_stats_resolves_name_to_buffer_key(
    monkeypatch: pytest.MonkeyPatch, clean_buffers
) -> None:
    # 按名称查询: containers.get 解析出完整 id 命中按 id 存的缓冲
    c = FakeStatsContainer("abc123full", make_stats(), running=False)
    install(monkeypatch, cmap={"web": c}, running=[])
    stats_sampler._append("abc123full", {"ts": "2026-08-25T03:00:00Z", "cpu_percent": 5.0, "mem_mb": 1.0, "mem_limit_mb": 2.0})
    pts = await stats_sampler.get_stats("web")
    assert len(pts) == 1 and pts[0]["cpu_percent"] == 5.0
