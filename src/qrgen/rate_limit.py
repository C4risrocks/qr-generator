"""Database-backed per-day rate limiting."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from qrgen import db
from qrgen.db import Client, QrInput, RateLimitWindow

logger = logging.getLogger("qrgen.rate_limit")

ENDPOINT_GENERATE = "generate"
ENDPOINT_PREVIEWS = "previews"

RATE_LIMIT_GENERATE_PER_DAY = int(os.environ.get("RATE_LIMIT_GENERATE_PER_DAY", "250"))
RATE_LIMIT_PREVIEWS_PER_DAY = int(os.environ.get("RATE_LIMIT_PREVIEWS_PER_DAY", "500"))
RATE_LIMIT_WINDOW_RETENTION_HOURS = int(os.environ.get("RATE_LIMIT_WINDOW_RETENTION_HOURS", "48"))
INPUT_RETENTION_DAYS = int(os.environ.get("INPUT_RETENTION_DAYS", "30"))
CLEANUP_INTERVAL_SECONDS = 15 * 60

ENDPOINT_LIMITS = {
    ENDPOINT_GENERATE: RATE_LIMIT_GENERATE_PER_DAY,
    ENDPOINT_PREVIEWS: RATE_LIMIT_PREVIEWS_PER_DAY,
}

_last_warned: dict[str, float] = {}


def endpoint_for_path(path: str) -> str | None:
    if path.endswith("/generate"):
        return ENDPOINT_GENERATE
    if path.endswith("/previews"):
        return ENDPOINT_PREVIEWS
    return None


def warn_once(key: str, message: str) -> None:
    now = time.monotonic()
    if now - _last_warned.get(key, 0) > 60:
        logger.warning(message)
        _last_warned[key] = now


def _seconds_until_day_end() -> int:
    now = datetime.now(timezone.utc)
    end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((end - now).total_seconds()))


def _upsert_insert(session: AsyncSession, values: dict):
    """Dialect-aware upsert for the daily counter."""
    dialect = session.bind.dialect.name if session.bind else "sqlite"
    if dialect == "postgresql":
        stmt = pg_insert(RateLimitWindow).values(**values)
        return stmt.on_conflict_do_update(
            index_elements=[
                RateLimitWindow.client_id,
                RateLimitWindow.endpoint,
                RateLimitWindow.window_date,
            ],
            set_={"request_count": RateLimitWindow.request_count + 1},
        )
    stmt = sqlite_insert(RateLimitWindow).values(**values)
    return stmt.on_conflict_do_update(
        index_elements=[
            RateLimitWindow.client_id,
            RateLimitWindow.endpoint,
            RateLimitWindow.window_date,
        ],
        set_={"request_count": RateLimitWindow.request_count + 1},
    )


async def get_or_create_client(
    session: AsyncSession,
    ip_address: str,
    user_agent: str,
    accept_language: str,
) -> Client:
    client = (
        await session.execute(
            select(Client).where(
                Client.ip_address == ip_address, Client.user_agent == user_agent
            )
        )
    ).scalar_one_or_none()
    if client is not None:
        client.last_seen_at = func.now()
        client.accept_language = accept_language[:255]
        return client
    client = Client(
        ip_address=ip_address,
        user_agent=user_agent,
        accept_language=accept_language[:255],
        country=_lookup_country(ip_address),
    )
    session.add(client)
    try:
        await session.flush()
    except Exception:  # noqa: BLE001
        await session.rollback()
        client = (
            await session.execute(
                select(Client).where(
                    Client.ip_address == ip_address, Client.user_agent == user_agent
                )
            )
        ).scalar_one()
    return client


def _lookup_country(ip_address: str) -> str | None:
    path = os.environ.get("GEOIP_DB_PATH")
    if not path or not os.path.isfile(path):
        return None
    try:
        import maxminddb

        with maxminddb.open_database(path) as reader:
            record = reader.get(ip_address)
        if record:
            return (record.get("country") or {}).get("iso_code")
    except Exception:  # noqa: BLE001
        warn_once("geoip", "GeoIP lookup failed; country stays empty")
    return None


async def check_and_record(
    ip_address: str, user_agent: str, accept_language: str, path: str
) -> tuple[bool, int, int | None]:
    """Increment the daily counter and return (allowed, retry_after, client_id).

    Raises on database failures; callers must treat failures as fail-open.
    """
    endpoint = endpoint_for_path(path)
    if endpoint is None:
        return True, 0, None

    async with db.session_factory()() as session:
        client = await get_or_create_client(session, ip_address, user_agent, accept_language)
        today = datetime.now(timezone.utc).date()
        await session.execute(
            _upsert_insert(
                session,
                {
                    "client_id": client.id,
                    "endpoint": endpoint,
                    "window_date": today,
                    "request_count": 1,
                },
            )
        )
        count = (
            await session.execute(
                select(RateLimitWindow.request_count).where(
                    RateLimitWindow.client_id == client.id,
                    RateLimitWindow.endpoint == endpoint,
                    RateLimitWindow.window_date == today,
                )
            )
        ).scalar_one()
        await session.commit()

        if count > ENDPOINT_LIMITS[endpoint]:
            return False, _seconds_until_day_end(), client.id
        return True, 0, client.id


async def cleanup_once() -> None:
    """Delete expired rate-limit windows and old inputs."""
    async with db.session_factory()() as session:
        now = datetime.now(timezone.utc)
        # Compute the cutoff from a datetime so hourly retentions are exact;
        # subtracting a timedelta with hours from a date silently truncates
        # to whole days.
        window_cutoff = (now - timedelta(hours=RATE_LIMIT_WINDOW_RETENTION_HOURS)).date()
        await session.execute(
            delete(RateLimitWindow).where(RateLimitWindow.window_date < window_cutoff)
        )
        input_cutoff = (now - timedelta(days=INPUT_RETENTION_DAYS)).date()
        await session.execute(
            delete(QrInput).where(func.date(QrInput.created_at) < input_cutoff)
        )
        await session.commit()


async def cleanup_loop() -> None:
    while True:
        try:
            await cleanup_once()
        except Exception:  # noqa: BLE001
            warn_once("cleanup", "database cleanup failed")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
