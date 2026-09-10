"""Resolution of QR input payloads (text or UTF-8 file) shared by CLI and web."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from qrgen.core import InvalidInput

logger = logging.getLogger("qrgen.inputs")

MAX_INPUT_CHARS = 2048
MAX_FILENAME_LENGTH = 255
MAX_INPUT_BYTES = 10_000  # 2048 chars * 4 bytes + BOM margin


@dataclass(frozen=True)
class InputPayload:
    filename: str | None
    content: str
    content_type: str


def parse_text(data: str) -> InputPayload:
    content = data.strip()
    if not content:
        raise InvalidInput("data cannot be empty")
    if len(content) > MAX_INPUT_CHARS:
        raise InvalidInput(
            f"data exceeds the maximum length of {MAX_INPUT_CHARS} characters"
        )
    return InputPayload(filename=None, content=content, content_type="text")


def parse_file(filename: str, raw: bytes) -> InputPayload:
    if not filename:
        raise InvalidInput("a file name is required")
    name = Path(filename).name
    if not name:
        raise InvalidInput("invalid file name")
    if len(name) > MAX_FILENAME_LENGTH:
        raise InvalidInput(f"file name exceeds the maximum length of {MAX_FILENAME_LENGTH} characters")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InvalidInput("file is not valid UTF-8 text") from exc
    if "\x00" in content:
        raise InvalidInput("binary files are not supported")
    if not content.strip():
        raise InvalidInput("file is empty")
    if len(content) > MAX_INPUT_CHARS:
        raise InvalidInput(
            f"file content exceeds the maximum length of {MAX_INPUT_CHARS} characters"
        )
    return InputPayload(filename=name, content=content, content_type="text/plain")


async def persist_input(client_id: int | None, payload: InputPayload) -> None:
    """Persist the input after a successful QR generation (fail-open)."""
    from qrgen import db
    from qrgen.db import QrInput

    if client_id is None or not db.is_configured():
        return
    try:
        async with db.session_factory()() as session:
            session.add(
                QrInput(
                    client_id=client_id,
                    filename=payload.filename,
                    content=payload.content,
                    content_type=payload.content_type,
                    content_hash=db.sha256_hex(payload.content),
                )
            )
            await session.commit()
    except Exception:
        logger.exception("failed to persist qr input")
