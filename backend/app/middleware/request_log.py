"""请求日志中间件,见 docs/logging.md。SSE/WS 记建立与结束各一条。"""
from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            self._log(request, 500, start)
            raise
        self._log(request, response.status_code, start)
        return response

    @staticmethod
    def _log(request: Request, status: int, start: float) -> None:
        from loguru import logger

        dur = round((time.perf_counter() - start) * 1000, 1)
        principal = getattr(request.state, "principal", None)
        actor = f"{principal.type}:{principal.name}" if principal else "-"
        lvl = "DEBUG" if request.url.path == "/api/health" else "INFO"
        logger.log(
            lvl,
            "{method} {path} -> {status} {dur}ms actor={actor}",
            method=request.method,
            path=request.url.path,
            status=status,
            dur=dur,
            actor=actor,
        )
