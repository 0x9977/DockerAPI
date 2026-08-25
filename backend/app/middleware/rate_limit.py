"""登录防爆破限速,见 docs/auth.md: 同一 IP 连续失败 5 次锁定 60 秒,
期间登录返回 429 rate_limited;仅作用于 POST /api/v1/auth/login。

实现: 进程内 dict + 锁,无需持久化(重启即清零,可接受)。
- record_login_failure(ip) / reset_login_failures(ip): 认证路由在
  登录失败/成功时调用的钩子(契约,类名与钩子名不可改)。
- RateLimitMiddleware: 拦截锁定中的登录请求,直接返回 429 信封
  (统一 {"error": {code, message}} 格式)。
"""
from __future__ import annotations

import math
import threading
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

LOGIN_PATH = "/api/v1/auth/login"
MAX_FAILURES = 5
LOCK_SECONDS = 60.0

_lock = threading.Lock()
_failures: dict[str, int] = {}
_locked_until: dict[str, float] = {}


def record_login_failure(ip: str) -> None:
    """登录失败一次;累计到 MAX_FAILURES 即锁定 LOCK_SECONDS。"""
    with _lock:
        count = _failures.get(ip, 0) + 1
        if count >= MAX_FAILURES:
            _locked_until[ip] = time.monotonic() + LOCK_SECONDS
            _failures[ip] = 0
        else:
            _failures[ip] = count


def reset_login_failures(ip: str) -> None:
    """登录成功(或管理员解封)时清零该 IP 的失败计数与锁定。"""
    with _lock:
        _failures.pop(ip, None)
        _locked_until.pop(ip, None)


def is_login_blocked(ip: str) -> bool:
    with _lock:
        until = _locked_until.get(ip)
        if until is None:
            return False
        if time.monotonic() >= until:
            _locked_until.pop(ip, None)
            return False
        return True


def login_retry_after(ip: str) -> int:
    with _lock:
        until = _locked_until.get(ip)
    if until is None:
        return 0
    return max(1, math.ceil(until - time.monotonic()))


class RateLimitMiddleware(BaseHTTPMiddleware):
    """锁定中的 POST /api/v1/auth/login 直接返回 429 rate_limited 信封,其余放行。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "POST" and request.url.path == LOGIN_PATH:
            ip = request.client.host if request.client else "unknown"
            if is_login_blocked(ip):
                retry = login_retry_after(ip)
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": {
                            "code": "rate_limited",
                            "message": f"too many failed logins, retry in {retry}s",
                        }
                    },
                    headers={"Retry-After": str(retry)},
                )
        return await call_next(request)
