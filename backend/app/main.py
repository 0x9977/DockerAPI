"""FastAPI 入口。装配顺序: 中间件 → 异常处理 → 路由 → 静态 SPA。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.config import settings
from app.db import init_db
from app.errors import ApiError
from app.logsetup import setup_logging
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_log import RequestLogMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    setup_logging(settings.log_level, settings.logs_dir)
    init_db()
    from loguru import logger

    # 后台服务: 未实现(开发期)时降级为警告,不阻塞启动
    for name, mod, fn in (
        ("stats_sampler", "app.services.stats_sampler", "start"),
        ("job_mgr", "app.services.job_mgr", "start"),
    ):
        try:
            await getattr(__import__(mod, fromlist=[fn]), fn)()
        except NotImplementedError:
            logger.warning("后台服务 {} 未实现,已跳过", name)
        except Exception as e:  # pragma: no cover
            logger.error("后台服务 {} 启动失败: {}", name, e)
    yield
    for mod, fn in (
        ("app.services.stats_sampler", "stop"),
        ("app.services.job_mgr", "stop"),
    ):
        try:
            await getattr(__import__(mod, fromlist=[fn]), fn)()
        except Exception:
            pass


app = FastAPI(title="DockerAPI", version=__version__, lifespan=lifespan)

# ---------- 请求体上限(1MB,见 api.md) ----------
_MAX_BODY = 1024 * 1024


@app.middleware("http")
async def body_limit_middleware(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > _MAX_BODY:
        return JSONResponse(
            status_code=413,
            content={"error": {"code": "payload_too_large", "message": f"request body exceeds {_MAX_BODY} bytes"}},
        )
    return await call_next(request)


app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestLogMiddleware)


# ---------- 异常处理: 统一错误信封 ----------
@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # 剔除 input 原值(可能含密码字段),只保留校验错误结构
    errs = [
        {k: v for k, v in e.items() if k != "input"} for e in exc.errors()[:3]
    ]
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "validation_error", "message": str(errs)}},
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed", 429: "rate_limited"}.get(
        exc.status_code, f"http_{exc.status_code}"
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": str(exc.detail)}},
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
    from loguru import logger

    logger.exception("unhandled error on {method} {path}", method=request.method, path=request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "internal server error"}},
    )


# ---------- 路由 ----------
from app.routers import audit, auth, containers, jobs, keys, stacks, system  # noqa: E402

app.include_router(system.health_router)
app.include_router(auth.router, prefix="/api/v1")
app.include_router(keys.router, prefix="/api/v1")
app.include_router(containers.router, prefix="/api/v1")
app.include_router(stacks.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(audit.router, prefix="/api/v1")
app.include_router(system.router)

# ---------- 前端静态托管(构建产物存在时) ----------
_static = Path(__file__).parent / "static"
if (_static / "index.html").exists():
    from fastapi.responses import FileResponse

    @app.api_route(
        "/{full_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    async def spa(full_path: str):
        """SPA 静态资源 + history 路由回退;/api 未匹配路径(含非 GET)统一 404 信封。"""
        if full_path.startswith("api/") or full_path.startswith("api"):
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "not_found", "message": f"no such endpoint: /{full_path}"}},
            )
        candidate = (_static / full_path).resolve() if full_path else None
        if candidate is not None and candidate.is_file() and candidate.is_relative_to(_static.resolve()):
            return FileResponse(candidate)
        return FileResponse(_static / "index.html")
