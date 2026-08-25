"""认证主体模型(骨架固定,认证模块实现 deps 时使用)。"""
from __future__ import annotations

from dataclasses import dataclass, field

TYPE_USER = "user"
TYPE_API_KEY = "api_key"
TYPE_SYSTEM = "system"

ALL_SCOPES = ("view", "start", "stop", "delete", "admin")


@dataclass
class Principal:
    type: str
    name: str
    scopes: set[str] = field(default_factory=set)

    def has_scope(self, scope: str) -> bool:
        if self.type == TYPE_USER:
            return True  # 登录用户=管理员,全量
        return "admin" in self.scopes or scope in self.scopes
