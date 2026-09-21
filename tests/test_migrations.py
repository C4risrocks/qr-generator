from __future__ import annotations

import asyncio
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command


def _config(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _pg_base_url() -> str | None:
    """DATABASE_URL when it points at PostgreSQL (CI provides one); None on
    SQLite-only environments, where the PostgreSQL-only tests must skip."""
    url = os.environ.get("DATABASE_URL", "")
    return url if url.startswith("postgresql") else None


def _swap_db(url: str, dbname: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", parts.query, ""))


def _plain_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def test_migrations_upgrade_head_sqlite(tmp_path, monkeypatch) -> None:
    """Alembic migrations must apply cleanly on SQLite (0001 + 0002)."""
    from qrgen import migrate

    db_path = tmp_path / "mig.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("QRGEN_MIGRATE_WAIT_SECONDS", "5")
    assert migrate.run_migrations() == 0

    con = sqlite3.connect(db_path)
    try:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"clients", "rate_limit_windows", "qr_inputs"} <= tables
        window_cols = {row[1] for row in con.execute("PRAGMA table_info(rate_limit_windows)")}
        assert "created_at" in window_cols
        version = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert version == "0002"
    finally:
        con.close()


def test_migrations_downgrade_upgrade_roundtrip_sqlite(tmp_path) -> None:
    db_path = tmp_path / "mig2.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    cfg = _config(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    con = sqlite3.connect(db_path)
    try:
        version = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert version == "0002"
    finally:
        con.close()


def test_migrated_sqlite_schema_autoincrements(tmp_path) -> None:
    """Rows inserted into the migrated (not create_all) SQLite schema must
    get working autoincrement primary keys on every table."""
    db_path = tmp_path / "mig3.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    command.upgrade(_config(url), "head")

    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(
            "INSERT INTO clients (ip_address, user_agent, accept_language)"
            " VALUES (?, ?, ?)",
            ("1.2.3.4", "agent", "es"),
        )
        client_id = cur.lastrowid
        assert client_id is not None and client_id > 0
        cur = con.execute(
            "INSERT INTO rate_limit_windows"
            " (client_id, endpoint, window_date, request_count)"
            " VALUES (?, ?, ?, ?)",
            (client_id, "generate", "2026-09-10", 1),
        )
        assert cur.lastrowid is not None and cur.lastrowid > 0
        cur = con.execute(
            "INSERT INTO qr_inputs (client_id, content, content_type, content_hash)"
            " VALUES (?, ?, ?, ?)",
            (client_id, "hola", "text", "x" * 64),
        )
        assert cur.lastrowid is not None and cur.lastrowid > 0
        con.commit()
        # A second client proves the sequence advances (no rowid reuse).
        cur = con.execute(
            "INSERT INTO clients (ip_address, user_agent, accept_language)"
            " VALUES (?, ?, ?)",
            ("5.6.7.8", "agent", "es"),
        )
        assert cur.lastrowid == client_id + 1
    finally:
        con.close()


def test_0002_backdates_windows_to_utc_midnight_sqlite(tmp_path) -> None:
    """Rows predating 0002 get created_at at the UTC midnight of their
    window day, and the rebuilt table enforces NOT NULL + DEFAULT."""
    db_path = tmp_path / "mig4.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    cfg = _config(url)
    command.upgrade(cfg, "0001")
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT INTO clients (ip_address, user_agent, accept_language)"
            " VALUES (?, ?, ?)",
            ("1.2.3.4", "agent", "es"),
        )
        con.execute(
            "INSERT INTO rate_limit_windows"
            " (client_id, endpoint, window_date, request_count)"
            " VALUES (?, ?, ?, ?)",
            (1, "generate", "2026-08-01", 7),
        )
        con.commit()
    finally:
        con.close()
    command.upgrade(cfg, "head")

    con = sqlite3.connect(db_path)
    try:
        created_at = con.execute(
            "SELECT created_at FROM rate_limit_windows"
        ).fetchone()[0]
        assert created_at == "2026-08-01 00:00:00"

        cols = {row[1]: row for row in con.execute("PRAGMA table_info(rate_limit_windows)")}
        # row format: cid, name, type, notnull, dflt_value, pk
        assert cols["created_at"][3] == 1, "created_at must be NOT NULL"
        assert "CURRENT_TIMESTAMP" in (cols["created_at"][4] or "").upper()

        indexes = {row[1] for row in con.execute("PRAGMA index_list(rate_limit_windows)")}
        assert "ix_rate_limit_windows_created_at" in indexes
        assert "ix_rate_limit_windows_window_date" in indexes

        # Raw SQL inserts (not via the ORM default) must get a timestamp.
        con.execute(
            "INSERT INTO rate_limit_windows"
            " (client_id, endpoint, window_date, request_count)"
            " VALUES (?, ?, ?, ?)",
            (1, "previews", "2026-09-10", 1),
        )
        inserted = con.execute(
            "SELECT created_at FROM rate_limit_windows WHERE endpoint = 'previews'"
        ).fetchone()[0]
        assert inserted is not None
    finally:
        con.close()


@pytest.mark.skipif(
    _pg_base_url() is None, reason="requires a PostgreSQL DATABASE_URL (CI)"
)
def test_0002_backdates_windows_on_postgresql(tmp_path, monkeypatch) -> None:
    """The production path of 0002 (PostgreSQL): in-place backfill to the UTC
    midnight of the window day, then server default + NOT NULL via ALTER.
    Runs against a dedicated database so the shared CI database is untouched."""
    import asyncpg

    base = _pg_base_url()
    dbname = f"qrgen_mig_{uuid.uuid4().hex[:10]}"
    cfg = _config(_swap_db(base, dbname))
    # Migration scripts run via env.py with this URL; keep env consistent too.
    monkeypatch.setenv("DATABASE_URL", _swap_db(base, dbname))

    async def create_db() -> None:
        admin = await asyncpg.connect(dsn=_plain_dsn(_swap_db(base, "postgres")))
        try:
            await admin.execute(f'CREATE DATABASE "{dbname}"')
        finally:
            await admin.close()

    async def seed() -> None:
        engine = create_async_engine(cfg.get_main_option("sqlalchemy.url"))
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO clients (ip_address, user_agent, accept_language)"
                        " VALUES ('1.2.3.4', 'agent', 'es')"
                    )
                )
                await conn.execute(
                    text(
                        "INSERT INTO rate_limit_windows"
                        " (client_id, endpoint, window_date, request_count)"
                        " VALUES (1, 'generate', DATE '2026-08-01', 7)"
                    )
                )
        finally:
            await engine.dispose()

    async def verify() -> None:
        engine = create_async_engine(cfg.get_main_option("sqlalchemy.url"))
        try:
            async with engine.begin() as conn:
                version = (
                    await conn.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one()
                assert version == "0002"
                created = (
                    await conn.execute(
                        text("SELECT created_at FROM rate_limit_windows")
                    )
                ).scalar_one()
                assert created is not None
                assert created.astimezone(timezone.utc) == datetime(
                    2026, 8, 1, tzinfo=timezone.utc
                )
                # A raw insert without created_at must still get a timestamp.
                await conn.execute(
                    text(
                        "INSERT INTO rate_limit_windows"
                        " (client_id, endpoint, window_date, request_count)"
                        " VALUES (1, 'previews', DATE '2026-09-10', 1)"
                    )
                )
                fresh = (
                    await conn.execute(
                        text(
                            "SELECT created_at FROM rate_limit_windows"
                            " WHERE endpoint = 'previews'"
                        )
                    )
                ).scalar_one()
                assert fresh is not None
                indexes = set(
                    (
                        await conn.execute(
                            text(
                                "SELECT indexname FROM pg_indexes"
                                " WHERE tablename = 'rate_limit_windows'"
                            )
                        )
                    ).scalars()
                )
                assert {
                    "ix_rate_limit_windows_created_at",
                    "ix_rate_limit_windows_window_date",
                } <= indexes
                nullable = (
                    await conn.execute(
                        text(
                            "SELECT is_nullable FROM information_schema.columns"
                            " WHERE table_name = 'rate_limit_windows'"
                            " AND column_name = 'created_at'"
                        )
                    )
                ).scalar_one()
                assert nullable == "NO"
        finally:
            await engine.dispose()

    async def drop_db() -> None:
        admin = await asyncpg.connect(dsn=_plain_dsn(_swap_db(base, "postgres")))
        try:
            await admin.execute(f'DROP DATABASE "{dbname}"')
        finally:
            await admin.close()

    asyncio.run(create_db())
    try:
        # Alembic must run outside a running event loop (env.py asyncio.run).
        command.upgrade(cfg, "0001")
        asyncio.run(seed())
        command.upgrade(cfg, "head")
        asyncio.run(verify())
    finally:
        asyncio.run(drop_db())
