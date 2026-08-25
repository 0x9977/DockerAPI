"""测试夹具。环境变量必须在导入 app 前设置。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="dapi-test-"))
os.environ["DATA_DIR"] = str(_tmp)
os.environ["STACKS_DIR"] = str(_tmp / "stacks")
os.environ["LOG_LEVEL"] = "WARNING"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _init_db():
    from app.db import init_db, init_engine

    init_engine(_tmp / "test.db")
    init_db()
    yield


@pytest.fixture()
def db():
    from app.db import session

    s = session()
    yield s
    s.rollback()
    s.close()


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def fake_docker(monkeypatch):
    """可扩展的假 docker 客户端: 各服务测试自行 monkeypatch docker_client.get_client。

    用法(服务测试内):
        from app.services import docker_client
        monkeypatch.setattr(docker_client, "get_client", lambda: my_fake)
    """
    from app.services import docker_client

    class FakeDocker:
        def __init__(self):
            self.calls: list[str] = []

        def containers(self):  # pragma: no cover
            raise NotImplementedError("extend me")

    fake = FakeDocker()
    monkeypatch.setattr(docker_client, "get_client", lambda: fake)
    return fake
