"""compose_svc 单测: 栈发现/优先级、状态判定、路径穿越防护、job 提交参数、日志聚合。

docker-py 用 fake 客户端(monkeypatch docker_client.get_client);
CLI 子进程用 monkeypatch asyncio.create_subprocess_exec 替换。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.config import settings
from app.errors import ApiError
from app.services import compose_svc, docker_client, job_mgr


# ---------- 夹具与假对象 ----------


@pytest.fixture()
def stacks_root(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(settings, "stacks_dir", tmp_path)
    return tmp_path


class FakeContainer:
    def __init__(
        self,
        cid: str,
        cname: str,
        project: str | None,
        state: str,
        image: str = "nginx:1",
        created: float = 1750000000.0,
        logs_payload: bytes = b"",
    ) -> None:
        labels = {"com.docker.compose.project": project} if project else {}
        self.attrs = {
            "Id": cid,
            "Names": ["/" + cname],
            "Image": image,
            "State": state,
            "Created": created,
            "Labels": labels,
        }
        self._logs_payload = logs_payload

    def logs(self, tail: int = 200, timestamps: bool = False, stream: bool = False, **kw) -> bytes:
        assert stream is False
        return self._logs_payload


class FakeDocker:
    """self.containers.list(all=True) 兼容形态。"""

    def __init__(self, items: list | None = None, exc: Exception | None = None) -> None:
        self._items = items or []
        self._exc = exc
        self.calls: list[dict] = []
        self.containers = self

    def list(self, all: bool = False, filters: dict | None = None) -> list:
        self.calls.append({"all": all, "filters": filters})
        if self._exc is not None:
            raise self._exc
        return list(self._items)


@pytest.fixture()
def patch_docker(monkeypatch):
    def _patch(items: list | None = None, exc: Exception | None = None) -> FakeDocker:
        fake = FakeDocker(items=items, exc=exc)
        monkeypatch.setattr(docker_client, "get_client", lambda: fake)
        return fake

    return _patch


def _mk_stack(root: Path, name: str, files: dict[str, str]) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    for fname, content in files.items():
        (d / fname).write_text(content, encoding="utf-8")
    return d


# ---------- 栈发现与状态 ----------


async def test_list_stacks_discovery_priority_and_status(stacks_root, patch_docker):
    _mk_stack(
        stacks_root,
        "alpha",
        {"compose.yaml": "a: 1\n", "compose.yml": "WRONG", "docker-compose.yaml": "WRONG"},
    )
    _mk_stack(stacks_root, "beta", {"docker-compose.yml": "b: 1\n"})
    _mk_stack(stacks_root, "gamma", {"compose.yml": "g: 1\n"})
    _mk_stack(stacks_root, "delta", {"compose.yaml": "d: 1\n"})
    _mk_stack(stacks_root, "no-compose", {"README.md": "hi"})
    (stacks_root / "plain.txt").write_text("x")
    patch_docker(
        [
            FakeContainer("c1", "alpha-web-1", "alpha", "running"),
            FakeContainer("c2", "alpha-db-1", "alpha", "running"),
            FakeContainer("c3", "beta-1", "beta", "running"),
            FakeContainer("c4", "beta-2", "beta", "exited"),
            FakeContainer("c5", "gamma-1", "gamma", "exited"),
            FakeContainer("c6", "ghost-1", "ghost", "running"),  # 无目录,不出现
        ]
    )
    result = await compose_svc.list_stacks()
    assert [r["name"] for r in result] == ["alpha", "beta", "delta", "gamma"]
    by_name = {r["name"]: r for r in result}
    assert by_name["alpha"]["status"] == "running"
    assert by_name["alpha"]["container_count"] == 2
    assert by_name["alpha"]["running_count"] == 2
    assert by_name["beta"]["status"] == "partial"
    assert by_name["beta"]["container_count"] == 2
    assert by_name["beta"]["running_count"] == 1
    assert by_name["gamma"]["status"] == "stopped"
    assert by_name["gamma"]["running_count"] == 0
    assert by_name["delta"]["status"] == "not_created"
    assert by_name["delta"]["container_count"] == 0


async def test_list_stacks_daemon_unreachable_degrades(stacks_root, patch_docker):
    _mk_stack(stacks_root, "alpha", {"compose.yaml": "a: 1\n"})
    patch_docker(exc=RuntimeError("daemon down"))
    result = await compose_svc.list_stacks()
    assert [r["name"] for r in result] == ["alpha"]
    assert result[0]["status"] == "unknown"
    assert result[0]["container_count"] == 0


async def test_list_stacks_missing_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "stacks_dir", tmp_path / "nonexistent")
    assert await compose_svc.list_stacks() == []


# ---------- 栈详情 ----------


async def test_get_stack_returns_yaml_and_containers(stacks_root, patch_docker):
    _mk_stack(stacks_root, "alpha", {"compose.yml": "WRONG", "compose.yaml": "services:\n  web:\n    image: nginx\n"})
    patch_docker([FakeContainer("c1", "alpha-web-1", "alpha", "running", image="nginx:latest")])
    d = await compose_svc.get_stack("alpha")
    assert d["name"] == "alpha"
    assert d["status"] == "running"
    assert "image: nginx" in d["compose_yaml"]
    assert "WRONG" not in d["compose_yaml"]
    assert len(d["containers"]) == 1
    item = d["containers"][0]
    assert item["id"] == "c1"
    assert item["name"] == "alpha-web-1"
    assert item["image"] == "nginx:latest"
    assert item["state"] == "running"
    assert item["compose_project"] == "alpha"
    assert item["created"].endswith("Z")
    assert item["stats"] == []  # stats_sampler 未实现/无缓冲 → 空


async def test_get_stack_daemon_unreachable(stacks_root, patch_docker):
    _mk_stack(stacks_root, "alpha", {"compose.yaml": "a: 1\n"})
    patch_docker(exc=RuntimeError("daemon down"))
    d = await compose_svc.get_stack("alpha")
    assert d["status"] == "unknown"
    assert d["containers"] == []


async def test_stack_name_validation_rejects_traversal_and_others(stacks_root, patch_docker):
    _mk_stack(stacks_root, "ok", {"compose.yaml": "x"})
    (stacks_root / "bare").mkdir()
    (stacks_root / "secret").mkdir()
    (stacks_root / "secret" / "compose.yaml").write_text("s")
    bad_names = [
        "../secret",  # 路径穿越
        "..%2Fsecret",
        "UPPER",  # 大写
        "x" * 65,  # 超长
        "-lead",  # 首字符非字母数字
        "a/b",
        "a.b",
        ".",
        "nope",  # 不存在
        "bare",  # 存在但无 compose 文件
    ]
    for bad in bad_names:
        with pytest.raises(ApiError) as err:
            await compose_svc.get_stack(bad)
        assert err.value.status == 404, bad
        assert err.value.code == "stack_not_found", bad


# ---------- 变更操作 → job 提交 ----------


@pytest.fixture()
def capture_submit(monkeypatch):
    captured: list[dict] = []

    async def fake_submit(job_type: str, stack: str, args: list[str], actor: dict | None = None) -> str:
        captured.append({"job_type": job_type, "stack": stack, "args": args, "actor": actor})
        return "j_test"

    monkeypatch.setattr(job_mgr, "submit_compose", fake_submit)
    return captured


async def test_stack_up_down_restart_submit_jobs(stacks_root, capture_submit):
    stack_dir = _mk_stack(stacks_root, "alpha", {"compose.yaml": "a: 1\n"})
    compose_file = stack_dir / "compose.yaml"
    actor = {"type": "user", "name": "admin", "ip": "1.2.3.4"}

    jid = await compose_svc.stack_up("alpha", actor=actor)
    assert jid == "j_test"
    assert capture_submit[-1] == {
        "job_type": "stack.up",
        "stack": "alpha",
        "args": ["-f", str(compose_file), "-p", "alpha", "up", "-d"],
        "actor": actor,
    }

    await compose_svc.stack_down("alpha", volumes=False, actor=actor)
    assert capture_submit[-1]["args"] == ["-f", str(compose_file), "-p", "alpha", "down"]

    await compose_svc.stack_down("alpha", volumes=True, actor=actor)
    assert capture_submit[-1]["args"] == ["-f", str(compose_file), "-p", "alpha", "down", "--volumes"]
    assert capture_submit[-1]["job_type"] == "stack.down"

    await compose_svc.stack_restart("alpha", actor=actor)
    assert capture_submit[-1]["job_type"] == "stack.restart"
    assert capture_submit[-1]["args"] == ["-f", str(compose_file), "-p", "alpha", "restart"]


async def test_stack_ops_reject_invalid_name_before_submit(stacks_root, capture_submit):
    for fn in (compose_svc.stack_up, compose_svc.stack_restart):
        with pytest.raises(ApiError) as err:
            await fn("../evil")
        assert err.value.code == "stack_not_found"
    with pytest.raises(ApiError):
        await compose_svc.stack_down("UPPER", volumes=True)
    assert capture_submit == []


# ---------- 日志聚合 ----------


class FakeCliProc:
    def __init__(self, out: bytes, returncode: int = 0) -> None:
        self._out = out
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, None]:
        return self._out, None


async def test_stack_logs_uses_compose_cli(stacks_root, monkeypatch, patch_docker):
    stack_dir = _mk_stack(stacks_root, "alpha", {"compose.yaml": "a: 1\n"})
    patch_docker()  # 不应走到 docker-py
    cmds: list[tuple] = []

    async def fake_exec(*cmd: str, **kw) -> FakeCliProc:
        cmds.append((cmd, kw))
        return FakeCliProc(b"2026-01-01T00:00:00Z l1\n2026-01-01T00:00:01Z l2\n")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    out = await compose_svc.stack_logs("alpha", tail=50)
    assert out == {
        "lines": [
            {"stream": "stdout", "line": "2026-01-01T00:00:00Z l1"},
            {"stream": "stdout", "line": "2026-01-01T00:00:01Z l2"},
        ]
    }
    cmd, kw = cmds[0]
    assert cmd == (
        "docker",
        "compose",
        "-f",
        str(stack_dir / "compose.yaml"),
        "-p",
        "alpha",
        "logs",
        "--no-color",
        "--tail",
        "50",
        "--timestamps",
    )
    assert kw.get("stdout") == asyncio.subprocess.PIPE
    assert kw.get("stderr") == asyncio.subprocess.STDOUT


async def test_stack_logs_cli_failure_falls_back_to_docker_py(stacks_root, monkeypatch, patch_docker):
    _mk_stack(stacks_root, "alpha", {"compose.yaml": "a: 1\n"})

    async def fake_exec(*cmd: str, **kw):
        raise FileNotFoundError("no docker cli")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    c1 = FakeContainer(
        "c1", "alpha-1", "alpha", "running",
        logs_payload=b"2026-01-01T00:00:02Z b1\n2026-01-01T00:00:04Z b2\n",
    )
    c2 = FakeContainer(
        "c2", "alpha-2", "alpha", "running",
        logs_payload=b"2026-01-01T00:00:01Z a1\n2026-01-01T00:00:03Z a2\n",
    )
    other = FakeContainer("c9", "other-1", "other", "running", logs_payload=b"2026-01-01T00:00:00Z NOPE\n")
    patch_docker([c1, c2, other])
    out = await compose_svc.stack_logs("alpha", tail=10)
    lines = [item["line"] for item in out["lines"]]
    assert lines == [
        "2026-01-01T00:00:01Z a1",
        "2026-01-01T00:00:02Z b1",
        "2026-01-01T00:00:03Z a2",
        "2026-01-01T00:00:04Z b2",
    ]
    assert all(item["stream"] == "stdout" for item in out["lines"])


async def test_stack_logs_cli_nonzero_exit_falls_back(stacks_root, monkeypatch, patch_docker):
    _mk_stack(stacks_root, "alpha", {"compose.yaml": "a: 1\n"})

    async def fake_exec(*cmd: str, **kw):
        return FakeCliProc(b"boom\n", returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    c1 = FakeContainer("c1", "alpha-1", "alpha", "running", logs_payload=b"2026-01-01T00:00:00Z x\n")
    patch_docker([c1])
    out = await compose_svc.stack_logs("alpha", tail=5)
    assert [item["line"] for item in out["lines"]] == ["2026-01-01T00:00:00Z x"]


async def test_stack_logs_invalid_stack_404(stacks_root, monkeypatch, patch_docker):
    with pytest.raises(ApiError) as err:
        await compose_svc.stack_logs("../evil")
    assert err.value.code == "stack_not_found"


async def test_stack_logs_daemon_unreachable_502(stacks_root, monkeypatch, patch_docker):
    _mk_stack(stacks_root, "alpha", {"compose.yaml": "a: 1\n"})

    async def fake_exec(*cmd: str, **kw):
        raise FileNotFoundError("no docker cli")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    patch_docker(exc=RuntimeError("daemon down"))
    with pytest.raises(ApiError) as err:
        await compose_svc.stack_logs("alpha")
    assert err.value.status == 502
    assert err.value.code == "daemon_error"
