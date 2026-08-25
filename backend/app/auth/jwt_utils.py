"""JWT(HS256)与密码哈希工具,见 docs/auth.md。

- 签名密钥: 首次使用时从 settings.secret_path 读取;文件不存在则生成
  64 字节随机数的 hex(128 字符)写入持久化(容器重建不失效)。
  0600 权限仅 Linux 生效,Windows 下忽略失败。
- token payload: {sub, iat, exp},exp = settings.jwt_expire_hours 小时。
- 密码哈希: bcrypt cost 12(明文 > 72 字节直接拒绝,bcrypt 硬限制)。
- 永不将密钥/明文密码写入日志。
"""
from __future__ import annotations

import os
import secrets
import threading
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt as pyjwt

from app.config import settings

_SECRET_CACHE: str | None = None
_SECRET_LOCK = threading.Lock()

# 未知用户登录时做一次等代价校验,消除用户名枚举时序侧信道
_DUMMY_HASH = bcrypt.hashpw(b"dockerapi-dummy", bcrypt.gensalt(rounds=12))


def _load_secret() -> str:
    """读取(或首次生成并持久化)JWT 签名密钥,进程内缓存。"""
    global _SECRET_CACHE
    if _SECRET_CACHE is not None:
        return _SECRET_CACHE
    with _SECRET_LOCK:
        if _SECRET_CACHE is None:
            path = settings.secret_path
            if path.exists():
                data = path.read_text(encoding="utf-8").strip()
            else:
                data = secrets.token_hex(64)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(data, encoding="utf-8")
                try:
                    os.chmod(path, 0o600)  # Linux 收紧权限;Windows 忽略
                except OSError:
                    pass
            if not data:
                raise RuntimeError(f"jwt secret file is empty: {path}")
            _SECRET_CACHE = data
    return _SECRET_CACHE


def create_token(username: str) -> tuple[str, str]:
    """签发 HS256 JWT,返回 (token, expires_at_iso)。"""
    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=settings.jwt_expire_hours)
    payload = {"sub": username, "iat": int(now.timestamp()), "exp": int(exp.timestamp())}
    token = pyjwt.encode(payload, _load_secret(), algorithm="HS256")
    expires_at = exp.isoformat(timespec="seconds").replace("+00:00", "Z")
    return token, expires_at


def decode_token(token: str) -> dict | None:
    """验签 + 过期校验;任何失败返回 None(不区分原因,不给攻击者信息)。"""
    try:
        payload = pyjwt.decode(token, _load_secret(), algorithms=["HS256"])
    except pyjwt.InvalidTokenError:
        return None
    if not isinstance(payload, dict) or not payload.get("sub"):
        return None
    return payload


def hash_password(password: str) -> str:
    pw = password.encode("utf-8")
    if len(pw) > 72:
        raise ValueError("password must be at most 72 bytes (bcrypt limit)")
    return bcrypt.hashpw(pw, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def dummy_verify(password: str) -> None:
    """对不存在的用户执行一次等代价 bcrypt 校验。"""
    try:
        bcrypt.checkpw(password.encode("utf-8"), _DUMMY_HASH)
    except (ValueError, TypeError):
        pass
