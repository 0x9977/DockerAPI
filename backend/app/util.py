"""小工具:时间与 ULID。"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid_new() -> str:
    """26 位 ULID(时间有序),job id 用。"""
    ts = int(time.time() * 1000)
    rnd = os.urandom(10)
    raw = ts.to_bytes(6, "big") + rnd
    # 128bit -> 26 char base32 (标准 ULID 编码,简化实现足够)
    n = int.from_bytes(raw, "big")
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[n & 0x1F])
        n >>= 5
    return "".join(reversed(out))
