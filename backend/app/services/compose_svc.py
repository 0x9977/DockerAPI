"""Compose 栈服务 — 实现负责人: compose/job subagent(见 docs/architecture.md 决策4/9、api.md)。

接口契约(路由层已按此调用,不得改签名):

栈名校验(安全底线): 必须 `^[a-z0-9][a-z0-9_-]{0,63}$` 且
Path(settings.stacks_dir / name).resolve() 仍在 settings.stacks_dir.resolve()
之下;不合法/目录不存在 → ApiError(404, "stack_not_found")。
栈发现: 扫描一级子目录,compose 文件优先级
compose.yaml > compose.yml > docker-compose.yaml > docker-compose.yml,
无 compose 文件的目录跳过。

list_stacks() -> list[dict]
    [{"name","status","container_count","running_count"}]
    daemon 不可达时仍返回目录清单(status="unknown")。

get_stack(name) -> dict
    {"name","status","compose_yaml"(文件全文),"containers":[同 container_svc 列表项]}

stack_up(name, actor) / stack_down(name, volumes=False, actor) / stack_restart(name, actor) -> str
    校验栈(404)→ 调 job_mgr.submit_compose(...) 返回 job_id。
    actor: dict{type,name,ip},随任务传递供终态审计。
    CLI: docker compose -f <file> -p <name> up -d | down [--volumes] | restart
    子进程环境白名单 PATH/DOCKER_HOST/HOME,cwd=栈目录(由 job_mgr 执行器落实)。

stack_logs(name, tail=200) -> dict
    {"lines":[{stream,line}...]},聚合该栈容器最近 tail 行,时间近似有序即可。
    优先 `docker compose logs` CLI 一次性拉取(30s 超时),失败回退 docker-py
    逐容器 logs 归并(按时间戳前缀排序)。
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import docker.errors
from loguru import logger

from app.config import settings
from app.errors import daemon_error, not_found
from app.services import docker_client, job_mgr, stats_sampler

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _subprocess_env() -> dict[str, str]:
    """子进程环境白名单(与 job_mgr 一致: 仅 PATH/DOCKER_HOST/HOME,审计 S4)。"""
    import os

    return {k: os.environ[k] for k in ("PATH", "DOCKER_HOST", "HOME") if k in os.environ}
_COMPOSE_FILES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
_PROJECT_LABEL = "com.docker.compose.project"
_LOGS_CLI_TIMEOUT = 30
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\S+)")


# ---------- 校验与发现(纯同步,无 IO 副作用除 stat 外) ----------


def _stack_paths(name: str) -> tuple[Path, Path]:
    """返回 (栈目录, compose 文件)。任何不合法一律 404 stack_not_found(白名单式)。"""
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise not_found("stack_not_found", f"invalid stack name: {name!r}")
    base = settings.stacks_dir.resolve()
    stack_dir = (settings.stacks_dir / name).resolve()
    try:
        stack_dir.relative_to(base)
    except ValueError:
        raise not_found("stack_not_found", f"invalid stack name: {name!r}") from None
    if not stack_dir.is_dir():
        raise not_found("stack_not_found", f"stack directory not found: {name}")
    for fname in _COMPOSE_FILES:
        candidate = stack_dir / fname
        if candidate.is_file():
            return stack_dir, candidate
    raise not_found("stack_not_found", f"no compose file in stack: {name}")


def _discover() -> list[tuple[str, Path, Path]]:
    """扫描 stacks_dir 一级子目录,返回 (name, 栈目录, compose 文件),按名字排序。"""
    root = settings.stacks_dir
    out: list[tuple[str, Path, Path]] = []
    if not root.is_dir():
        return out
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or not _NAME_RE.fullmatch(child.name):
            continue
        for fname in _COMPOSE_FILES:
            candidate = child / fname
            if candidate.is_file():
                out.append((child.name, child, candidate))
                break
    return out


# ---------- daemon 状态(docker-py 一律 to_thread) ----------


async def _fetch_containers() -> list[Any] | None:
    """全部容器;daemon 异常返回 None(调用方降级,不抛)。"""
    try:
        return await asyncio.to_thread(docker_client.get_client().containers.list, all=True)
    except Exception:
        return None


def _status_of(states: list[str]) -> tuple[str, int, int]:
    """(status, container_count, running_count)。"""
    running = sum(1 for s in states if s == "running")
    if not states:
        return "not_created", 0, 0
    if running == len(states):
        return "running", len(states), running
    if running == 0:
        return "stopped", len(states), 0
    return "partial", len(states), running


def _container_item(c: Any) -> dict:
    """映射成 container_svc 列表项同构字段(snake_case,见 docs/api.md)。"""
    attrs = c.attrs if hasattr(c, "attrs") else {}
    names = attrs.get("Names") or []
    cname = names[0].lstrip("/") if names else str(attrs.get("Id", ""))[:12]
    created = attrs.get("Created")
    try:
        created_iso = (
            datetime.fromtimestamp(float(created), tz=timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
    except (TypeError, ValueError, OSError):
        created_iso = ""
    try:
        stats = stats_sampler.get_recent(str(attrs.get("Id", "")), 30)
    except Exception:
        stats = []
    return {
        "id": str(attrs.get("Id", "")),
        "name": cname,
        "image": str(attrs.get("Image", "")),
        "state": str(attrs.get("State", "unknown")),
        "compose_project": (attrs.get("Labels") or {}).get(_PROJECT_LABEL, ""),
        "created": created_iso,
        "stats": stats,
    }


# ---------- 对外接口 ----------


async def list_stacks() -> list[dict]:
    found = _discover()
    containers = await _fetch_containers()
    states: dict[str, list[str]] = {}
    if containers is not None:
        for c in containers:
            attrs = c.attrs if hasattr(c, "attrs") else {}
            project = (attrs.get("Labels") or {}).get(_PROJECT_LABEL)
            if project:
                states.setdefault(project, []).append(str(attrs.get("State", "unknown")))
    out: list[dict] = []
    for name, _dir, _file in found:
        if containers is None:
            status, count, running = "unknown", 0, 0
        else:
            status, count, running = _status_of(states.get(name, []))
        out.append(
            {"name": name, "status": status, "container_count": count, "running_count": running}
        )
    return out


async def get_stack(name: str) -> dict:
    _dir, compose_file = _stack_paths(name)
    containers = await _fetch_containers()
    items: list[dict] = []
    stack_states: list[str] = []
    if containers is not None:
        for c in containers:
            attrs = c.attrs if hasattr(c, "attrs") else {}
            if (attrs.get("Labels") or {}).get(_PROJECT_LABEL) != name:
                continue
            stack_states.append(str(attrs.get("State", "unknown")))
            items.append(_container_item(c))
    status = "unknown" if containers is None else _status_of(stack_states)[0]
    return {
        "name": name,
        "status": status,
        "compose_yaml": compose_file.read_text(encoding="utf-8", errors="replace"),
        "containers": items,
    }


async def stack_up(name: str, actor: dict | None = None) -> str:
    _dir, compose_file = _stack_paths(name)
    return await job_mgr.submit_compose(
        "stack.up", name, ["-f", str(compose_file), "-p", name, "up", "-d"], actor
    )


async def stack_down(name: str, volumes: bool = False, actor: dict | None = None) -> str:
    _dir, compose_file = _stack_paths(name)
    args = ["-f", str(compose_file), "-p", name, "down"]
    if volumes:
        args.append("--volumes")
    return await job_mgr.submit_compose("stack.down", name, args, actor)


async def stack_restart(name: str, actor: dict | None = None) -> str:
    _dir, compose_file = _stack_paths(name)
    return await job_mgr.submit_compose(
        "stack.restart", name, ["-f", str(compose_file), "-p", name, "restart"], actor
    )


async def stack_logs(name: str, tail: int = 200) -> dict:
    _dir, compose_file = _stack_paths(name)
    cmd = [
        "docker", "compose",
        "-f", str(compose_file),
        "-p", name,
        "logs", "--no-color", "--tail", str(tail), "--timestamps",
    ]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=_subprocess_env(),
        )
    except OSError:
        proc = None
    if proc is not None:
        try:
            out, _err = await asyncio.wait_for(proc.communicate(), timeout=_LOGS_CLI_TIMEOUT)
        except TimeoutError:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
            try:  # 回收子进程,不留悬置管道(审计 C5)
                await proc.wait()
            except Exception:  # noqa: BLE001
                pass
            return await _logs_fallback(name, tail)
        if proc.returncode == 0:
            text = out.decode("utf-8", errors="replace") if out else ""
            return {"lines": [{"stream": "stdout", "line": ln} for ln in text.splitlines()]}
    return await _logs_fallback(name, tail)


async def _logs_fallback(name: str, tail: int) -> dict:
    """CLI 不可用时: docker-py 逐容器 logs(tail, timestamps) 归并,按时间戳排序。"""
    containers = await _fetch_containers()
    if containers is None:
        raise daemon_error("docker daemon unreachable")
    entries: list[tuple[str, str]] = []
    for c in containers:
        attrs = c.attrs if hasattr(c, "attrs") else {}
        if (attrs.get("Labels") or {}).get(_PROJECT_LABEL) != name:
            continue
        try:
            data = await asyncio.to_thread(c.logs, tail=tail, timestamps=True, stream=False)
        except docker.errors.DockerException as e:
            # 单容器恰在聚合间隙被回收(compose down 等)不应拖垮整份栈日志(审计 C6)
            logger.debug("stack {} logs: container skipped: {}", name, e)
            continue
        if isinstance(data, (bytes, bytearray)):
            text = bytes(data).decode("utf-8", errors="replace")
        else:
            text = str(data or "")
        for ln in text.splitlines():
            m = _TS_RE.match(ln)
            entries.append((m.group(1) if m else "", ln))
    entries.sort(key=lambda e: e[0])
    return {"lines": [{"stream": "stdout", "line": ln} for _ts, ln in entries]}
