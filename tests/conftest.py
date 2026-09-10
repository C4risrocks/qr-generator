from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from qrgen import db
from qrgen.db import Client, QrInput, RateLimitWindow
from qrgen.passwords import hash_password


def _prepare() -> None:
    url = os.environ["DATABASE_URL"]
    # NullPool: connections are created in the loop that uses them, which
    # lets the same engine serve pytest loops and the TestClient loop.
    engine = create_async_engine(url, poolclass=NullPool)
    db._ENGINE = engine
    db._SESSIONMAKER = async_sessionmaker(engine, expire_on_commit=False)

    async def setup() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(db.Base.metadata.create_all)
            # PostgreSQL is shared across tests: clear rows between tests.
            # SQLite uses a fresh file per test, so nothing to clear.
            if url.startswith("postgresql"):
                await connection.execute(delete(QrInput))
                await connection.execute(delete(RateLimitWindow))
                await connection.execute(delete(Client))

    asyncio.run(setup())


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    """Point every test at a database and provide an admin account.

    Uses PostgreSQL when DATABASE_URL is already set (CI) and a file-backed
    SQLite database otherwise.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
        monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password("secret"))
    _prepare()
    yield
    asyncio.run(db.dispose())
