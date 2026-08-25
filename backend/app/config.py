"""全局配置。全部来自环境变量,见 docs/deployment.md。

默认值面向本地开发(Windows/裸机);容器内由 Dockerfile 注入
DATA_DIR=/data、STACKS_DIR=/opt/stacks 覆盖。
"""
from __future__ import annotations

import os
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        self.docker_host: str = os.getenv("DOCKER_HOST", "unix:///var/run/docker.sock")
        self.data_dir: Path = Path(os.getenv("DATA_DIR", "./data"))
        self.stacks_dir: Path = Path(os.getenv("STACKS_DIR", "./stacks"))
        self.docker_timeout: int = int(os.getenv("DOCKER_TIMEOUT", "30"))
        self.compose_job_timeout: int = int(os.getenv("COMPOSE_JOB_TIMEOUT", "1800"))
        self.audit_retention_days: int = int(os.getenv("AUDIT_RETENTION_DAYS", "90"))
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO")
        self.jwt_expire_hours: int = int(os.getenv("JWT_EXPIRE_HOURS", "24"))

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "logs").mkdir(parents=True, exist_ok=True)
        self.stacks_dir.mkdir(parents=True, exist_ok=True)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "dockerapi.db"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / "secret.key"


settings = Settings()
