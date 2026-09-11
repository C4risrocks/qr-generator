from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from qrgen import db, rate_limit
from qrgen.db import Client, QrInput, RateLimitWindow

IP = "1.2.3.4"
UA = "test-agent"


async def test_check_and_record_increments() -> None:
    allowed, _retry_after, client_id = await rate_limit.check_and_record(
        IP, UA, "es-MX", "/api/generate"
    )
    assert allowed is True
    assert client_id is not None

    async with db.session_factory()() as session:
        window = (
            await session.execute(select(RateLimitWindow).where(RateLimitWindow.client_id == client_id))
        ).scalar_one()
        assert window.endpoint == "generate"
        assert window.request_count == 1

    allowed, _, _ = await rate_limit.check_and_record(IP, UA, "es-MX", "/api/generate")
    assert allowed is True
    async with db.session_factory()() as session:
        window = (
            await session.execute(select(RateLimitWindow).where(RateLimitWindow.client_id == client_id))
        ).scalar_one()
        assert window.request_count == 2


async def test_client_is_reused_for_same_ip_ua() -> None:
    await rate_limit.check_and_record(IP, UA, "es", "/api/previews")
    await rate_limit.check_and_record(IP, UA, "en", "/api/previews")
    async with db.session_factory()() as session:
        clients = (await session.execute(select(Client))).scalars().all()
        assert len(clients) == 1
        assert clients[0].accept_language == "en"


async def test_blocked_over_limit(monkeypatch) -> None:
    monkeypatch.setattr(rate_limit, "ENDPOINT_LIMITS", {"generate": 2, "previews": 3})
    for _ in range(2):
        allowed, _, _ = await rate_limit.check_and_record(IP, UA, "", "/api/generate")
        assert allowed is True
    allowed, retry_after, _ = await rate_limit.check_and_record(IP, UA, "", "/api/generate")
    assert allowed is False
    assert retry_after > 0


async def test_endpoints_have_independent_limits(monkeypatch) -> None:
    monkeypatch.setattr(rate_limit, "ENDPOINT_LIMITS", {"generate": 1, "previews": 3})
    allowed, _, _ = await rate_limit.check_and_record(IP, UA, "", "/api/generate")
    assert allowed is True
    allowed, _, _ = await rate_limit.check_and_record(IP, UA, "", "/api/generate")
    assert allowed is False
    allowed, _, _ = await rate_limit.check_and_record(IP, UA, "", "/api/previews")
    assert allowed is True


async def test_cleanup_removes_expired_rows(monkeypatch) -> None:
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_WINDOW_RETENTION_HOURS", 48)
    monkeypatch.setattr(rate_limit, "INPUT_RETENTION_DAYS", 30)
    now = datetime.now(timezone.utc)
    today = now.date()
    async with db.session_factory()() as session:
        client = Client(ip_address=IP, user_agent=UA)
        session.add(client)
        await session.flush()
        session.add(
            RateLimitWindow(
                client_id=client.id,
                endpoint="generate",
                window_date=today - timedelta(days=5),
                request_count=10,
                created_at=now - timedelta(days=5),
            )
        )
        session.add(
            QrInput(
                client_id=client.id,
                filename="old.txt",
                content="x",
                content_type="text/plain",
                content_hash="a" * 64,
                created_at=datetime.now(timezone.utc) - timedelta(days=40),
            )
        )
        await session.commit()

    await rate_limit.cleanup_once()

    async with db.session_factory()() as session:
        windows = (await session.execute(select(RateLimitWindow))).scalars().all()
        inputs = (await session.execute(select(QrInput))).scalars().all()
        assert windows == []
        assert inputs == []


async def test_cleanup_keeps_fresh_rows() -> None:
    today = datetime.now(timezone.utc).date()
    async with db.session_factory()() as session:
        client = Client(ip_address=IP, user_agent=UA)
        session.add(client)
        await session.flush()
        session.add(
            RateLimitWindow(client_id=client.id, endpoint="generate", window_date=today, request_count=1)
        )
        session.add(
            QrInput(
                client_id=client.id,
                filename=None,
                content="fresh",
                content_type="text",
                content_hash="b" * 64,
            )
        )
        await session.commit()

    await rate_limit.cleanup_once()

    async with db.session_factory()() as session:
        windows = (await session.execute(select(RateLimitWindow))).scalars().all()
        inputs = (await session.execute(select(QrInput))).scalars().all()
        assert len(windows) == 1
        assert len(inputs) == 1


async def test_cleanup_retention_hours_are_exact(monkeypatch) -> None:
    """Hourly window retention compares real timestamps, not whole days.

    With "now" frozen and a 36 h retention, a window created 30 h ago is
    kept while one created 40 h ago is deleted, even though both fall on
    recent calendar days.
    """
    import qrgen.rate_limit as rl

    fixed_now = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(rl, "datetime", FrozenDateTime)
    monkeypatch.setattr(rl, "RATE_LIMIT_WINDOW_RETENTION_HOURS", 36)
    monkeypatch.setattr(rl, "INPUT_RETENTION_DAYS", 30)
    today = fixed_now.date()
    async with db.session_factory()() as session:
        client = Client(ip_address=IP, user_agent=UA)
        session.add(client)
        await session.flush()
        session.add(
            RateLimitWindow(
                client_id=client.id,
                endpoint="generate",
                window_date=today - timedelta(days=1),
                request_count=1,
                created_at=fixed_now - timedelta(hours=30),
            )
        )
        session.add(
            RateLimitWindow(
                client_id=client.id,
                endpoint="previews",
                window_date=today - timedelta(days=2),
                request_count=1,
                created_at=fixed_now - timedelta(hours=40),
            )
        )
        await session.commit()

    await rl.cleanup_once()

    async with db.session_factory()() as session:
        windows = (await session.execute(select(RateLimitWindow))).scalars().all()
        assert [w.endpoint for w in windows] == ["generate"]


def test_endpoint_for_path() -> None:
    assert rate_limit.endpoint_for_path("/api/generate") == "generate"
    assert rate_limit.endpoint_for_path("/api/previews") == "previews"
    assert rate_limit.endpoint_for_path("/") is None
