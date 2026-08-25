"""log_svc 单测: demux 解析、时间戳剥离、手动解复用回退、SSE 帧/end/429/断开清理。"""
from __future__ import annotations

import asyncio
import struct
import time
from typing import Any, Iterator

import pytest
from docker.errors import NotFound

from app.errors import ApiError
from app.services import docker_client, log_svc


# ---------------------------------------------------------------- fakes


def _frame_gen(frames: list[Any], delay: float = 0.0) -> Iterator[Any]:
    """模拟 docker-py demux 流的同步生成器(可被 close)。"""

    def _gen() -> Iterator[Any]:
        for f in frames:
            if delay:
                time.sleep(delay)
            yield f

    return _gen()


class FakeContainer:
    """logs() 返回可控: stream=False 返回 demux 二元组,stream=True 返回帧生成器。"""

    def __init__(
        self,
        cid: str,
        fetch: tuple[bytes, bytes] = (b"", b""),
        frames: list[Any] | None = None,
        raise_demux: bool = False,
        stream_error: Exception | None = None,
        attrs: dict | None = None,
    ) -> None:
        self.id = cid
        self._fetch = fetch
        self._frames = frames
        self._raise_demux = raise_demux
        self._stream_error = stream_error
        self.attrs = attrs if attrs is not None else {
            "State": {"Running": True},
            "Config": {"Tty": False},
        }
        self.logs_kwargs: list[dict] = []

    def logs(self, **kwargs: Any) -> Any:
        self.logs_kwargs.append(kwargs)
        if self._raise_demux and "demux" in kwargs:
            raise TypeError("logs() got an unexpected keyword argument 'demux'")
        if kwargs.get("stream"):
            if self._stream_error is not None:
                raise self._stream_error
            assert self._frames is not None
            return _frame_gen(self._frames)
        return self._fetch


class FakeContainers:
    def __init__(self, cmap: dict[str, FakeContainer]) -> None:
        self._cmap = cmap
        self.get_calls: list[str] = []

    def get(self, cid: str) -> FakeContainer:
        self.get_calls.append(cid)
        if cid not in self._cmap:
            raise NotFound(f"No such container: {cid}")
        return self._cmap[cid]


class FakeDocker:
    def __init__(self, cmap: dict[str, FakeContainer], api: Any = None) -> None:
        self.containers = FakeContainers(cmap)
        self.api = api


class FakeResponse:
    def __init__(self, content: bytes, status: int = 200) -> None:
        self.content = content
        self.status_code = status
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeApi:
    """回退路径用的低层 api fake: _url + get。"""

    def __init__(self, raw: bytes = b"", status: int = 200) -> None:
        self._raw = raw
        self._status = status
        self.get_calls: list[dict] = []

    def _url(self, fmt: str, cid: str) -> str:
        return f"http://fake/v1.47/{fmt.format(cid)}"

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.get_calls.append({"url": url, **kwargs})
        return FakeResponse(self._raw, self._status)


def _mux(stream_type: int, payload: bytes) -> bytes:
    return struct.pack(">BxxxL", stream_type, len(payload)) + payload


@pytest.fixture()
def install(monkeypatch: pytest.MonkeyPatch):
    """注册 fake docker 客户端;用例结束清理订阅计数。"""
    log_svc._streams.clear()

    def _install(cmap: dict[str, FakeContainer], api: Any = None) -> FakeDocker:
        fake = FakeDocker(cmap, api)
        monkeypatch.setattr(docker_client, "get_client", lambda: fake)
        return fake

    yield _install
    log_svc._streams.clear()


# ---------------------------------------------------------------- fetch_logs


async def test_fetch_demux_split_by_stream(install) -> None:
    c = FakeContainer("c1", fetch=(b"out1\nout2\n", b"err1\n"))
    install({"c1": c})
    r = await log_svc.fetch_logs("c1", tail=10, timestamps=False)
    assert r == {
        "lines": [
            {"stream": "stdout", "line": "out1", "ts": None},
            {"stream": "stdout", "line": "out2", "ts": None},
            {"stream": "stderr", "line": "err1", "ts": None},
        ]
    }
    # 调用参数: demux 契约
    assert c.logs_kwargs == [{"tail": 10, "timestamps": False, "stream": False, "demux": True}]


async def test_fetch_none_and_empty_streams(install) -> None:
    # docker-py demux 无输出的一侧为 None
    install({"c1": FakeContainer("c1", fetch=(b"a\n", None))})
    assert (await log_svc.fetch_logs("c1"))["lines"] == [
        {"stream": "stdout", "line": "a", "ts": None}
    ]
    install({"c2": FakeContainer("c2", fetch=(None, None))})
    assert (await log_svc.fetch_logs("c2"))["lines"] == []


async def test_fetch_trailing_newline_and_blank_lines(install) -> None:
    install({"c1": FakeContainer("c1", fetch=(b"a\n\nb\n", b""))})
    r = await log_svc.fetch_logs("c1")
    assert [x["line"] for x in r["lines"]] == ["a", "", "b"]
    install({"c2": FakeContainer("c2", fetch=(b"\n", b""))})
    assert [x["line"] for x in (await log_svc.fetch_logs("c2"))["lines"]] == [""]


async def test_fetch_timestamps_stripped(install) -> None:
    install(
        {
            "c1": FakeContainer(
                "c1",
                fetch=(
                    b"2026-08-25T03:30:00.123456789Z hello world\n2026-08-25T03:30:01Z second\n",
                    b"2026-08-25T03:30:02Z oops\n",
                ),
            )
        }
    )
    r = await log_svc.fetch_logs("c1", tail=50, timestamps=True)
    assert r["lines"] == [
        {"stream": "stdout", "line": "hello world", "ts": "2026-08-25T03:30:00.123456789Z"},
        {"stream": "stdout", "line": "second", "ts": "2026-08-25T03:30:01Z"},
        {"stream": "stderr", "line": "oops", "ts": "2026-08-25T03:30:02Z"},
    ]


async def test_fetch_timestamps_false_keeps_line_intact(install) -> None:
    install({"c1": FakeContainer("c1", fetch=(b"2026-08-25T03:30:00Z not stripped\n", b""))})
    r = await log_svc.fetch_logs("c1", timestamps=False)
    assert r["lines"] == [
        {"stream": "stdout", "line": "2026-08-25T03:30:00Z not stripped", "ts": None}
    ]


async def test_fetch_crlf_lines(install) -> None:
    install({"c1": FakeContainer("c1", fetch=(b"a\r\nb\r\n", b""))})
    r = await log_svc.fetch_logs("c1")
    assert [x["line"] for x in r["lines"]] == ["a", "b"]


async def test_fetch_container_not_found(install) -> None:
    install({})
    with pytest.raises(ApiError) as ei:
        await log_svc.fetch_logs("nope")
    assert ei.value.status == 404
    assert ei.value.code == "container_not_found"


# ---------------------------- fetch 回退: 手动 8 字节流头解复用


async def test_fetch_fallback_manual_demux(install) -> None:
    raw = (
        _mux(1, b"hello\n")
        + _mux(2, b"warn\n")
        + _mux(1, b"world\n")
        + _mux(2, b"again\n")
    )
    api = FakeApi(raw)
    c = FakeContainer("c1", raise_demux=True)
    install({"c1": c}, api=api)
    r = await log_svc.fetch_logs("c1", tail=7, timestamps=False)
    assert r["lines"] == [
        {"stream": "stdout", "line": "hello", "ts": None},
        {"stream": "stdout", "line": "world", "ts": None},
        {"stream": "stderr", "line": "warn", "ts": None},
        {"stream": "stderr", "line": "again", "ts": None},
    ]
    # 回退请求打到 logs 端点,参数正确
    call = api.get_calls[0]
    assert "/containers/c1/logs" in call["url"]
    assert call["params"]["tail"] == 7
    assert call["params"]["follow"] == 0
    assert call["params"]["stdout"] == 1 and call["params"]["stderr"] == 1


async def test_fetch_fallback_tty_all_stdout(install) -> None:
    api = FakeApi(b"raw line\nraw2\n")
    c = FakeContainer("c1", raise_demux=True, attrs={"State": {"Running": True}, "Config": {"Tty": True}})
    install({"c1": c}, api=api)
    r = await log_svc.fetch_logs("c1")
    assert r["lines"] == [
        {"stream": "stdout", "line": "raw line", "ts": None},
        {"stream": "stdout", "line": "raw2", "ts": None},
    ]


async def test_fetch_fallback_daemon_404(install) -> None:
    install({"c1": FakeContainer("c1", raise_demux=True)}, api=FakeApi(b"", status=404))
    with pytest.raises(ApiError) as ei:
        await log_svc.fetch_logs("c1")
    assert ei.value.status == 404
    assert ei.value.code == "container_not_found"


async def test_fetch_fallback_daemon_500(install) -> None:
    install({"c1": FakeContainer("c1", raise_demux=True)}, api=FakeApi(b"", status=500))
    with pytest.raises(ApiError) as ei:
        await log_svc.fetch_logs("c1")
    assert ei.value.status == 502
    assert ei.value.code == "daemon_error"


def test_demux_bytes_ignores_partial_and_unknown() -> None:
    raw = _mux(1, b"a\n") + b"\x01\x00\x00\x00" + b"xx"  # 半个头
    assert log_svc._demux_bytes(raw, tty=False) == (b"a\n", b"")
    # 未知流类型: 载荷被跳过但不破坏后续帧
    weird = struct.pack(">BxxxL", 9, 3) + b"zzz" + _mux(2, b"e\n")
    assert log_svc._demux_bytes(weird, tty=False) == (b"", b"e\n")
    assert log_svc._demux_bytes(b"plain", tty=True) == (b"plain", b"")


def test_split_frame_variants() -> None:
    assert log_svc._split_frame((b"a", None)) == (b"a", b"")
    assert log_svc._split_frame((None, b"b")) == (b"", b"b")
    assert log_svc._split_frame((None, (b"a", b"b"))) == (b"a", b"b")  # 嵌套变体
    assert log_svc._split_frame(b"raw") == (b"raw", b"")  # tty 裸块
    assert log_svc._split_frame("junk") == (b"", b"")


# ---------------------------------------------------------------- stream_logs


async def test_stream_frames_and_end_event(install) -> None:
    frames = [(b"a\n", None), (None, b"b\n"), (b"c\n", None)]
    install({"c1": FakeContainer("c1", frames=frames)})
    items = [x async for x in log_svc.stream_logs("c1")]
    assert items == [
        {"data": {"stream": "stdout", "line": "a"}},
        {"data": {"stream": "stderr", "line": "b"}},
        {"data": {"stream": "stdout", "line": "c"}},
        {"event": "end", "data": {"reason": "closed"}},
    ]
    # 流结束后订阅计数回落
    assert log_svc._streams == {}


async def test_stream_line_assembly_across_frames(install) -> None:
    frames = [
        (b"par", None),
        (b"ti", None),
        (b"al\nsec", None),
        (b"ond\n", None),
        (None, b"tail-no-newline"),
    ]
    install({"c1": FakeContainer("c1", frames=frames)})
    items = [x async for x in log_svc.stream_logs("c1")]
    assert items == [
        {"data": {"stream": "stdout", "line": "partial"}},
        {"data": {"stream": "stdout", "line": "second"}},
        {"data": {"stream": "stderr", "line": "tail-no-newline"}},  # 结束时残余半行补发
        {"event": "end", "data": {"reason": "closed"}},
    ]


async def test_stream_not_found(install) -> None:
    install({})
    gen = log_svc.stream_logs("nope")
    with pytest.raises(ApiError) as ei:
        await gen.__anext__()
    assert ei.value.status == 404
    assert ei.value.code == "container_not_found"


async def test_stream_open_error_event(install) -> None:
    install({"c1": FakeContainer("c1", stream_error=RuntimeError("daemon boom"))})
    items = [x async for x in log_svc.stream_logs("c1")]
    assert items == [{"event": "error", "data": {"message": "daemon boom"}}]
    assert log_svc._streams == {}


async def test_stream_limit_429_and_release(install) -> None:
    # 200 帧 × 30ms ≈ 6s,足够 5 路并发各取一帧后再开第 6 路
    frames = [(f"l{i}\n".encode(), None) for i in range(200)]
    install({"c1": FakeContainer("c1", frames=frames)})

    gens = []
    for _ in range(5):
        g = log_svc.stream_logs("c1")
        first = await g.__anext__()
        assert first == {"data": {"stream": "stdout", "line": "l0"}}
        gens.append(g)
    assert log_svc._streams["c1"] == 5

    g6 = log_svc.stream_logs("c1")
    with pytest.raises(ApiError) as ei:
        await g6.__anext__()
    assert ei.value.status == 429
    assert ei.value.code == "too_many_streams"

    # 客户端断开: 计数同步回落
    for g in gens:
        await g.aclose()
    assert log_svc._streams == {}

    # 回落后可重新订阅
    g7 = log_svc.stream_logs("c1")
    assert await g7.__anext__() == {"data": {"stream": "stdout", "line": "l0"}}
    await g7.aclose()
    assert log_svc._streams == {}


async def test_stream_disconnect_single_subscriber_release(install) -> None:
    frames = [(f"x{i}\n".encode(), None) for i in range(100)]
    install({"c1": FakeContainer("c1", frames=frames)})
    g = log_svc.stream_logs("c1")
    await g.__anext__()
    assert log_svc._streams["c1"] == 1
    await g.aclose()
    assert log_svc._streams == {}
    # 给生产者线程一点时间退出(不断言,只确保不崩)
    await asyncio.sleep(0.02)


async def test_stream_only_counts_while_open(install) -> None:
    # 前一个流自然结束后,同容器还能再开 5 路(计数确实清零)
    for round_no in range(6):
        install({"c1": FakeContainer("c1", frames=[(b"one\n", None)])})
        items = [x async for x in log_svc.stream_logs("c1")]
        assert items[-1] == {"event": "end", "data": {"reason": "closed"}}
        assert log_svc._streams == {}, round_no


async def test_stream_counts_by_container_id(install) -> None:
    # 不同容器互不影响
    slow = [(f"s{i}\n".encode(), None) for i in range(100)]
    install(
        {
            "c1": FakeContainer("c1", frames=slow),
            "c2": FakeContainer("c2", frames=slow),
        }
    )
    g1 = log_svc.stream_logs("c1")
    await g1.__anext__()
    g2 = log_svc.stream_logs("c2")
    await g2.__anext__()
    assert log_svc._streams == {"c1": 1, "c2": 1}
    await g1.aclose()
    await g2.aclose()
    assert log_svc._streams == {}


def test_raw_demux_stream_incremental_frames() -> None:
    """原始帧流(跨块半包)增量解帧: 回退路径的核心,真实 daemon 走此路径。"""
    import struct

    from app.services.log_svc import _RawDemuxStream

    hdr = struct.Struct(">BxxxL")

    def frame(mark: int, payload: bytes) -> bytes:
        return hdr.pack(mark, len(payload)) + payload

    chunks = [
        frame(1, b"out1\n") + frame(2, b"err1\n")[:6],   # 完整帧 + 半个帧头
        frame(2, b"err1\n")[6:],                          # 补齐帧头与 payload
        frame(1, b"out2\n"),
    ]
    closed = []
    it = _RawDemuxStream(iter(chunks), tty=False, closer=lambda: closed.append(True))
    frames = [f for f in it]
    assert frames == [(b"out1\n", None), (None, b"err1\n"), (b"out2\n", None)]
    it.close()
    assert closed == [True]

    # tty 模式: 无帧头,整块按 stdout
    it2 = _RawDemuxStream(iter([b"plain\n", b"line2\n"]), tty=True)
    assert [f for f in it2] == [(b"plain\n", None), (b"line2\n", None)]
