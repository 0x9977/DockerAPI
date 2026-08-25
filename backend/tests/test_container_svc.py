"""container_svc 单测: FakeDockerClient 模拟 daemon。

覆盖: 前缀/名称解析与 404、列表排序与字段映射与 stats 注入、幂等 note、
错误码翻译(404/409/504/502)、Env 脱敏、remove 409 透传、并发串行与锁超时。
"""
from __future__ import annotations

import asyncio

import docker
import pytest
import requests

from app.errors import ApiError
from app.services import container_svc, docker_client, stats_sampler

CID_A = "a" * 64
CID_B = "b" * 64
CID_C = "c" * 64
CID_D = "d" * 64


class _FakeResponse:
    """带 url/reason/text,兼容 docker-py APIError.__str__ 的格式化访问。"""

    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.url = "http://dockerapi-test/fake"
        self.reason = "FakeError"
        self.text = text


def api_error(status: int, message: str) -> docker.errors.APIError:
    # 仿 docker.errors.create_api_error_from_http_exception: daemon 信息走 explanation,
    # __str__ 会输出 "{status} ... Error for {url}: {reason} ("{explanation}")"
    return docker.errors.APIError(f"{status} Error", response=_FakeResponse(status), explanation=message)


def not_found(message: str) -> docker.errors.NotFound:
    return docker.errors.NotFound(message, response=_FakeResponse(404))


class FakeCtr:
    """daemon 侧容器记录(list/inspect 数据源)。"""

    def __init__(
        self,
        id: str,
        name: str,
        image: str = "busybox:latest",
        state: str = "running",
        labels: dict | None = None,
        created: int = 1756080000,
        env: list[str] | None = None,
    ) -> None:
        self.id = id
        self.name = name
        self.image = image
        self.state = state
        self.labels = labels or {}
        self.created = created
        self.env = env or []


class FakeContainer:
    """docker-py Container 模拟: 操作方法更新状态并模拟 daemon 幂等(304)。"""

    def __init__(self, rec: FakeCtr, daemon: "FakeDockerClient") -> None:
        self._rec = rec
        self._daemon = daemon
        self.id = rec.id
        self.attrs = daemon.build_inspect(rec)

    def _transition(self, action: str, target: str) -> None:
        self._daemon.calls.append((action, self._rec.id))
        exc = self._daemon.errors.get(action)
        if exc is not None:
            raise exc
        already = {"start": "running", "stop": "exited", "pause": "paused", "unpause": "running"}.get(action)
        if already is not None and self._rec.state == already:
            if self._daemon.raise_304:
                raise api_error(304, "304 Client Error: already in state")
            return  # 模拟不回 304 的 daemon: 静默无操作
        self._rec.state = target
        self.attrs["State"]["Status"] = target

    def start(self) -> None:
        self._transition("start", "running")

    def stop(self, timeout: int = 10) -> None:
        self._daemon.timeouts.append(timeout)
        self._transition("stop", "exited")

    def restart(self, timeout: int = 10) -> None:
        self._daemon.timeouts.append(timeout)
        self._transition("restart", "running")

    def pause(self) -> None:
        self._transition("pause", "paused")

    def unpause(self) -> None:
        self._transition("unpause", "running")

    def remove(self, force: bool = False) -> None:
        self._daemon.calls.append(("remove", self._rec.id))
        exc = self._daemon.errors.get("remove")
        if exc is not None:
            raise exc
        if self._rec.state == "running" and not force:
            raise api_error(409, "cannot remove running container, use force")
        self._daemon._remove(self._rec.id)


class FakeContainerCollection:
    def __init__(self, daemon: "FakeDockerClient") -> None:
        self._daemon = daemon

    def get(self, cid: str) -> FakeContainer:
        self._daemon.calls.append(("get", cid))
        exc = self._daemon.errors.get("get")
        if exc is not None:
            raise exc
        rec = self._daemon._lookup(cid)
        if rec is None:
            raise not_found(f"No such container: {cid}")
        return FakeContainer(rec, self._daemon)


class FakeAPIClient:
    def __init__(self, daemon: "FakeDockerClient") -> None:
        self._daemon = daemon

    def containers(self, all: bool = False, **kwargs: object) -> list[dict]:
        self._daemon.calls.append(("list", {"all": all}))
        exc = self._daemon.errors.get("list")
        if exc is not None:
            raise exc
        return [
            {
                "Id": rec.id,
                "Names": [f"/{rec.name}"],  # daemon list 的 Name 带 / 前缀
                "Image": rec.image,
                "State": rec.state,
                "Labels": dict(rec.labels),
                "Created": rec.created,  # 秒级时间戳
            }
            for rec in self._daemon.records.values()
            if all or rec.state == "running"
        ]


class FakeDockerClient:
    """可控假 daemon: records 容器集合;errors 按调用点(list/get/start/...)注入异常。"""

    def __init__(self) -> None:
        self.records: dict[str, FakeCtr] = {}
        self.calls: list[tuple] = []
        self.errors: dict[str, Exception] = {}
        self.timeouts: list[int] = []
        self.raise_304 = True
        self.api = FakeAPIClient(self)
        self.containers = FakeContainerCollection(self)

    def add(self, id: str, name: str, **kwargs: object) -> FakeCtr:
        rec = FakeCtr(id=id, name=name, **kwargs)  # type: ignore[arg-type]
        self.records[id] = rec
        return rec

    def _remove(self, cid: str) -> None:
        self.records.pop(cid, None)

    def _lookup(self, cid: str) -> FakeCtr | None:
        if cid in self.records:
            return self.records[cid]
        for rec in self.records.values():
            if rec.name == cid:
                return rec
        return None

    def build_inspect(self, rec: FakeCtr) -> dict:
        return {
            "Id": rec.id,
            "Name": f"/{rec.name}",
            "Created": "2025-08-25T00:00:00Z",
            "State": {"Status": rec.state, "Running": rec.state == "running", "Pid": 1},
            "Config": {
                "Image": rec.image,
                "Env": list(rec.env),
                "Labels": dict(rec.labels),
                "Cmd": ["/bin/sh"],  # 应被裁剪掉
            },
            "HostConfig": {
                "PortBindings": {"80/tcp": [{"HostPort": "8080"}]},
                "Binds": ["/data:/data"],
                "RestartPolicy": {"Name": "always"},
                "NetworkMode": "default",  # 应被裁剪掉
            },
            "NetworkSettings": {"Ports": {"80/tcp": [{"HostPort": "8080"}]}},
        }


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def fake(monkeypatch):
    fake = FakeDockerClient()
    monkeypatch.setattr(docker_client, "get_client", lambda: fake)
    monkeypatch.setattr(container_svc, "_locks", {})  # 隔离 per-container 锁
    return fake


# ---------- resolve_container ----------


def test_resolve_full_id_direct(fake):
    assert run(container_svc.resolve_container(CID_A)) == CID_A
    assert fake.calls == []  # 完整 ID 直接用,不查 daemon


def test_resolve_by_name_and_prefix(fake):
    fake.add(CID_A, "web")
    fake.add(CID_B, "db")
    assert run(container_svc.resolve_container("web")) == CID_A
    assert run(container_svc.resolve_container(CID_B[:12])) == CID_B


def test_resolve_unknown_404(fake):
    fake.add(CID_A, "web")
    with pytest.raises(ApiError) as ei:
        run(container_svc.resolve_container("nope"))
    assert (ei.value.status, ei.value.code) == (404, "container_not_found")


def test_resolve_ambiguous_prefix_404(fake):
    fake.add("ab" + "1" * 62, "x")
    fake.add("ab" + "2" * 62, "y")
    with pytest.raises(ApiError) as ei:
        run(container_svc.resolve_container("ab"))
    assert (ei.value.status, ei.value.code) == (404, "container_not_found")


# ---------- list_containers ----------


def test_list_containers_order_fields_stats(fake, monkeypatch):
    fake.add(
        CID_A, "demo-demo-1", image="busybox:latest", state="running",
        labels={"com.docker.compose.project": "demo"}, created=1756080000,
    )
    fake.add(CID_B, "made-1", state="created", created=1756080001)
    fake.add(CID_C, "gone-1", state="exited", created=1756080002)
    fake.add(CID_D, "frozen-1", state="paused", created=1756080003)

    def fake_recent(cid: str, n: int = 30) -> list[dict]:
        assert n == 30
        if cid != CID_A:
            return []
        return [{"ts": "2025-08-25T00:00:00Z", "cpu_percent": 1.2, "mem_mb": 3.4, "mem_limit_mb": 976.0}]

    monkeypatch.setattr(stats_sampler, "get_recent", fake_recent)
    items = run(container_svc.list_containers())

    assert ("list", {"all": True}) in fake.calls  # all=True
    assert [i["state"] for i in items] == ["running", "created", "exited", "paused"]  # 其余垫底
    first = items[0]
    assert first["id"] == CID_A
    assert first["name"] == "demo-demo-1"  # 去掉 daemon 的 / 前缀
    assert first["image"] == "busybox:latest"
    assert first["compose_project"] == "demo"
    assert first["created"] == "2025-08-25T00:00:00Z"  # 秒级时间戳 → ISO UTC
    assert first["stats"][0]["cpu_percent"] == 1.2
    assert items[1]["stats"] == []
    assert items[1]["compose_project"] is None


def test_list_stats_sampler_exception_swallowed(fake, monkeypatch):
    fake.add(CID_A, "web")

    def boom(cid: str, n: int = 30) -> list[dict]:
        raise RuntimeError("no buffer")

    monkeypatch.setattr(stats_sampler, "get_recent", boom)
    items = run(container_svc.list_containers())
    assert items[0]["stats"] == []


def test_list_daemon_unreachable_504(fake):
    fake.errors["list"] = requests.exceptions.ConnectionError("daemon down")
    with pytest.raises(ApiError) as ei:
        run(container_svc.list_containers())
    assert (ei.value.status, ei.value.code) == (504, "daemon_timeout")


# ---------- 变更操作幂等 / note ----------


def test_start_already_running_note(fake):
    fake.add(CID_A, "web", state="running")
    r = run(container_svc.start_container("web"))
    assert r == {"status": "ok", "note": "already_in_state"}
    assert ("start", CID_A) in fake.calls  # 仍直接执行,非预检跳过


def test_start_prestate_note_without_304(fake):
    # daemon 不回 304 的场景: pre-state 检查同样生成 note
    fake.add(CID_A, "web", state="running")
    fake.raise_304 = False
    r = run(container_svc.start_container("web"))
    assert r == {"status": "ok", "note": "already_in_state"}


def test_start_from_created_no_note(fake):
    fake.add(CID_A, "web", state="created")
    r = run(container_svc.start_container("web"))
    assert r == {"status": "ok"}
    assert "note" not in r
    assert fake.records[CID_A].state == "running"


def test_restart_running_no_note(fake):
    fake.add(CID_A, "web", state="running")
    r = run(container_svc.restart_container("web"))
    assert r == {"status": "ok"}
    assert ("restart", CID_A) in fake.calls


def test_stop_passes_t(fake):
    fake.add(CID_A, "web", state="running")
    run(container_svc.stop_container("web", t=7))
    assert fake.timeouts == [7]
    assert fake.records[CID_A].state == "exited"


def test_restart_passes_t(fake):
    fake.add(CID_A, "web", state="exited")
    run(container_svc.restart_container("web", t=3))
    assert fake.timeouts == [3]
    assert fake.records[CID_A].state == "running"


def test_pause_unpause(fake):
    fake.add(CID_A, "web", state="running")
    assert run(container_svc.pause_container("web")) == {"status": "ok"}
    assert fake.records[CID_A].state == "paused"
    assert run(container_svc.unpause_container("web")) == {"status": "ok"}
    assert fake.records[CID_A].state == "running"
    r = run(container_svc.unpause_container("web"))  # 已运行再 unpause → 幂等 note
    assert r == {"status": "ok", "note": "already_in_state"}


# ---------- 错误码翻译 ----------


def test_op_unknown_container_404(fake):
    with pytest.raises(ApiError) as ei:
        run(container_svc.start_container("ghost"))
    assert (ei.value.status, ei.value.code) == (404, "container_not_found")


def test_op_get_not_found_404(fake):
    fake.add(CID_A, "web")
    fake.errors["get"] = not_found("No such container: x")
    with pytest.raises(ApiError) as ei:
        run(container_svc.stop_container("web"))
    assert (ei.value.status, ei.value.code) == (404, "container_not_found")


def test_op_409_conflict(fake):
    fake.add(CID_A, "web", state="created")
    fake.errors["start"] = api_error(409, "driver is unhealthy")
    with pytest.raises(ApiError) as ei:
        run(container_svc.start_container("web"))
    assert (ei.value.status, ei.value.code) == (409, "conflict")
    assert "driver is unhealthy" in ei.value.message


@pytest.mark.parametrize(
    "exc",
    [
        requests.exceptions.ReadTimeout("timed out"),
        requests.exceptions.ConnectTimeout("connect timed out"),
        requests.exceptions.ConnectionError("daemon down"),
        docker.errors.DockerException("docker daemon unreachable"),
    ],
)
def test_op_timeout_504(fake, exc):
    fake.add(CID_A, "web", state="created")
    fake.errors["start"] = exc
    with pytest.raises(ApiError) as ei:
        run(container_svc.start_container("web"))
    assert (ei.value.status, ei.value.code) == (504, "daemon_timeout")


def test_op_daemon_error_502(fake):
    fake.add(CID_A, "web", state="created")
    fake.errors["start"] = api_error(500, "internal daemon boom")
    with pytest.raises(ApiError) as ei:
        run(container_svc.start_container("web"))
    assert (ei.value.status, ei.value.code) == (502, "daemon_error")


# ---------- inspect ----------


def test_inspect_masks_env(fake):
    fake.add(
        CID_A, "web",
        env=[
            "PATH=/usr/bin:/bin",
            "DB_PASSWORD=hunter2",
            "API_TOKEN=tok123",
            "SSH_PRIVATE_KEY=-----BEGIN",
            "MY_CREDENTIALS=abc",
            "SESSION_SECRET=s3cr3t",
            "TOKENIZER_MODEL=x",  # 变量名含 TOKEN 同样命中
        ],
    )
    r = run(container_svc.inspect_container("web"))
    env = r["Config"]["Env"]
    assert "PATH=/usr/bin:/bin" in env
    for masked in ("DB_PASSWORD", "API_TOKEN", "SSH_PRIVATE_KEY", "MY_CREDENTIALS", "SESSION_SECRET", "TOKENIZER_MODEL"):
        assert f"{masked}=***" in env
    assert not any("hunter2" in e or "tok123" in e for e in env)  # 原值绝不出现


def test_inspect_trimmed_fields(fake):
    fake.add(CID_A, "web")
    r = run(container_svc.inspect_container(CID_A[:8]))  # 短 ID 前缀解析
    assert r["Id"] == CID_A
    assert r["Name"] == "/web"
    assert set(r) == {"Id", "Name", "Created", "State", "Config", "HostConfig", "NetworkSettings", "is_self"}
    assert set(r["Config"]) == {"Image", "Env", "Labels"}  # Cmd 等被裁剪
    assert set(r["HostConfig"]) == {"PortBindings", "Binds", "RestartPolicy"}  # NetworkMode 等被裁剪
    assert set(r["NetworkSettings"]) == {"Ports"}
    assert r["HostConfig"]["PortBindings"] == {"80/tcp": [{"HostPort": "8080"}]}


def test_inspect_not_found(fake):
    with pytest.raises(ApiError) as ei:
        run(container_svc.inspect_container("ghost"))
    assert ei.value.code == "container_not_found"


# ---------- remove ----------


def test_remove_running_without_force_409_passthrough(fake):
    fake.add(CID_A, "web", state="running")
    with pytest.raises(ApiError) as ei:
        run(container_svc.remove_container("web"))
    assert (ei.value.status, ei.value.code) == (409, "conflict")
    assert "cannot remove running container" in ei.value.message
    assert CID_A in fake.records  # 未删除


def test_remove_force(fake):
    fake.add(CID_A, "web", state="running")
    r = run(container_svc.remove_container("web", force=True))
    assert r == {"status": "ok"}
    assert CID_A not in fake.records
    assert CID_A not in container_svc._locks  # 锁条目清理


def test_remove_stopped_without_force(fake):
    fake.add(CID_A, "web", state="exited")
    r = run(container_svc.remove_container("web"))
    assert r == {"status": "ok"}
    assert CID_A not in fake.records


def test_remove_not_found(fake):
    with pytest.raises(ApiError) as ei:
        run(container_svc.remove_container("ghost"))
    assert ei.value.code == "container_not_found"


# ---------- 串行化与锁 ----------


def test_concurrent_starts_serialized(fake):
    fake.add(CID_A, "web", state="created")

    async def go():
        return await asyncio.gather(
            container_svc.start_container("web"),
            container_svc.start_container("web"),
        )

    r1, r2 = run(go())
    seq = [c for c in fake.calls if c[0] in ("get", "start")]
    # 串行: 各自的 get+start 成对执行,不会出现 get,get,start,start 交错
    assert seq == [("get", CID_A), ("start", CID_A), ("get", CID_A), ("start", CID_A)]
    # 一个首次启动成功,另一个幂等 note(完成顺序不定,按集合断言)
    assert sorted([r1.get("note", ""), r2.get("note", "")]) == ["", "already_in_state"]


def test_lock_wait_timeout_409(fake, monkeypatch):
    fake.add(CID_A, "web", state="created")
    monkeypatch.setattr(container_svc, "LOCK_WAIT_SECONDS", 0.05)

    async def go():
        lock = container_svc._lock_for(CID_A)  # 模拟另一请求持锁
        await lock.acquire()
        try:
            return await container_svc.start_container("web")
        finally:
            lock.release()

    with pytest.raises(ApiError) as ei:
        run(go())
    assert (ei.value.status, ei.value.code) == (409, "conflict")
    assert ei.value.message == "操作正在被其他请求执行"
    assert ("start", CID_A) not in fake.calls  # 未拿到锁,操作未下发


async def test_self_protection_blocks_mutations(fake, monkeypatch):
    """面板自身容器: 列表标记 is_self,变更操作一律 403 self_protection。"""
    from app.services import container_svc

    self_full = CID_A
    other = CID_B
    fake.add(self_full, "selfpanel", state="running")
    fake.add(other, "otherapp", state="running")

    monkeypatch.setattr(container_svc, "_self_container_id", self_full)
    monkeypatch.setattr(container_svc, "_self_detected", True)

    items = await container_svc.list_containers()
    by_id = {i["id"]: i for i in items}
    assert by_id[self_full]["is_self"] is True
    assert by_id[other]["is_self"] is False

    detail = await container_svc.inspect_container(self_full)
    assert detail["is_self"] is True

    for fn, args in (
        (container_svc.start_container, ()),
        (container_svc.stop_container, ()),
        (container_svc.restart_container, ()),
        (container_svc.pause_container, ()),
        (container_svc.unpause_container, ()),
        (container_svc.remove_container, ()),
    ):
        with pytest.raises(ApiError) as ei:
            await fn("selfpanel", *args)
        assert ei.value.status == 403
        assert ei.value.code == "self_protection"

    # 其他容器不受影响
    r = await container_svc.stop_container("otherapp")
    assert r["status"] == "ok"

    monkeypatch.setattr(container_svc, "_self_container_id", None)
    monkeypatch.setattr(container_svc, "_self_detected", True)
