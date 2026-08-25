"""容器服务 — 契约见 docs/api.md「错误码翻译表」与「关键行为细则」。

实现约定:
- docker-py 是同步库: 所有 daemon 调用一律经 asyncio.to_thread 桥接(硬规则);
- act-then-interpret: 变更操作不做 check-then-act 预检,直接执行按结果翻译;
  操作前 inspect 一次仅为取 pre-state 生成 already_in_state note,
  daemon 返回 304 同样翻译为幂等成功(200 + note);
- 同容器变更操作用模块级 per-container asyncio.Lock 串行,获取锁等待超过
  LOCK_WAIT_SECONDS 抛 409 conflict("操作正在被其他请求执行");
  容器删除成功后清理对应锁条目;
- 异常翻译: NotFound→404 container_not_found;APIError 409→409 conflict
  (message 取 str(e));requests/socket 超时或 DockerException→504
  daemon_timeout;其余 APIError→502 daemon_error。
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

import docker
import requests
from loguru import logger

from app.errors import ApiError
from app.services import docker_client, stats_sampler

LOCK_WAIT_SECONDS = 10.0

_FULL_ID_RE = re.compile(r"[0-9a-fA-F]{64}")
_SENSITIVE_RE = re.compile(r"(?i)(PASS|SECRET|TOKEN|KEY|CREDENTIAL)")
_STATE_RANK = {"running": 0, "created": 1, "exited": 2}

# per-container 锁(仅锁与 stats 环形缓冲允许驻留内存,见 AGENTS.md)
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(cid: str) -> asyncio.Lock:
    lock = _locks.get(cid)
    if lock is None:
        lock = asyncio.Lock()
        _locks[cid] = lock
    return lock


def _map_error(e: Exception, cid: str) -> ApiError | None:
    """docker-py / requests 异常 → ApiError;未知异常返回 None(原样上抛)。"""
    if isinstance(e, docker.errors.NotFound):
        return ApiError(404, "container_not_found", f"No such container: {cid}")
    if isinstance(e, docker.errors.APIError):
        if getattr(e, "status_code", None) == 409:
            return ApiError(409, "conflict", str(e))
        return ApiError(502, "daemon_error", str(e))
    if isinstance(e, (requests.exceptions.Timeout, requests.exceptions.ConnectionError, TimeoutError)):
        return ApiError(504, "daemon_timeout", "docker daemon timeout")
    if isinstance(e, docker.errors.DockerException):
        return ApiError(504, "daemon_timeout", "docker daemon timeout")
    return None


async def _call(cid: str, fn: Any, *args: Any, **kwargs: Any) -> Any:
    """asyncio.to_thread 桥接 + 统一异常翻译(304 由 _mutate 单独处理,不经此处)。"""
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except ApiError:
        raise
    except Exception as e:
        mapped = _map_error(e, cid)
        if mapped is not None:
            raise mapped from e
        raise


def _to_iso(value: Any) -> str | None:
    """daemon list 的秒级时间戳 → ISO 8601 UTC;字符串原样返回。"""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, str):
        return value
    return None


def _mask_env(env: list[str] | None) -> list[str]:
    """Env 脱敏: 变量名匹配 (?i)(PASS|SECRET|TOKEN|KEY|CREDENTIAL) → 值替换 ***。"""
    masked: list[str] = []
    for item in env or []:
        name, sep, _value = item.partition("=")
        if sep and _SENSITIVE_RE.search(name):
            masked.append(f"{name}=***")
        else:
            masked.append(item)
    return masked


def _trim_inspect(data: dict) -> dict:
    config = data.get("Config") or {}
    host_config = data.get("HostConfig") or {}
    network = data.get("NetworkSettings") or {}
    return {
        "Id": data.get("Id"),
        "Name": data.get("Name"),
        "Created": data.get("Created"),
        "State": data.get("State"),
        "Config": {
            "Image": config.get("Image"),
            "Env": _mask_env(config.get("Env")),
            "Labels": config.get("Labels") or {},
        },
        "HostConfig": {
            "PortBindings": host_config.get("PortBindings"),
            "Binds": host_config.get("Binds"),
            "RestartPolicy": host_config.get("RestartPolicy"),
        },
        "NetworkSettings": {"Ports": network.get("Ports")},
    }


async def resolve_container(cid: str) -> str:
    """cid 为完整 64 位 hex ID 时直接返回;否则 list(all=True) 按名称/ID 前缀解析。"""
    if not cid:
        raise ApiError(404, "container_not_found", "No such container: ''")
    if _FULL_ID_RE.fullmatch(cid):
        return cid
    client = docker_client.get_client()
    items = await _call(cid, client.api.containers, all=True)
    for item in items:  # 名称优先(daemon 同语义)
        for name in item.get("Names") or []:
            if name.lstrip("/") == cid:
                return item["Id"]
    matches = [item["Id"] for item in items if str(item.get("Id", "")).startswith(cid)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ApiError(404, "container_not_found", f"Ambiguous container id: {cid}")
    raise ApiError(404, "container_not_found", f"No such container: {cid}")


async def list_containers() -> list[dict]:
    """全部容器(all=True),running<created<exited(其余垫底),内嵌最近 30 点 stats。"""
    client = docker_client.get_client()
    items = await _call("", client.api.containers, all=True)
    # 锁表收割(审计 C9): 条目过多时清掉已不存在容器的锁
    if len(_locks) > 500:
        alive = {i.get("Id") for i in items}
        for k in [k for k in _locks if k not in alive]:
            _locks.pop(k, None)
    self_id = await self_container_id()
    result: list[dict] = []
    for item in items:
        cid = item.get("Id", "")
        names = item.get("Names") or []
        labels = item.get("Labels") or {}
        try:
            stats = stats_sampler.get_recent(cid, 30)
        except Exception:  # 容器无缓冲/采样器就绪失败 → 空数组(前端按无图处理)
            stats = []
        result.append(
            {
                "id": cid,
                "name": names[0].lstrip("/") if names else "",
                "image": item.get("Image", ""),
                "state": item.get("State", ""),
                "compose_project": labels.get("com.docker.compose.project"),
                "created": _to_iso(item.get("Created")),
                "is_self": cid == self_id,
                "stats": stats,
            }
        )
    result.sort(key=lambda c: _STATE_RANK.get(c["state"], 99))  # 稳定排序,组内保持 daemon 序
    return result


async def inspect_container(cid: str) -> dict:
    """daemon inspect 结果裁剪返回,Config.Env 脱敏。"""
    full_id = await resolve_container(cid)
    client = docker_client.get_client()
    c = await _call(cid, client.containers.get, full_id)
    trimmed = _trim_inspect(c.attrs or {})
    trimmed["is_self"] = full_id == await self_container_id()
    return trimmed


# ---------- 面板自身容器识别与保护 ----------

_self_container_id: str | None = None   # 缓存的面板自身容器完整 ID;None=未检测或非容器运行
_self_detected: bool = False


async def self_container_id() -> str | None:
    """面板自身容器的完整 ID(惰性检测,进程内缓存)。

    容器内运行时 hostname 即本容器短 ID(daemon 按前缀解析);裸机运行时
    解析不到(NotFound/连接失败)→ None,即无自保护对象。命中后额外校验
    full id 前缀,排除"恰好有容器与宿主机同名"的巧合。
    """
    global _self_container_id, _self_detected
    if _self_detected:
        return _self_container_id

    def _detect() -> str | None:
        import socket

        hostname = socket.gethostname()
        try:
            c = docker_client.get_client().containers.get(hostname)
        except Exception:
            return None
        full = getattr(c, "id", "") or ""
        return full if full.startswith(hostname) else None

    try:
        _self_container_id = await asyncio.to_thread(_detect)
    except Exception:  # noqa: BLE001 - 检测失败不阻断业务
        _self_container_id = None
    _self_detected = True
    if _self_container_id:
        logger.info("self-protection: panel container detected ({})", _self_container_id[:12])
    return _self_container_id


async def _ensure_not_self(full_id: str) -> None:
    """面板自身容器禁止一切变更操作——停掉自己等于整个服务中断。"""
    sid = await self_container_id()
    if sid and full_id == sid:
        raise ApiError(
            403, "self_protection", "禁止对面板自身容器执行变更操作(会导致服务中断)"
        )


async def _mutate(cid: str, action: str, target_state: str | None, t: int | None = None) -> dict:
    """变更操作公共骨架: 解析→自身保护→取锁→inspect(pre-state)→执行→翻译。

    不做 check-then-act: 无论 pre-state 如何都直接执行目标操作;
    pre-state 已是目标状态 或 daemon 返回 304 → note=already_in_state。
    restart 无目标状态语义(重启运行中容器仍是真实动作),永不加 note。
    面板自身容器一律 403(停掉自己=整个服务中断)。
    """
    full_id = await resolve_container(cid)
    await _ensure_not_self(full_id)
    lock = _lock_for(full_id)
    try:
        await asyncio.wait_for(lock.acquire(), timeout=LOCK_WAIT_SECONDS)
    except asyncio.TimeoutError:
        raise ApiError(409, "conflict", "操作正在被其他请求执行") from None

    note: str | None = None
    try:
        client = docker_client.get_client()
        c = await _call(cid, client.containers.get, full_id)
        pre_state = ((c.attrs or {}).get("State") or {}).get("Status")
        if target_state is not None and pre_state == target_state:
            note = "already_in_state"

        def _op() -> None:
            if action == "start":
                c.start()
            elif action == "stop":
                c.stop(timeout=t)
            elif action == "restart":
                c.restart(timeout=t)
            elif action == "pause":
                c.pause()
            else:
                c.unpause()

        try:
            await asyncio.to_thread(_op)
        except docker.errors.APIError as e:
            if getattr(e, "status_code", None) != 304:
                raise
            note = "already_in_state"  # daemon 304 → 幂等成功
    except ApiError:
        raise
    except Exception as e:
        mapped = _map_error(e, cid)
        if mapped is not None:
            raise mapped from e
        raise
    finally:
        lock.release()

    if note:
        return {"status": "ok", "note": note}
    return {"status": "ok"}


async def start_container(cid: str) -> dict:
    return await _mutate(cid, "start", "running")


async def stop_container(cid: str, t: int = 10) -> dict:
    return await _mutate(cid, "stop", "exited", t=t)


async def restart_container(cid: str, t: int = 10) -> dict:
    return await _mutate(cid, "restart", None, t=t)


async def pause_container(cid: str) -> dict:
    return await _mutate(cid, "pause", "paused")


async def unpause_container(cid: str) -> dict:
    return await _mutate(cid, "unpause", "running")


async def remove_container(cid: str, force: bool = False) -> dict:
    """删除容器;force=False 且运行中时 daemon 409 原样透传为 conflict。"""
    full_id = await resolve_container(cid)
    await _ensure_not_self(full_id)
    lock = _lock_for(full_id)
    try:
        await asyncio.wait_for(lock.acquire(), timeout=LOCK_WAIT_SECONDS)
    except asyncio.TimeoutError:
        raise ApiError(409, "conflict", "操作正在被其他请求执行") from None

    removed = False
    try:
        client = docker_client.get_client()
        c = await _call(cid, client.containers.get, full_id)
        await _call(cid, c.remove, force=force)
        removed = True
    except ApiError:
        raise
    except Exception as e:
        mapped = _map_error(e, cid)
        if mapped is not None:
            raise mapped from e
        raise
    finally:
        if removed:
            _locks.pop(full_id, None)  # 容器已删除,清理锁条目
        lock.release()
    return {"status": "ok"}
