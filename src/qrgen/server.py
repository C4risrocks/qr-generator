"""Web server for the QR generator: public UI, API, admin panel."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import secrets
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse, Response
from PIL import Image
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from qrgen import db, inputs, rate_limit
from qrgen.admin import router as admin_router
from qrgen.core import (
    DEFAULT_EC,
    FORMATS,
    GRADIENT_INFO,
    MEDIA_TYPES,
    PALETTES,
    PNG,
    PRESETS,
    STYLE_INFO,
    InvalidInput,
    QRConfig,
    generate_previews,
    generate_qr,
    validate_logo,
)
from qrgen.inputs import InputPayload
from qrgen.webassets import WEB_DIR

logger = logging.getLogger("qrgen.server")

MAX_LOGO_BYTES = 5 * 1024 * 1024
MAX_BODY_BYTES = 6 * 1024 * 1024

SESSION_SECRET = os.environ.get("SESSION_SECRET") or secrets.token_urlsafe(32)
SESSION_HTTPS_ONLY = os.environ.get("SESSION_HTTPS_ONLY", "false").lower() == "true"


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


app = FastAPI(
    title="QR Generator",
    description="Personalizable QR codes from a link or text.",
    lifespan=lifespan,
)


@app.middleware("http")
async def guard_request(request: Request, call_next):
    """Rate limit CPU-heavy endpoints with the daily DB counter and cap bodies."""
    if request.method == "POST" and rate_limit.endpoint_for_path(request.url.path) is not None:
        if db.is_configured():
            try:
                ip = request.client.host if request.client else "unknown"
                user_agent = request.headers.get("user-agent", "")
                accept_language = request.headers.get("accept-language", "")
                allowed, retry_after, client_id = await rate_limit.check_and_record(
                    ip, user_agent, accept_language, request.url.path
                )
                if client_id is not None:
                    request.state.client_id = client_id
                if not allowed:
                    return JSONResponse(
                        {"detail": "rate limit exceeded; try again later"},
                        status_code=429,
                        headers={"Retry-After": str(retry_after)},
                    )
            except Exception:  # noqa: BLE001
                rate_limit.warn_once("ratelimit", "rate limit check failed; allowing request")
        else:
            rate_limit.warn_once("no_db", "DATABASE_URL is not configured; rate limiting disabled")

    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > MAX_BODY_BYTES:
                return JSONResponse(
                    {"detail": "request body too large"},
                    status_code=413,
                )
        except ValueError:
            pass

    return await call_next(request)


app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=SESSION_HTTPS_ONLY,
    max_age=60 * 60 * 8,
)


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


def _build_config(
    data: str,
    style: str,
    foreground: str,
    background: str,
    gradient: str,
    gradient_to: str,
    error_correction: str,
    box_size: int,
    border: int,
    image_format: str,
    logo: Image.Image | None,
    logo_ratio: float,
    transparent_background: bool,
    frame_color: str | None,
    title: str,
    subtitle: str,
) -> QRConfig:
    try:
        return QRConfig(
            data=data,
            style=style,
            foreground=foreground,
            background=background,
            gradient=gradient,
            gradient_to=gradient_to,
            error_correction=error_correction,
            box_size=box_size,
            border=border,
            image_format=image_format,
            logo=logo,
            logo_ratio=logo_ratio,
            transparent_background=transparent_background,
            frame_color=frame_color or None,
            title=title,
            subtitle=subtitle,
        )
    except TypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/healthz")
def healthz() -> JSONResponse:
    """Lightweight liveness probe; no QR work, no external dependencies."""
    return JSONResponse({"status": "ok"})


@app.get("/readyz")
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


@app.get("/api/catalog")
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


@app.post("/api/previews")
async def previews(
    data: Annotated[str, Form()] = "",
    file: Annotated[UploadFile | None, File()] = None,
    style: Annotated[str, Form()] = "square",
    foreground: Annotated[str, Form()] = "#000000",
    background: Annotated[str, Form()] = "#ffffff",
    gradient: Annotated[str, Form()] = "none",
    gradient_to: Annotated[str, Form()] = "#000000",
    error_correction: Annotated[str, Form()] = DEFAULT_EC,
    box_size: Annotated[int, Form()] = 10,
    border: Annotated[int, Form()] = 4,
    image_format: Annotated[str, Form()] = PNG,
    logo: Annotated[UploadFile | None, File()] = None,
    logo_ratio: Annotated[float, Form()] = 0.2,
    transparent_background: Annotated[bool, Form()] = False,
    frame_color: Annotated[str | None, Form()] = None,
    title: Annotated[str, Form()] = "",
    subtitle: Annotated[str, Form()] = "",
) -> JSONResponse:
    payload = await _resolve_input(data, file)
    logo_image = await _read_logo(logo)
    config = _build_config(
        payload.content, style, foreground, background, gradient, gradient_to,
        error_correction, box_size, border, image_format, logo_image,
        logo_ratio, transparent_background, frame_color, title, subtitle,
    )
    try:
        previews_out, selected, warnings = await run_in_threadpool(generate_previews, config)
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


@app.post("/api/generate")
async def generate(
    request: Request,
    background_tasks: BackgroundTasks,
    data: Annotated[str, Form()] = "",
    file: Annotated[UploadFile | None, File()] = None,
    style: Annotated[str, Form()] = "square",
    foreground: Annotated[str, Form()] = "#000000",
    background: Annotated[str, Form()] = "#ffffff",
    gradient: Annotated[str, Form()] = "none",
    gradient_to: Annotated[str, Form()] = "#000000",
    error_correction: Annotated[str, Form()] = DEFAULT_EC,
    box_size: Annotated[int, Form()] = 10,
    border: Annotated[int, Form()] = 4,
    image_format: Annotated[str, Form()] = PNG,
    logo: Annotated[UploadFile | None, File()] = None,
    logo_ratio: Annotated[float, Form()] = 0.2,
    transparent_background: Annotated[bool, Form()] = False,
    frame_color: Annotated[str | None, Form()] = None,
    title: Annotated[str, Form()] = "",
    subtitle: Annotated[str, Form()] = "",
) -> Response:
    payload = await _resolve_input(data, file)
    logo_image = await _read_logo(logo)
    config = _build_config(
        payload.content, style, foreground, background, gradient, gradient_to,
        error_correction, box_size, border, image_format, logo_image,
        logo_ratio, transparent_background, frame_color, title, subtitle,
    )
    try:
        result = await run_in_threadpool(generate_qr, config)
    except InvalidInput as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    client_id = getattr(request.state, "client_id", None)
    background_tasks.add_task(inputs.persist_input, client_id, payload)

    headers = {"X-QR-Warnings": "; ".join(result.warnings)}
    return Response(
        content=result.content,
        media_type=MEDIA_TYPES[result.image_format],
        headers=headers,
    )


app.include_router(admin_router)


def main() -> int:
    return run()


def run(host: str | None = None, port: int | None = None) -> int:
    import uvicorn

    host = host or os.environ.get("HOST", "127.0.0.1")
    port = port or int(os.environ.get("PORT", "8000"))

    print(f"QR Generator web UI: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
