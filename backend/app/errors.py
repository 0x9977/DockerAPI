"""统一错误体系,见 docs/api.md。

所有业务错误抛 ApiError,由 main.py 的全局 handler 翻译成
{"error": {"code": ..., "message": ...}} 响应。
"""
from __future__ import annotations


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def not_found(code: str, message: str) -> ApiError:
    return ApiError(404, code, message)


def conflict(message: str) -> ApiError:
    return ApiError(409, "conflict", message)


def daemon_error(message: str) -> ApiError:
    return ApiError(502, "daemon_error", message)


def daemon_timeout(message: str = "docker daemon timeout") -> ApiError:
    return ApiError(504, "daemon_timeout", message)


def map_daemon_error(e: Exception) -> ApiError:
    """把 docker-py/requests 层异常统一翻译为 ApiError(所有 Service 复用,见 api.md 翻译表)。"""
    import requests
    from docker.errors import APIError, DockerException, NotFound  # noqa: F401

    if isinstance(e, NotFound):
        return ApiError(404, "container_not_found", str(e))
    if isinstance(e, (requests.exceptions.Timeout, requests.exceptions.ConnectionError, TimeoutError, ConnectionError)):
        return daemon_timeout(str(e) or "docker daemon unreachable")
    if isinstance(e, APIError):
        if e.response is not None and getattr(e.response, "status_code", None) == 409:
            return conflict(str(e))
        return daemon_error(str(e))
    if isinstance(e, DockerException):
        return daemon_error(str(e))
    return daemon_error(f"{type(e).__name__}: {e}")
