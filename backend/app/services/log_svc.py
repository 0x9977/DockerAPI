"""容器日志服务 — 见 docs/logging.md、docs/api.md。

demux 策略(比较后选定"最稳的一种",说明如下):

- 上游 docker-py 7.1/7.2 的 ``logs()`` **尚不支持** ``demux=True``(该参数
  目前只有 ``attach()`` 实现,logs 传 demux 会 TypeError)。因此两条路径都
  采用"优先 demux,TypeError 时回退"的结构,新旧 docker-py 均可工作:
  * fetch_logs: 先调 ``container.logs(stream=False, demux=True)``;TypeError
    时回退——用 APIClient 自带的 requests.Session 直接 GET
    ``/containers/{id}/logs``,对响应体按 docker 8 字节流头手动解复用
    (``>BxxxL``: 1 字节流类型 + 3 字节填充 + 4 字节大端 payload 长度)。
    tty 容器的日志本就不多路复用,整体按 stdout 处理。tail/timestamps
    始终交给 daemon 处理,语义与 docker CLI 一致。
  * stream_logs: 先调 ``container.logs(stream=True, follow=True,
    demux=True)``;TypeError 时回退 ``api.attach(stream=True, logs=True,
    demux=True)``(attach 的 demux 在 7.x 已存在;logs=1 + stream=1 等价于
    "历史 backlog + follow",与 logs(follow=True) 行为一致)。

- demux 帧统一经 ``_split_frame`` 归一化为 ``(stdout|None, stderr|None)``
  二元组(docker-py ``demux_adaptor`` 的帧格式,兼容 ``(None, (out, err))``
  嵌套变体);行以 ``\\n`` 拆分,跨帧半行由 ``_LineAssembler`` 缓冲,流自然
  结束时残余半行补发。

- 流桥接: 阻塞的同步生成器由 ``run_in_executor`` 起的生产者线程驱动,
  帧经 ``loop.call_soon_threadsafe`` 投递到 ``asyncio.Queue``;消费者
  (async generator)断开时置 stop 事件并 close 底层流,不留孤儿订阅。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import re
import struct
import threading
from collections.abc import AsyncGenerator, Iterator
from typing import Any

from docker.errors import NotFound
from loguru import logger

from app.errors import ApiError, map_daemon_error
from app.services import docker_client

_MAX_STREAMS = 5                      # 同容器并发 SSE 订阅上限(见 docs/logging.md)
_GLOBAL_STREAM_CAP = 20               # 全局并发日志流上限(保护专用线程池)
_STREAM_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=16, thread_name_prefix="dapi-logstream"
)
_QUEUE_MAX = 500                      # 每路订阅的队列水位(背压)
_HEADER = struct.Struct(">BxxxL")     # docker 流式帧头
_STREAM_STDOUT = 1
_STREAM_STDERR = 2

# daemon timestamps=true 时行首的 RFC3339 时间戳 + 单空格前缀
_TS_PREFIX = re.compile(
    r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))[ ](.*)$"
)

_streams: dict[str, int] = {}         # 容器 id -> 活跃 SSE 订阅数(仅事件循环线程读写)
_global_streams = 0                   # 全局活跃订阅数(仅事件循环线程读写)


# ---------------------------------------------------------------- 基础工具


async def _get_container(cid: str) -> Any:
    """解析容器(接受完整 id/名称),不存在抛 404,daemon 故障抛 504/502。"""
    try:
        return await asyncio.to_thread(docker_client.get_client().containers.get, cid)
    except NotFound as e:
        raise ApiError(404, "container_not_found", f"No such container: {cid}") from e
    except ApiError:
        raise
    except Exception as e:  # noqa: BLE001 - daemon 不可达/超时
        raise map_daemon_error(e) from e


def _clean_str(line: str) -> str:
    """去掉行尾 CR(容器进程写 CRLF 时),保留其余内容。"""
    return line[:-1] if line.endswith("\r") else line


def _iter_lines(data: bytes) -> Iterator[str]:
    """按 \\n 拆行;末尾换行不产生空行,行内空行保留。"""
    if not data:
        return
    text = data.decode("utf-8", errors="replace")
    if text.endswith("\n"):
        text = text[:-1]
    for part in text.split("\n"):
        yield _clean_str(part)


def _split_frame(frame: Any) -> tuple[bytes, bytes]:
    """归一化 demux 帧为 (stdout, stderr) bytes。

    docker-py demux 流帧为 ``(out|None, err|None)``;兼容 ``(None, (out, err))``
    嵌套变体与裸 bytes 块(tty,按 stdout)。
    """
    if isinstance(frame, (bytes, bytearray)):
        return bytes(frame), b""
    if isinstance(frame, tuple):
        if len(frame) == 2:
            a, b = frame
            if isinstance(b, tuple):
                return _split_frame(b)
            return a or b"", b or b""
    return b"", b""


def _demux_bytes(raw: bytes, tty: bool) -> tuple[bytes, bytes]:
    """按 8 字节流头手动解复用完整响应体(tty 时无流头,整体 stdout)。"""
    if tty:
        return raw, b""
    out = bytearray()
    err = bytearray()
    i, n = 0, len(raw)
    while i + _HEADER.size <= n:
        stream_type, length = _HEADER.unpack_from(raw, i)
        i += _HEADER.size
        payload = raw[i : i + length]
        i += len(payload)
        if stream_type == _STREAM_STDOUT:
            out += payload
        elif stream_type == _STREAM_STDERR:
            err += payload
        if len(payload) < length:  # 响应被截断,放弃残余
            break
    return bytes(out), bytes(err)


class _LineAssembler:
    """把 demux 帧装配成完整行;半行跨帧缓冲,流结束时 flush 残余。"""

    def __init__(self) -> None:
        self._buf: dict[str, bytes] = {"stdout": b"", "stderr": b""}

    def feed_frame(self, frame: Any) -> list[tuple[str, str]]:
        out, err = _split_frame(frame)
        lines: list[tuple[str, str]] = []
        if out:
            lines.extend(self._feed("stdout", out))
        if err:
            lines.extend(self._feed("stderr", err))
        return lines

    def _feed(self, name: str, data: bytes) -> list[tuple[str, str]]:
        buf = self._buf[name] + data
        if b"\n" not in buf:
            self._buf[name] = buf
            return []
        parts = buf.split(b"\n")
        self._buf[name] = parts.pop()  # 末段是半行,继续缓冲
        return [(name, _clean_str(p.decode("utf-8", errors="replace"))) for p in parts]

    def flush(self) -> list[tuple[str, str]]:
        """流结束时输出残余半行(若有)。"""
        lines = [(n, _clean_str(self._buf[n].decode("utf-8", errors="replace")))
                 for n in ("stdout", "stderr") if self._buf[n]]
        self._buf = {"stdout": b"", "stderr": b""}
        return lines


# ---------------------------------------------------------------- 一次性拉取


async def fetch_logs(cid: str, tail: int = 200, timestamps: bool = False) -> dict:
    """``GET /containers/{id}/logs`` 的 Service 层。

    Returns:
        ``{"lines": [{"stream": "stdout|stderr", "line": str, "ts": str|None}]}``;
        stdout 行在前、stderr 行在后(daemon demux 本就丢失交织顺序)。
        timestamps=True 时剥离 daemon 行首的 ISO 时间戳填入 ts。

    Raises:
        ApiError: 404 container_not_found / 5xx 时 502 daemon_error(回退路径)。
    """
    container = await _get_container(cid)

    def _fetch() -> tuple[bytes, bytes]:
        try:
            result = container.logs(tail=tail, timestamps=timestamps, stream=False, demux=True)
            return _split_frame(result)
        except TypeError:
            pass  # docker-py 7.1/7.2 的 logs() 无 demux 参数 → 原始端点手动解复用
        except ApiError:
            raise
        except Exception as e:  # noqa: BLE001 - daemon 故障
            raise map_daemon_error(e)
        try:
            return _fetch_raw_logs(container, tail, timestamps)
        except ApiError:
            raise
        except Exception as e:  # noqa: BLE001
            raise map_daemon_error(e)

    out, err = await asyncio.to_thread(_fetch)

    lines: list[dict] = []
    for stream_name, data in (("stdout", out), ("stderr", err)):
        for line in _iter_lines(data):
            ts: str | None = None
            if timestamps:
                m = _TS_PREFIX.match(line)
                if m:
                    ts = m.group(1)
                    line = m.group(2)
            lines.append({"stream": stream_name, "line": line, "ts": ts})
    return {"lines": lines}


def _fetch_raw_logs(container: Any, tail: int, timestamps: bool) -> tuple[bytes, bytes]:
    """回退路径: 直接 GET /containers/{id}/logs 并手动解复用(见模块 docstring)。"""
    api = docker_client.get_client().api
    params = {
        "stdout": 1,
        "stderr": 1,
        "timestamps": 1 if timestamps else 0,
        "follow": 0,
        "tail": tail,
    }
    res = api.get(
        api._url("/containers/{0}/logs", container.id),
        params=params,
        stream=True,
        timeout=getattr(api, "timeout", None),
    )
    try:
        if res.status_code >= 400:
            _raise_daemon_status(res.status_code, container.id)
        raw = res.content
    finally:
        res.close()
    tty = bool((getattr(container, "attrs", None) or {}).get("Config", {}).get("Tty", False))
    return _demux_bytes(raw, tty)


def _raise_daemon_status(status: int, cid: str) -> None:
    if status == 404:
        raise ApiError(404, "container_not_found", f"No such container: {cid}")
    raise ApiError(502, "daemon_error", f"docker daemon returned {status} for logs")


# ---------------------------------------------------------------- SSE 实时流


def _open_stream(container: Any, tail: int) -> Iterator[Any]:
    """打开阻塞的日志流,带 tail 限制(不回放全量历史)。

    优先 logs(..., demux=True)(docker-py 新版/测试替身直接给 demux 帧)。
    TypeError(docker-py 7.1/7.2 的 logs() 无 demux)时注意: 其无 demux 的
    logs(stream=True) 返回的 CancellableStream 已被 _stream_helper 自动解
    复用(chunk 是无帧头的 payload,且丢失 stdout/stderr 区分),不能再二次
    解帧——回退走原始 HTTP 端点拿真帧(同 _fetch_raw_logs),由
    _RawDemuxStream 增量解帧;tty 容器无帧头,整体按 stdout。
    """
    try:
        return container.logs(stream=True, follow=True, tail=tail, demux=True)
    except TypeError:
        return _raw_follow_stream(container, tail)


def _raw_follow_stream(container: Any, tail: int) -> "_RawDemuxStream":
    api = docker_client.get_client().api
    params = {"stdout": 1, "stderr": 1, "timestamps": 0, "follow": 1, "tail": tail}
    res = api.get(
        api._url("/containers/{0}/logs", container.id),
        params=params,
        stream=True,
        timeout=getattr(api, "timeout", None),
    )
    if res.status_code >= 400:
        res.close()
        _raise_daemon_status(res.status_code, container.id)
    tty = bool((getattr(container, "attrs", None) or {}).get("Config", {}).get("Tty", False))
    return _RawDemuxStream(res.iter_content(chunk_size=None), tty, closer=res.close)


class _RawDemuxStream:
    """把原始多路复用字节流(未解帧)迭代成 demux 帧二元组(跨块缓冲)。"""

    def __init__(self, raw: Iterator[bytes], tty: bool, closer: Any = None) -> None:
        self._raw = raw
        self._tty = tty
        self._buf = b""
        self._closer = closer

    def __iter__(self) -> "_RawDemuxStream":
        return self

    def __next__(self) -> tuple[bytes | None, bytes | None]:
        while True:
            if self._tty:
                chunk = next(self._raw)
                return (chunk, None)
            while len(self._buf) >= 8:
                mark = self._buf[0]
                size = int.from_bytes(self._buf[4:8], "big")
                if len(self._buf) < 8 + size:
                    break
                payload = self._buf[8 : 8 + size]
                self._buf = self._buf[8 + size :]
                if mark == _STREAM_STDERR:
                    return (None, payload)
                return (payload, None)  # mark==0/1 及未知类型按 stdout
            self._buf += next(self._raw)

    def close(self) -> None:
        try:
            if self._closer is not None:
                self._closer()
                return
            close = getattr(self._raw, "close", None)
            if callable(close):
                close()
        except Exception:  # noqa: BLE001 - 清理路径不抛
            pass


def _close_quietly(it: Any) -> None:
    """尽力关闭底层流(CancellableStream.close 会关闭 daemon 连接)。"""
    if it is None:
        return
    try:
        close = getattr(it, "close", None)
        if callable(close):
            close()
    except Exception:  # noqa: BLE001 - 清理路径不抛
        pass


def _make_emitter(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue):
    """生产者线程 → 事件循环队列(线程安全投递 + 背压丢行标记)。"""
    state = {"dropped": False}

    def _emit(item: tuple) -> None:
        def _put() -> None:
            try:
                if state["dropped"]:
                    state["dropped"] = False
                    queue.put_nowait(("line", "stderr", "...[输出过快,部分行已丢弃]..."))
                queue.put_nowait(item)
            except asyncio.QueueFull:
                state["dropped"] = True
            except Exception:  # noqa: BLE001 - 消费者已断开时静默丢弃
                pass

        try:
            loop.call_soon_threadsafe(_put)
        except RuntimeError:  # 事件循环已关闭(进程退出)
            pass

    return _emit


async def stream_logs(cid: str, tail: int = 200) -> AsyncGenerator[dict, None]:
    """``GET /containers/{id}/logs/stream`` 的事件源。

    Yields:
        ``{"data": {"stream": ..., "line": ...}}`` 每完整行一帧(打开时回放
        最近 tail 行,之后实时增量);流自然结束(容器停止/删除)后
        ``{"event": "end", "data": {"reason": "closed"}}``;
        daemon 断开等异常 ``{"event": "error", "data": {"message": str}}``。

    Raises:
        ApiError: 404 container_not_found;同容器并发订阅超过 5 或全局超过
        20 → 429 too_many_streams(客户端断开/流结束后计数回落)。
    """
    global _global_streams
    container = await _get_container(cid)
    key: str = getattr(container, "id", cid)

    count = _streams.get(key, 0)
    if count >= _MAX_STREAMS or _global_streams >= _GLOBAL_STREAM_CAP:
        logger.warning(
            "log stream limit reached: container={} subs={} global={}", key, count, _global_streams
        )
        raise ApiError(429, "too_many_streams", "log stream subscription limit reached")
    _streams[key] = count + 1
    _global_streams += 1

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    emit = _make_emitter(loop, queue)
    stop = threading.Event()
    holder: list[Any] = [None]  # 生产者线程放底层流,供消费者断开时强制关闭

    def _producer() -> None:
        try:
            it = _open_stream(container, tail)
        except NotFound:
            emit(("error", f"No such container: {cid}"))
            return
        except Exception as e:  # noqa: BLE001 - daemon 打开失败 → error 事件
            emit(("error", str(e)))
            return
        holder[0] = it
        if stop.is_set():  # 消费者已在打开期间断开
            _close_quietly(it)
            holder[0] = None
            return
        assembler = _LineAssembler()
        try:
            for frame in it:
                if stop.is_set():
                    return
                for name, line in assembler.feed_frame(frame):
                    emit(("line", name, line))
            for name, line in assembler.flush():
                emit(("line", name, line))
            emit(("end",))
        except Exception as e:  # noqa: BLE001 - daemon 中途断开 → error 事件
            emit(("error", str(e)))
        finally:
            _close_quietly(it)
            holder[0] = None

    producer_fut = loop.run_in_executor(_STREAM_POOL, _producer)
    try:
        while True:
            item = await queue.get()
            if item[0] == "line":
                yield {"data": {"stream": item[1], "line": item[2]}}
            elif item[0] == "error":
                yield {"event": "error", "data": {"message": item[1]}}
                return
            else:  # "end"
                yield {"event": "end", "data": {"reason": "closed"}}
                return
    finally:
        # 客户端断开(CancelledError/GeneratorExit)或正常 return:
        # 通知生产者停止、强关底层流、订阅计数回落
        stop.set()
        _close_quietly(holder[0])
        remaining = _streams.get(key, 1) - 1
        if remaining > 0:
            _streams[key] = remaining
        else:
            _streams.pop(key, None)
        _global_streams = max(0, _global_streams - 1)
        _ = producer_fut  # 不 await: 生产者最迟在下一帧自行退出
