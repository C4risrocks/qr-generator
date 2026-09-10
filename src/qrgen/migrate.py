"""Alembic migrations run programmatically (shared by CLI and entrypoint)."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command


def _project_dir() -> Path:
    for candidate in (
        Path.cwd(),
        Path(__file__).resolve().parents[2],
        Path(os.environ.get("QRGEN_PROJECT_DIR", "/app")),
    ):
        if (candidate / "alembic.ini").is_file():
            return candidate
    raise RuntimeError("could not locate alembic.ini")


def _ping(url: str) -> bool:
    async def ping() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(ping())
        return True
    except Exception:  # noqa: BLE001
        return False


def run_migrations() -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("error: DATABASE_URL is not configured", file=sys.stderr)
        return 1

    wait_seconds = float(os.environ.get("QRGEN_MIGRATE_WAIT_SECONDS", "30"))
    deadline = time.monotonic() + wait_seconds
    while not _ping(url):
        if time.monotonic() >= deadline:
            print("error: database is not reachable", file=sys.stderr)
            return 1
        print("waiting for database...", file=sys.stderr)
        time.sleep(2)

    try:
        cfg = Config(str(_project_dir() / "alembic.ini"))
        cfg.set_main_option("script_location", str(_project_dir() / "alembic"))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
    except Exception as exc:  # noqa: BLE001
        print(f"error: migration failed: {exc}", file=sys.stderr)
        return 1
    print("database migrations applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_migrations())
