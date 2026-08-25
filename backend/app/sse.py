"""SSE 序列化辅助: 服务层产出 dict 帧,路由层在此转成合法 JSON data。

- sse-starlette 对非字符串 data 会用 Python repr(单引号,非法 JSON),
  前端 JSON.parse 会失败——所以统一在路由边界 json.dumps。
- 生成器在响应头(200)发出后才抛出的 ApiError(如 404/429)无法变成
  HTTP 状态码,这里转成首帧 `event: error` 后终止;前端把 error 事件
  视为致命(停止重连)。
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator

from app.errors import ApiError


def sse_json_frames(gen: AsyncGenerator[dict, None]) -> AsyncGenerator[dict, None]:
    async def wrap() -> AsyncGenerator[dict, None]:
        try:
            async for ev in gen:
                data: Any = ev.get("data")
                if data is not None and not isinstance(data, str):
                    ev = {**ev, "data": json.dumps(data, ensure_ascii=False)}
                yield ev
        except ApiError as e:
            yield {
                "event": "error",
                "data": json.dumps(
                    {"code": e.code, "message": e.message, "status": e.status},
                    ensure_ascii=False,
                ),
            }

    return wrap()
