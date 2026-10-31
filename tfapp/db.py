"""DB 연결.

질의마다 연결을 새로 만들면 느리므로(공고 4.2절), 연결 풀을 프로세스에 한 번만 만들고
모든 사용자 세션이 함께 다시 쓴다.
"""
from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_secret

_pool: ConnectionPool | None = None
_pool_url: str | None = None
_lock = threading.Lock()


class DBConfigError(RuntimeError):
    pass


def _make_pool(url: str) -> ConnectionPool:
    return ConnectionPool(
        conninfo=url,
        min_size=1,
        max_size=5,
        timeout=20,
        max_idle=300,
        # Supabase pooler(PgBouncer/Supavisor)와 함께 쓰려면 prepared statement 를 끔
        kwargs={"prepare_threshold": None, "row_factory": dict_row, "connect_timeout": 15},
        check=ConnectionPool.check_connection,   # 끊긴 연결은 버리고 새로 연결
        open=True,
        name="tf_pool",
    )


def get_pool() -> ConnectionPool:
    global _pool, _pool_url
    url = get_secret("DATABASE_URL")
    if not url:
        raise DBConfigError("DATABASE_URL 이 설정되지 않음 (.env 또는 Streamlit Secrets)")
    if _pool is not None and _pool_url == url:
        return _pool
    with _lock:
        if _pool is None or _pool_url != url:
            if _pool is not None:
                _pool.close()
            _pool = _make_pool(url)
            _pool_url = url
    return _pool


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """풀에서 연결을 빌려 준다. 블록이 오류 없이 끝나면 commit, 오류면 rollback."""
    with get_pool().connection() as conn:
        yield conn


def close_pool() -> None:
    global _pool, _pool_url
    with _lock:
        if _pool is not None:
            _pool.close()
        _pool = None
        _pool_url = None


_URL_RE = re.compile(r"postgres(?:ql)?://\S+")
_PW_RE = re.compile(r"(password\s*=\s*)\S+", re.IGNORECASE)


def safe_error(exc: BaseException) -> str:
    """오류 메시지에서 DB 주소와 비밀번호를 가린다 (공고 6.3절 8번)."""
    text = f"{type(exc).__name__}: {exc}"
    text = _URL_RE.sub("[DB 주소 가림]", text)
    text = _PW_RE.sub(r"\1[가림]", text)
    secret = get_secret("DATABASE_URL")
    if secret:
        text = text.replace(secret, "[DB 주소 가림]")
    return text.splitlines()[0][:300]
