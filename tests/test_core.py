from __future__ import annotations

import io
import xml.etree.ElementTree as ET

import pytest
from PIL import Image

from qrgen.core import (
    PNG,
    STYLE_INFO,
    STYLES,
    SVG,
    InvalidInput,
    QRConfig,
    contrast_ratio,
    generate_previews,
    generate_qr,
)

URL = "https://example.com"


def make_config(**overrides) -> QRConfig:
    defaults: dict = {
        "data": URL,
        "style": "square",
        "foreground": "#000000",
        "background": "#ffffff",
        "gradient": "none",
        "gradient_to": "#000000",
        "error_correction": "M",
        "box_size": 10,
        "border": 4,
        "image_format": PNG,
    }
    defaults.update(overrides)
    return QRConfig(**defaults)


def open_png(result) -> Image.Image:
    assert result.content[:8] == b"\x89PNG\r\n\x1a\n"
    return Image.open(io.BytesIO(result.content))


@pytest.mark.parametrize("style", STYLES)
def test_every_style_renders_png(style: str) -> None:
    result = generate_qr(make_config(style=style))
    assert result.image_format == PNG
    img = open_png(result)
    assert img.size[0] == img.size[1]


def test_catalog_entries_are_consistent() -> None:
    assert [info.id for info in STYLE_INFO] == list(STYLES)
    assert all(info.png for info in STYLE_INFO)


def test_svg_renders_valid_xml() -> None:
    result = generate_qr(make_config(image_format=SVG))
    assert result.content.lstrip().startswith(b"<")
    ET.fromstring(result.content)
    assert b"<svg" in result.content or b"svg" in result.content[:60]


@pytest.mark.parametrize("style", ("rounded", "vertical-bars", "horizontal-bars"))
def test_svg_falls_back_with_warning(style: str) -> None:
    result = generate_qr(make_config(image_format=SVG, style=style))
    assert any("not available in SVG" in w for w in result.warnings)


def test_svg_gradient_falls_back_with_warning() -> None:
    result = generate_qr(
        make_config(image_format=SVG, gradient="linear-h", gradient_to="#0000ff")
    )
    assert any("gradients are not available in SVG" in w for w in result.warnings)


def test_custom_colors_applied() -> None:
    fg, bg = "#ff0000", "#0000ff"
    img = open_png(generate_qr(make_config(foreground=fg, background=bg)))
    colors = {color for _, color in img.getcolors(maxcolors=1_000_000)}
    assert (255, 0, 0) in colors
    assert (0, 0, 255) in colors


def _channel_extremes(img: Image.Image) -> tuple[int, int]:
    max_red = max_blue = 0
    for _, color in img.getcolors(maxcolors=1_000_000):
        r, b = color[0], color[2]
        max_red = max(max_red, r - b)
        max_blue = max(max_blue, b - r)
    return max_red, max_blue


def test_linear_gradient_applied() -> None:
    result = generate_qr(
        make_config(foreground="#ff0000", gradient="linear-h", gradient_to="#0000ff")
    )
    img = open_png(result)
    max_red, max_blue = _channel_extremes(img)
    assert max_red > 120
    assert max_blue > 120


def test_same_gradient_colors_rejected() -> None:
    with pytest.raises(InvalidInput, match="gradient colors must differ"):
        generate_qr(make_config(gradient="radial", gradient_to="#000000"))


def test_transparent_background_renders_rgba() -> None:
    result = generate_qr(make_config(transparent_background=True))
    img = open_png(result)
    assert img.mode == "RGBA"
    corner = img.getpixel((0, 0))
    assert corner[3] == 0


def test_transparent_background_rejected_for_svg() -> None:
    with pytest.raises(InvalidInput, match="transparent background is only supported for PNG"):
        generate_qr(make_config(image_format=SVG, transparent_background=True))


def test_frame_and_title_rejected_for_svg() -> None:
    with pytest.raises(InvalidInput, match="frame and text are only supported for PNG"):
        generate_qr(make_config(image_format=SVG, title="Hola"))


def test_frame_and_title_require_solid_background() -> None:
    with pytest.raises(InvalidInput, match="require a solid background"):
        generate_qr(make_config(transparent_background=True, frame_color="#222222"))


def test_frame_title_subtitle_enlarge_output() -> None:
    plain = open_png(generate_qr(make_config()))
    framed = open_png(
        generate_qr(make_config(frame_color="#18181b", title="Mi enlace", subtitle="Sitio web"))
    )
    assert framed.size[0] > plain.size[0]
    assert framed.size[1] > plain.size[1]


def test_logo_embeds_and_forces_error_correction() -> None:
    logo = Image.new("RGB", (64, 64), (0, 255, 0))
    result = generate_qr(make_config(logo=logo, error_correction="L"))
    assert any("raised to H" in w for w in result.warnings)
    img = open_png(result)
    center = img.getpixel((img.size[0] // 2, img.size[1] // 2))
    assert center[:3] == (0, 255, 0)


def test_logo_rejected_for_svg() -> None:
    logo = Image.new("RGB", (64, 64), (0, 255, 0))
    with pytest.raises(InvalidInput, match="only supported for PNG"):
        generate_qr(make_config(image_format=SVG, logo=logo))


def test_empty_data_rejected() -> None:
    with pytest.raises(InvalidInput, match="cannot be empty"):
        generate_qr(make_config(data="   "))


def test_data_length_limited() -> None:
    with pytest.raises(InvalidInput, match="maximum length"):
        generate_qr(make_config(data="x" * 2049))


def test_oversized_data_rejected() -> None:
    with pytest.raises(InvalidInput, match="too large"):
        generate_qr(make_config(data="x" * 1800, error_correction="H"))


def test_unknown_style_rejected() -> None:
    with pytest.raises(InvalidInput, match="unknown style"):
        generate_qr(make_config(style="nope"))


def test_unknown_gradient_rejected() -> None:
    with pytest.raises(InvalidInput, match="unknown gradient"):
        generate_qr(make_config(gradient="nope"))


def test_same_colors_rejected() -> None:
    with pytest.raises(InvalidInput, match="must differ"):
        generate_qr(make_config(foreground="#000000", background="#000000"))


def test_invalid_color_rejected() -> None:
    with pytest.raises(InvalidInput, match="invalid color"):
        generate_qr(make_config(foreground="not-a-color"))


def test_low_contrast_warns() -> None:
    result = generate_qr(make_config(foreground="#010101", background="#020202"))
    assert any("low contrast" in w for w in result.warnings)


def test_contrast_ratio_basic() -> None:
    assert contrast_ratio((0, 0, 0), (255, 255, 255)) > 15
    assert contrast_ratio((0, 0, 0), (0, 0, 0)) == 1


def test_box_size_and_border_control_dimensions() -> None:
    small = open_png(generate_qr(make_config(box_size=5, border=2)))
    large = open_png(generate_qr(make_config(box_size=10, border=4)))
    assert large.size[0] > small.size[0]


def test_previews_cover_every_style() -> None:
    previews, selected, warnings = generate_previews(make_config())
    assert [p.style for p in previews] == list(STYLES)
    assert selected.style == "square"
    assert not warnings
    for preview in previews + [selected]:
        assert preview.content[:8] == b"\x89PNG\r\n\x1a\n"
        img = Image.open(io.BytesIO(preview.content))
        assert img.size[0] == img.size[1]


def test_previews_apply_custom_colors() -> None:
    previews, _, _ = generate_previews(
        make_config(foreground="#ff0000", gradient="linear-h", gradient_to="#0000ff")
    )
    img = Image.open(io.BytesIO(previews[0].content))
    max_red, max_blue = _channel_extremes(img)
    assert max_red > 120
    assert max_blue > 120


def test_selected_preview_reflects_style_and_frame() -> None:
    plain_previews, plain_selected, _ = generate_previews(make_config(style="circles"))
    framed_previews, framed_selected, _ = generate_previews(
        make_config(style="circles", frame_color="#18181b", title="Título", subtitle="Sub")
    )
    assert plain_selected.style == "circles"
    assert framed_selected.style == "circles"
    assert (
        Image.open(io.BytesIO(framed_selected.content)).size[1]
        > Image.open(io.BytesIO(plain_selected.content)).size[1]
    )
    assert [
        a.content == b.content for a, b in zip(plain_previews, framed_previews)
    ] == [True] * len(STYLES)


def test_selected_preview_caps_box_size() -> None:
    _, selected, _ = generate_previews(make_config(box_size=30))
    img = Image.open(io.BytesIO(selected.content))
    assert img.size[0] <= 1000
