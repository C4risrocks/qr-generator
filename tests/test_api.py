from __future__ import annotations

import base64
import importlib.metadata
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from qrgen import db
from qrgen.db import QrInput
from qrgen.server import app

client = TestClient(app)


def test_body_too_large_rejected(monkeypatch) -> None:
    from qrgen import server

    monkeypatch.setattr(server, "MAX_BODY_BYTES", 100)
    res = client.post("/api/generate", data={"data": "x" * 200})
    assert res.status_code == 413


def test_rate_limit_enforced(monkeypatch) -> None:
    from qrgen import rate_limit

    monkeypatch.setattr(rate_limit, "ENDPOINT_LIMITS", {"generate": 2, "previews": 500})
    for _ in range(2):
        res = client.post("/api/generate", data={"data": "x"})
        assert res.status_code == 200
    res = client.post("/api/generate", data={"data": "x"})
    assert res.status_code == 429
    assert int(res.headers["retry-after"]) > 0


def test_rate_limit_returns_retry_after() -> None:
    res = client.post("/api/generate", data={"data": "x"})
    assert res.status_code == 200


def test_input_persisted_after_generation() -> None:
    res = client.post("/api/generate", data={"data": "https://ejemplo.com"})
    assert res.status_code == 200

    async def fetch() -> list[QrInput]:
        async with db.session_factory()() as session:
            return (await session.execute(select(QrInput))).scalars().all()

    import asyncio

    inputs = asyncio.run(fetch())
    assert len(inputs) == 1
    assert inputs[0].filename is None
    assert inputs[0].content == "https://ejemplo.com"
    assert inputs[0].content_type == "text"
    assert inputs[0].client_id is not None


def test_file_input_persisted_with_filename() -> None:
    res = client.post(
        "/api/generate",
        files={"file": ("archivo.txt", b"contenido del archivo", "text/plain")},
    )
    assert res.status_code == 200

    import asyncio

    async def fetch() -> list[QrInput]:
        async with db.session_factory()() as session:
            return (await session.execute(select(QrInput))).scalars().all()

    inputs = asyncio.run(fetch())
    assert len(inputs) == 1
    assert inputs[0].filename == "archivo.txt"
    assert inputs[0].content == "contenido del archivo"
    assert inputs[0].content_type == "text/plain"


def test_previews_do_not_persist() -> None:
    client.post("/api/previews", data={"data": "https://ejemplo.com"})

    import asyncio

    async def count() -> int:
        async with db.session_factory()() as session:
            return len((await session.execute(select(QrInput))).scalars().all())

    assert asyncio.run(count()) == 0


def test_previews_with_file() -> None:
    res = client.post(
        "/api/previews",
        files={"file": ("archivo.txt", b"hola", "text/plain")},
    )
    assert res.status_code == 200
    assert len(res.json()["styles"]) == 7


def test_generate_rejects_both_inputs() -> None:
    res = client.post(
        "/api/generate",
        data={"data": "hola"},
        files={"file": ("a.txt", b"hola", "text/plain")},
    )
    assert res.status_code == 400


def test_generate_rejects_invalid_utf8_file() -> None:
    res = client.post(
        "/api/generate",
        files={"file": ("a.txt", b"\xff\xfe\x00", "text/plain")},
    )
    assert res.status_code == 400
    assert "UTF-8" in res.json()["detail"]


def test_generate_rejects_binary_file() -> None:
    res = client.post(
        "/api/generate",
        files={"file": ("a.bin", b"PNG\x00\x01", "application/octet-stream")},
    )
    assert res.status_code == 400


def test_generate_rejects_no_input() -> None:
    res = client.post("/api/generate")
    assert res.status_code == 400


def test_readyz_ok_with_database() -> None:
    res = client.get("/readyz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_readyz_unavailable_without_database(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db.reset()
    res = client.get("/readyz")
    assert res.status_code == 503


def png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def test_catalog_shape() -> None:
    res = client.get("/api/catalog")
    assert res.status_code == 200
    payload = res.json()
    assert len(payload["styles"]) >= 7
    for style in payload["styles"]:
        assert {"id", "label", "description", "png", "svg"} <= set(style)
    assert any(p["id"] == "linear-h" for p in payload["gradient_types"])
    assert payload["palettes"]
    assert payload["presets"]


def test_previews_endpoint_returns_all_styles() -> None:
    res = client.post(
        "/api/previews",
        data={
            "data": "https://example.com",
            "style": "circles",
            "foreground": "#1d4ed8",
            "background": "#f5f8ff",
        },
    )
    assert res.status_code == 200
    payload = res.json()
    assert len(payload["styles"]) == 7
    for item in payload["styles"]:
        assert item["image"].startswith("data:image/png;base64,")
    assert payload["selected"]["style"] == "circles"
    assert payload["selected"]["image"].startswith("data:image/png;base64,")
    assert "warnings" in payload


def test_previews_reject_empty_data() -> None:
    res = client.post("/api/previews", data={"data": "  "})
    assert res.status_code == 400


def test_previews_rejects_bad_style() -> None:
    res = client.post(
        "/api/previews", data={"data": "https://example.com", "style": "nope"}
    )
    assert res.status_code == 400


WEB_PAYLOAD = {
    "data": "https://ejemplo.com",
    "style": "dots",
    "foreground": "#18181b",
    "background": "#ffffff",
    "gradient": "none",
    "gradient_to": "#18181b",
    "error_correction": "M",
    "box_size": "10",
    "border": "4",
    "image_format": "png",
    "logo_ratio": "0.2",
    "transparent_background": "false",
    "frame_color": "",
    "title": "",
    "subtitle": "",
}


def test_generate_accepts_full_web_payload() -> None:
    """Regression: the web always sends frame_color='' and booleans as strings."""
    res = client.post("/api/generate", data=WEB_PAYLOAD)
    assert res.status_code == 200
    assert res.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_previews_accepts_full_web_payload() -> None:
    res = client.post("/api/previews", data=WEB_PAYLOAD)
    assert res.status_code == 200
    assert len(res.json()["styles"]) == 7


def test_generate_accepts_web_payload_with_svg() -> None:
    payload = dict(WEB_PAYLOAD)
    payload["image_format"] = "svg"
    res = client.post("/api/generate", data=payload)
    assert res.status_code == 200
    assert "svg" in res.headers["content-type"]


def test_generate_returns_png() -> None:
    res = client.post("/api/generate", data={"data": "https://example.com"})
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_generate_svg() -> None:
    res = client.post(
        "/api/generate", data={"data": "https://example.com", "image_format": "svg"}
    )
    assert res.status_code == 200
    assert "svg" in res.headers["content-type"]


def test_generate_with_gradient_and_transparency() -> None:
    res = client.post(
        "/api/generate",
        data={
            "data": "https://example.com",
            "gradient": "linear-h",
            "gradient_to": "#0000ff",
            "transparent_background": "true",
        },
    )
    assert res.status_code == 200
    assert res.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_generate_rejects_bad_style() -> None:
    res = client.post(
        "/api/generate", data={"data": "https://example.com", "style": "nope"}
    )
    assert res.status_code == 400


def test_generate_rejects_large_logo() -> None:
    big = Image.new("RGB", (3000, 3000), (255, 0, 0))
    res = client.post(
        "/api/generate",
        data={"data": "https://example.com"},
        files={"logo": ("logo.png", png_bytes(big), "image/png")},
    )
    assert res.status_code == 400
    assert "too large" in res.json()["detail"]


def test_generate_rejects_invalid_logo() -> None:
    res = client.post(
        "/api/generate",
        data={"data": "https://example.com"},
        files={"logo": ("logo.png", b"not-an-image", "image/png")},
    )
    assert res.status_code == 400


def test_generate_warns_on_low_contrast() -> None:
    res = client.post(
        "/api/generate",
        data={"data": "hola", "foreground": "#010101", "background": "#020202"},
    )
    assert res.status_code == 200
    assert "low contrast" in res.headers.get("x-qr-warnings", "")


def test_index_page_served() -> None:
    res = client.get("/")
    assert res.status_code == 200
    assert "QR Studio" in res.text


def test_default_web_dir_contains_index() -> None:
    from qrgen import server

    assert (server.WEB_DIR / "index.html").is_file()


def test_web_dir_respects_env_override(monkeypatch, tmp_path) -> None:
    from qrgen import webassets

    (tmp_path / "index.html").write_text("hola")
    monkeypatch.setenv("QRGEN_WEB_DIR", str(tmp_path))
    assert webassets.resolve_web_dir() == tmp_path


def test_healthz() -> None:
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_generate_rejects_too_long_data() -> None:
    res = client.post("/api/generate", data={"data": "x" * 2049})
    assert res.status_code == 400
    assert "maximum length" in res.json()["detail"]


def test_console_script_entry_points() -> None:
    scripts = {
        entry.name: entry.value
        for entry in importlib.metadata.entry_points(group="console_scripts")
    }
    assert scripts.get("qrgen") == "qrgen.cli:main"
    assert scripts.get("qrgen-serve") == "qrgen.server:main"


def test_server_has_main() -> None:
    from qrgen import server

    assert callable(server.main)


@pytest.mark.parametrize("style", ("square", "circles", "dots"))
def test_selected_preview_matches_requested_style(style: str) -> None:
    previews_res = client.post(
        "/api/previews", data={"data": "https://example.com", "style": style}
    )
    generate_res = client.post(
        "/api/generate", data={"data": "https://example.com", "style": style}
    )
    assert previews_res.status_code == 200
    assert generate_res.status_code == 200
    payload = previews_res.json()
    assert payload["selected"]["style"] == style
    selected_img = Image.open(
        io.BytesIO(
            base64.b64decode(payload["selected"]["image"].split(",", 1)[1])
        )
    )
    final_img = Image.open(io.BytesIO(generate_res.content))
    assert final_img.size[0] >= selected_img.size[0]
