"""loguru 装配,见 docs/logging.md: stdout + 文件轮转 + 脱敏 filter。"""
from __future__ import annotations

import re
import sys

from loguru import logger

_MASK_PATTERNS = [
    re.compile(r"dka_[A-Za-z0-9]+"),
    re.compile(r"Bearer\s+\S+"),
    re.compile(r"(\"?password\"?\s*[:=]\s*)\S+"),
]


def _scrub(text: str) -> str:
    for pat in _MASK_PATTERNS:
        text = pat.sub(lambda m: (m.group(1) + "***") if m.lastindex else "***", text)
    return text


def _filter(record) -> bool:
    msg = record["message"]
    if isinstance(msg, str):
        scrubbed = _scrub(msg)
        if scrubbed != msg:
            record["message"] = scrubbed
    return True


def setup_logging(level: str, logs_dir) -> None:
    logger.remove()
    logger.add(sys.stderr, level=level, filter=_filter)
    logger.add(
        logs_dir / "dockerapi.log",
        level=level,
        filter=_filter,
        rotation="10 MB",
        retention=5,
        encoding="utf-8",
    )
