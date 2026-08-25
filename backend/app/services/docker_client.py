"""docker-py 客户端单例。测试时 monkeypatch get_client 返回 fake。"""
from __future__ import annotations

from typing import Any

import docker

from app.config import settings

_client: Any = None


def get_client() -> Any:
    """返回 DockerClient 单例。DOCKER_HOST 可覆盖连接方式。"""
    global _client
    if _client is None:
        _client = docker.DockerClient(
            base_url=settings.docker_host,
            timeout=settings.docker_timeout,
        )
    return _client


def reset_client() -> None:
    """测试与配置变更后重置。"""
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass
    _client = None
