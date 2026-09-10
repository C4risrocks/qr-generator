"""Database engine, session factory and ORM models."""

from __future__ import annotations

import hashlib
import os
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool


class Base(DeclarativeBase):
    pass


# BigInteger on PostgreSQL (BIGSERIAL) and plain INTEGER autoincrement on
# SQLite, where BIGINT primary keys cannot alias the rowid.
ID_TYPE = BigInteger().with_variant(Integer, "sqlite")


class Client(Base):
    """A browser client identified by IP + User-Agent (persistent)."""

    __tablename__ = "clients"
    __table_args__ = (
        UniqueConstraint("ip_address", "user_agent", name="uq_clients_ip_ua"),
    )

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    ip_address: Mapped[str] = mapped_column(String(64))
    user_agent: Mapped[str] = mapped_column(Text, default="")
    accept_language: Mapped[str] = mapped_column(String(255), default="")
    country: Mapped[str | None] = mapped_column(String(4), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RateLimitWindow(Base):
    """Per-day request counter for one endpoint and one client."""

    __tablename__ = "rate_limit_windows"
    __table_args__ = (
        UniqueConstraint(
            "client_id", "endpoint", "window_date", name="uq_rate_window"
        ),
    )

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE")
    )
    endpoint: Mapped[str] = mapped_column(String(16))
    window_date: Mapped[date] = mapped_column(Date)
    request_count: Mapped[int] = mapped_column(Integer, default=0)


class QrInput(Base):
    """Text or file content used to generate a QR code (monitoring only)."""

    __tablename__ = "qr_inputs"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(32), default="text")
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


_ENGINE: AsyncEngine | None = None
_SESSIONMAKER: async_sessionmaker[AsyncSession] | None = None


def is_configured() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


def get_engine() -> AsyncEngine:
    global _ENGINE
    if _ENGINE is None:
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            raise RuntimeError("DATABASE_URL is not configured")
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            # NullPool keeps connections bound to the event loop they were
            # created in, which makes SQLite usable across test loops.
            kwargs["poolclass"] = NullPool
        _ENGINE = create_async_engine(url, **kwargs)
    return _ENGINE


def session_factory() -> async_sessionmaker[AsyncSession]:
    global _SESSIONMAKER
    if _SESSIONMAKER is None:
        _SESSIONMAKER = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _SESSIONMAKER


def reset() -> None:
    """Drop cached engine/session state (used by tests)."""
    global _ENGINE, _SESSIONMAKER
    _ENGINE = None
    _SESSIONMAKER = None


async def dispose() -> None:
    """Close the engine and drop cached state (used by tests)."""
    global _ENGINE, _SESSIONMAKER
    if _ENGINE is not None:
        await _ENGINE.dispose()
    _ENGINE = None
    _SESSIONMAKER = None


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
