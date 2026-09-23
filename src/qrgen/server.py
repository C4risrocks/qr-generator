"""Web server for the QR generator: public UI, API, admin panel."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse, Response
from PIL import Image
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.middleware.sessions import SessionMiddleware

from qrgen import db, inputs, rate_limit
from qrgen.admin import router as admin_router
from qrgen.body_gate import DEFAULT_INFLIGHT_BUDGET, MAX_BODY_BYTES, BodyGate
from qrgen.core import (
    FORMATS,
    GRADIENT_INFO,
    MEDIA_TYPES,
    PALETTES,
    PRESETS,
    STYLE_INFO,
    InvalidInput,
    QRConfig,
    generate_previews,
    generate_qr,
    parse_options,
    validate_logo,
)
from qrgen.inputs import InputPayload
from qrgen.proxy import client_ip
from qrgen.webassets import WEB_DIR

logger = logging.getLogger("qrgen.server")

MAX_LOGO_BYTES = 5 * 1024 * 1024

SESSION_SECRET = os.environ.get("SESSION_SECRET") or secrets.token_urlsafe(32)
SESSION_HTTPS_ONLY = os.environ.get("SESSION_HTTPS_ONLY", "false").lower() == "true"


async def _apply_rate_limit(scope):
    """Record the request and return a 429 response when over quota.

    Returns None when the request may proceed (or when rate limiting is
    unavailable, which fails open after warning).
    """
    if not (
        scope["method"] == "POST"
        and rate_limit.endpoint_for_path(scope["path"]) is not None
    ):
        return None
    if not db.is_configured():
        rate_limit.warn_once(
            "no_db",
            "DATABASE_URL is not configured; rate limiting disabled",
        )
        return None
    try:
        request = Request(scope)
        ip = client_ip(request)
        user_agent = request.headers.get("user-agent", "")
        accept_language = request.headers.get("accept-language", "")
        allowed, retry_after, client_id = await rate_limit.check_and_record(
            ip, user_agent, accept_language, scope["path"]
        )
        if client_id is not None:
            scope.setdefault("state", {})["client_id"] = client_id
        if not allowed:
            return JSONResponse(
                {"detail": "rate limit exceeded; try again later"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
    except Exception:  # noqa: BLE001
        rate_limit.warn_once(
            "ratelimit", "rate limit check failed; allowing request"
        )
    return None


class RateLimitMiddleware:
    """Records the daily quota before the app runs.

    Sits inside the BodyGate: the gate only calls through once the body is
    complete and bounded, so the quota is consumed after size checks and an
    oversized body never reaches the counter.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rejection = await _apply_rate_limit(scope)
        if rejection is not None:
            await rejection(scope, receive, send)
            return
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_task = None
    if db.is_configured():
        cleanup_task = asyncio.create_task(rate_limit.cleanup_loop())
    yield
    if cleanup_task is not None:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
    await db.dispose()


@dataclass(frozen=True)
class FormRequest:
    """Everything the QR endpoints need, resolved once per request."""

    config: QRConfig
    payload: InputPayload
    client_id: int | None


# Module level on purpose: FastAPI evaluates annotations against module
# globals, so the dependency and its result type cannot live inside
# build_app's closure when __future__ annotations are on.
async def _read_logo(logo: UploadFile | None) -> Image.Image | None:
    if logo is None:
        return None
    raw = await logo.read(MAX_LOGO_BYTES + 1)
    if not raw:
        return None
    if len(raw) > MAX_LOGO_BYTES:
        raise HTTPException(status_code=400, detail="logo image exceeds the 5 MB limit")
    try:
        image = Image.open(io.BytesIO(raw))
        try:
            validate_logo(image)
        except InvalidInput as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        image.load()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid logo image") from exc
    return image


async def _resolve_input(data: str, file: UploadFile | None) -> InputPayload:
    if file is not None and file.filename:
        if data.strip():
            raise HTTPException(status_code=400, detail="provide text or a file, not both")
        raw = await file.read(inputs.MAX_INPUT_BYTES + 1)
        if len(raw) > inputs.MAX_INPUT_BYTES:
            raise HTTPException(status_code=400, detail="file content exceeds the maximum size")
        try:
            return inputs.parse_file(file.filename, raw)
        except InvalidInput as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return inputs.parse_text(data)
    except InvalidInput as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def form_request(request: Request) -> FormRequest:
    """The QR options seam: one dependency that turns any request body
    (web form today, other mappings tomorrow) into a validated QRConfig.

    Data/file resolution and logo decoding hide here; the declared
    QRConfig fields are the option contract via parse_options.
    """
    form = await request.form()
    file = form.get("file")
    logo = form.get("logo")
    payload = await _resolve_input(
        str(form.get("data", "")),
        file if isinstance(file, StarletteUploadFile) else None,
    )
    logo_image = await _read_logo(logo if isinstance(logo, StarletteUploadFile) else None)
    try:
        config = parse_options(form, data=payload.content, logo=logo_image)
    except InvalidInput as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FormRequest(
        config=config,
        payload=payload,
        client_id=getattr(request.state, "client_id", None),
    )


def build_app(
    *,
    max_body_bytes: int = MAX_BODY_BYTES,
    inflight_max_bytes: int = DEFAULT_INFLIGHT_BUDGET,
) -> FastAPI:
    """Construct the application.

    Body limits are constructor parameters: production wiring keeps the
    env-derived defaults, and tests construct apps with explicit limits
    instead of patching module state.
    """
    application = FastAPI(
        title="QR Generator",
        description="Personalizable QR codes from a link or text.",
        lifespan=lifespan,
    )

    application.add_middleware(
        SessionMiddleware,
        secret_key=SESSION_SECRET,
        same_site="lax",
        https_only=SESSION_HTTPS_ONLY,
        max_age=60 * 60 * 8,
    )
    # add_middleware wraps the existing stack, so the last one added is the
    # outermost. The body gate must run first: it streams and bounds the
    # body, then hands a complete body to the rate limiter, so size
    # rejections never consume quota.
    application.add_middleware(RateLimitMiddleware)
    application.add_middleware(
        BodyGate, max_bytes=max_body_bytes, inflight_budget=inflight_max_bytes
    )

    @application.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @application.get("/healthz")
    def healthz() -> JSONResponse:
        """Lightweight liveness probe; no QR work, no external dependencies."""
        return JSONResponse({"status": "ok"})

    def _app_version() -> str:
        try:
            from importlib.metadata import version

            return version("qrgen")
        except Exception:  # noqa: BLE001
            return "unknown"

    @application.get("/version")
    def version() -> JSONResponse:
        """Build identity: lets the deploy workflow verify the running commit."""
        return JSONResponse(
            {
                "version": _app_version(),
                "git_sha": os.environ.get("QRGEN_GIT_SHA", "unknown"),
                "build_date": os.environ.get("QRGEN_BUILD_DATE", "unknown"),
            }
        )

    @application.get("/readyz")
    async def readyz() -> JSONResponse:
        """Readiness probe: checks the database connection."""
        if not db.is_configured():
            return JSONResponse({"status": "db not configured"}, status_code=503)
        try:
            async with db.session_factory()() as session:
                await session.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            return JSONResponse({"status": "db unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    @application.get("/api/catalog")
    def catalog() -> JSONResponse:
        return JSONResponse(
            {
                "styles": [
                    {
                        "id": info.id,
                        "label": info.label,
                        "description": info.description,
                        "png": info.png,
                        "svg": info.svg,
                    }
                    for info in STYLE_INFO
                ],
                "gradient_types": GRADIENT_INFO,
                "ec_levels": [
                    {"id": level, "label": f"{level} · {pct}%"}
                    for level, pct in (("L", 7), ("M", 15), ("Q", 25), ("H", 30))
                ],
                "formats": list(FORMATS),
                "palettes": list(PALETTES),
                "presets": list(PRESETS),
            }
        )

    @application.post("/api/previews")
    async def previews(
        qr: Annotated[FormRequest, Depends(form_request)],
    ) -> JSONResponse:
        try:
            previews_out, selected, warnings = await run_in_threadpool(
                generate_previews, qr.config
            )
        except InvalidInput as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(
            {
                "styles": [
                    {
                        "id": preview.style,
                        "image": "data:image/png;base64,"
                        + base64.b64encode(preview.content).decode("ascii"),
                    }
                    for preview in previews_out
                ],
                "selected": {
                    "style": selected.style,
                    "image": "data:image/png;base64,"
                    + base64.b64encode(selected.content).decode("ascii"),
                },
                "warnings": list(warnings),
            }
        )

    @application.post("/api/generate")
    async def generate(
        background_tasks: BackgroundTasks,
        qr: Annotated[FormRequest, Depends(form_request)],
    ) -> Response:
        try:
            result = await run_in_threadpool(generate_qr, qr.config)
        except InvalidInput as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        background_tasks.add_task(inputs.persist_input, qr.client_id, qr.payload)

        headers = {"X-QR-Warnings": "; ".join(result.warnings)}
        return Response(
            content=result.content,
            media_type=MEDIA_TYPES[result.image_format],
            headers=headers,
        )

    application.include_router(admin_router)
    return application


app = build_app()


def main() -> int:
    return run()


def run(host: str | None = None, port: int | None = None) -> int:
    import uvicorn

    host = host or os.environ.get("HOST", "127.0.0.1")
    port = port or int(os.environ.get("PORT", "8000"))

    print(f"QR Generator web UI: http://{host}:{port}")
    # Single worker on purpose: the in-flight body budget is process-wide
    # (one BodyGate ledger) and the tmpfs budget is per container. Several
    # workers would each enforce their own cap over the same /tmp. Scale
    # out with separate container replicas instead.
    uvicorn.run(app, host=host, port=port, workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
