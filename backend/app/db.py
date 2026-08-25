from __future__ import annotations

from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker | None = None


def init_engine(db_path: Path | str) -> None:
    global _engine, _SessionLocal
    url = f"sqlite:///{db_path}"
    _engine = create_engine(url, connect_args={"check_same_thread": False})
    # SQLite WAL: 审计写入与读并发更平滑
    @event.listens_for(_engine, "connect")
    def _set_wal(dbapi_conn, _):  # pragma: no cover
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)


def get_engine():
    if _engine is None:
        init_engine(settings.db_path)
    return _engine


def init_db() -> None:
    from app import models  # noqa: F401 确保表已注册

    Base.metadata.create_all(get_engine())


def session() -> Session:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖:请求级 session,自动提交/回滚。"""
    db = session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
