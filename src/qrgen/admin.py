"""Admin panel: authentication and read-only monitoring views."""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import case, func, select

from qrgen import db
from qrgen.db import Client, QrInput, RateLimitWindow
from qrgen.passwords import admin_configured, verify_password
from qrgen.proxy import client_ip
from qrgen.rate_limit import (
    ENDPOINT_LIMITS,
    INPUT_RETENTION_DAYS,
    RATE_LIMIT_WINDOW_RETENTION_HOURS,
)
from qrgen.webassets import WEB_DIR

router = APIRouter(prefix="/admin")

LOGIN_LIMIT_PER_MINUTE = 10
_login_attempts: dict[str, deque[float]] = defaultdict(deque)


def _is_authed(request: Request) -> bool:
    return bool(request.session.get("admin"))


def _require_admin(request: Request) -> None:
    if not _is_authed(request):
        raise HTTPException(status_code=401, detail="admin authentication required")


def _login_allowed(ip: str) -> bool:
    now = time.monotonic()
    attempts = _login_attempts[ip]
    while attempts and now - attempts[0] > 60:
        attempts.popleft()
    if len(attempts) >= LOGIN_LIMIT_PER_MINUTE:
        return False
    attempts.append(now)
    return True


def _db_or_503() -> None:
    if not db.is_configured():
        raise HTTPException(status_code=503, detail="database is not configured")


@router.get("", response_model=None)
def admin_index(request: Request):
    if not _is_authed(request):
        return RedirectResponse("/admin/login", status_code=303)
    return FileResponse(WEB_DIR / "admin.html")


@router.get("/login", response_model=None)
def admin_login_page(request: Request):
    if _is_authed(request):
        return RedirectResponse("/admin", status_code=303)
    return FileResponse(WEB_DIR / "admin-login.html")


@router.post("/login")
async def admin_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
) -> RedirectResponse:
    ip = client_ip(request)
    if not _login_allowed(ip):
        raise HTTPException(status_code=429, detail="too many login attempts; try again later")
    if not admin_configured():
        raise HTTPException(status_code=503, detail="admin account is not configured")
    if username == os.environ.get("ADMIN_USERNAME") and verify_password(
        password, os.environ.get("ADMIN_PASSWORD_HASH", "")
    ):
        request.session["admin"] = True
        return RedirectResponse("/admin", status_code=303)
    raise HTTPException(status_code=401, detail="invalid credentials")


@router.post("/logout")
def admin_logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=303)


@router.get("/api/summary")
async def api_summary(request: Request) -> JSONResponse:
    _require_admin(request)
    _db_or_503()
    today = datetime.now(timezone.utc).date()
    async with db.session_factory()() as session:
        rows = (
            await session.execute(
                select(
                    RateLimitWindow.endpoint,
                    func.sum(RateLimitWindow.request_count).label("requests"),
                )
                .where(RateLimitWindow.window_date == today)
                .group_by(RateLimitWindow.endpoint)
            )
        ).all()
        requests_today = {row.endpoint: int(row.requests) for row in rows}
        unique_clients = (
            await session.execute(
                select(func.count(func.distinct(RateLimitWindow.client_id))).select_from(
                    RateLimitWindow
                )
                .where(RateLimitWindow.window_date == today)
            )
        ).scalar_one()
        inputs_today = (
            await session.execute(
                select(func.count()).select_from(QrInput).where(
                    func.date(QrInput.created_at) == today
                )
            )
        ).scalar_one()
        clients_total = (
            await session.execute(select(func.count()).select_from(Client))
        ).scalar_one()
        return JSONResponse(
            {
                "date": today.isoformat(),
                "requests_today": requests_today,
                "limits": ENDPOINT_LIMITS,
                "unique_clients_today": unique_clients,
                "inputs_today": inputs_today,
                "clients_total": clients_total,
            }
        )


@router.get("/api/usage")
async def api_usage(request: Request, days: int = 14) -> JSONResponse:
    _require_admin(request)
    _db_or_503()
    days = max(1, min(days, 90))
    since = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
    async with db.session_factory()() as session:
        rows = (
            await session.execute(
                select(
                    RateLimitWindow.window_date,
                    RateLimitWindow.endpoint,
                    func.sum(RateLimitWindow.request_count).label("requests"),
                    func.count(func.distinct(RateLimitWindow.client_id)).label("clients"),
                )
                .where(RateLimitWindow.window_date >= since)
                .group_by(RateLimitWindow.window_date, RateLimitWindow.endpoint)
                .order_by(RateLimitWindow.window_date)
            )
        ).all()
        series: dict[str, dict] = {}
        for row in rows:
            day = row.window_date.isoformat()
            entry = series.setdefault(
                day, {"date": day, "generate": 0, "previews": 0, "clients": 0}
            )
            entry[row.endpoint] = int(row.requests or 0)
            entry["clients"] = max(entry["clients"], int(row.clients or 0))
        return JSONResponse({"series": list(series.values())})


@router.get("/api/clients")
async def api_clients(request: Request, limit: int = 50) -> JSONResponse:
    _require_admin(request)
    _db_or_503()
    limit = max(1, min(limit, 500))
    today = datetime.now(timezone.utc).date()
    async with db.session_factory()() as session:
        rows = (
            await session.execute(
                select(
                    Client.id,
                    Client.ip_address,
                    Client.user_agent,
                    Client.accept_language,
                    Client.country,
                    Client.first_seen_at,
                    Client.last_seen_at,
                    func.sum(
                        case(
                            (RateLimitWindow.endpoint == "generate", RateLimitWindow.request_count),
                            else_=0,
                        )
                    ).label("generate_today"),
                    func.sum(
                        case(
                            (RateLimitWindow.endpoint == "previews", RateLimitWindow.request_count),
                            else_=0,
                        )
                    ).label("previews_today"),
                )
                .outerjoin(
                    RateLimitWindow,
                    (RateLimitWindow.client_id == Client.id)
                    & (RateLimitWindow.window_date == today),
                )
                .group_by(Client.id)
                .order_by(Client.last_seen_at.desc())
                .limit(limit)
            )
        ).all()
        clients = []
        for row in rows:
            generate_today = int(row.generate_today or 0)
            previews_today = int(row.previews_today or 0)
            clients.append(
                {
                    "id": row.id,
                    "ip": row.ip_address,
                    "user_agent": (row.user_agent or "")[:200],
                    "accept_language": row.accept_language,
                    "country": row.country,
                    "first_seen": row.first_seen_at.isoformat() if row.first_seen_at else None,
                    "last_seen": row.last_seen_at.isoformat() if row.last_seen_at else None,
                    "generate_today": generate_today,
                    "previews_today": previews_today,
                    "usage_pct": round(
                        max(
                            generate_today / ENDPOINT_LIMITS["generate"],
                            previews_today / ENDPOINT_LIMITS["previews"],
                        )
                        * 100
                    ),
                }
            )
        return JSONResponse({"clients": clients})


@router.get("/api/inputs")
async def api_inputs(request: Request, limit: int = 50) -> JSONResponse:
    _require_admin(request)
    _db_or_503()
    limit = max(1, min(limit, 500))
    async with db.session_factory()() as session:
        rows = (
            (await session.execute(select(QrInput).order_by(QrInput.id.desc()).limit(limit)))
            .scalars()
            .all()
        )
        inputs = []
        for item in rows:
            content = item.content if len(item.content) <= 200 else item.content[:200] + "\u2026"
            inputs.append(
                {
                    "id": item.id,
                    "client_id": item.client_id,
                    "filename": item.filename,
                    "content": content,
                    "content_type": item.content_type,
                    "content_hash": item.content_hash,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                }
            )
        return JSONResponse({"inputs": inputs})


@router.get("/api/config")
async def api_config(request: Request) -> JSONResponse:
    _require_admin(request)
    return JSONResponse(
        {
            "rate_limits": ENDPOINT_LIMITS,
            "rate_limit_window_retention_hours": RATE_LIMIT_WINDOW_RETENTION_HOURS,
            "input_retention_days": INPUT_RETENTION_DAYS,
            "admin_configured": admin_configured(),
        }
    )
