from __future__ import annotations

import sqlite3

from alembic.config import Config

from alembic import command


def _config(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


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
