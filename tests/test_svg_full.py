from __future__ import annotations

import xml.etree.ElementTree as ET

from PIL import Image

from qrgen.core import (
    PNG,
    STYLE_INFO,
    STYLES,
    SVG,
    QRConfig,
    generate_qr,
)


def make_config(**overrides) -> QRConfig:
    return QRConfig(data="hola", **overrides)


def svg_bytes(image_format: str = SVG) -> bytes:
    result = generate_qr(make_config(image_format=image_format))
    return result.content


def parse_svg(content: bytes) -> ET.Element:
    return ET.fromstring(content)


def test_every_style_renders_svg() -> None:
    """All seven styles must now render real SVG (no fallback to squares)."""
    for style in STYLES:
        result = generate_qr(make_config(image_format=SVG, style=style))
        assert not any("not available in SVG" in w for w in result.warnings), style
        root = parse_svg(result.content)
        paths = [el for el in root.iter() if el.tag.endswith("path")]
        assert paths, f"style {style} produced no path"


def test_rounded_drawer_uses_arcs() -> None:
    result = generate_qr(make_config(image_format=SVG, style="rounded"))
    path = next(el for el in parse_svg(result.content).iter() if el.tag.endswith("path"))
    assert "A" in path.get("d")  # arc commands present


def test_bars_drawers_use_caps() -> None:
    for style in ("vertical-bars", "horizontal-bars"):
        result = generate_qr(make_config(image_format=SVG, style=style))
        path = next(el for el in parse_svg(result.content).iter() if el.tag.endswith("path"))
        # rounded caps + bars geometry: two subpaths per module minimum
        assert "A" in path.get("d"), style
        assert path.get("d").count("z") >= 2, style


def test_gradient_applied_in_svg() -> None:
    result = generate_qr(
        make_config(image_format=SVG, gradient="linear-h", gradient_to="#0000ff")
    )
    assert not any("gradients are not available" in w for w in result.warnings)
    root = parse_svg(result.content)
    defs = next(el for el in root.iter() if el.tag.endswith("defs"))
    gradients = [el for el in defs.iter() if el.tag.endswith("Gradient")]
    assert len(gradients) == 1
    stops = [el.get("stop-color") for el in gradients[0].iter() if el.tag.endswith("stop")]
    assert stops == ["#000000", "#0000ff"]
    path = next(el for el in root.iter() if el.tag.endswith("path"))
    assert path.get("fill") == "url(#qr-fill)"


def test_radial_gradient_shape() -> None:
    result = generate_qr(
        make_config(image_format=SVG, gradient="radial", gradient_to="#00ff00")
    )
    root = parse_svg(result.content)
    grad = next(el for el in root.iter() if el.tag.endswith("radialGradient"))
    assert grad.get("cx") == "0.5" and grad.get("r") == "0.5"


def test_solid_fill_uses_foreground() -> None:
    """Regression: the SVG used to be hardcoded black."""
    result = generate_qr(make_config(image_format=SVG, foreground="#ff0000"))
    path = next(el for el in parse_svg(result.content).iter() if el.tag.endswith("path"))
    assert path.get("fill") == "#ff0000"


def test_background_rect_uses_back_color() -> None:
    result = generate_qr(make_config(image_format=SVG, background="#00ff00"))
    root = parse_svg(result.content)
    rects = [el for el in root.iter() if el.tag.endswith("rect")]
    assert len(rects) == 1
    assert rects[0].get("fill") == "#00ff00"


def test_transparent_background_is_native_in_svg() -> None:
    result = generate_qr(make_config(image_format=SVG, transparent_background=True))
    assert not any("transparent" in w for w in result.warnings)
    root = parse_svg(result.content)
    assert not [el for el in root.iter() if el.tag.endswith("rect")]


def test_gradient_still_png_only_in_api_semantics() -> None:
    """PNG keeps the raster masks; SVG uses native defs — both valid."""
    png_result = generate_qr(
        make_config(image_format=PNG, gradient="linear-h", gradient_to="#0000ff")
    )
    assert png_result.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_svg_omits_only_raster_only_options() -> None:
    """Logo, frame and text remain PNG-only; transparency is native now."""
    logo = Image.new("RGB", (64, 64), (0, 255, 0))
    result = generate_qr(
        make_config(
            image_format=SVG,
            logo=logo,
            transparent_background=True,
            frame_color="#18181b",
            title="Hola",
            subtitle="Web",
        )
    )
    warnings = result.warnings
    assert any("logo is not available in SVG" in w for w in warnings)
    assert any("frame is not available in SVG" in w for w in warnings)
    assert any("title is not available in SVG" in w for w in warnings)
    assert any("subtitle is not available in SVG" in w for w in warnings)
    assert not any("transparent background is not available" in w for w in warnings)
    # the rendered SVG must show no raster leftovers: no rect beyond background
    root = parse_svg(result.content)
    assert not [el for el in root.iter() if el.tag.endswith("rect")]
    path = next(el for el in root.iter() if el.tag.endswith("path"))
    assert path.get("fill") == "#000000"  # solid fg: gradient was none


def test_catalog_reports_all_styles_svg_capable() -> None:
    assert all(info.svg for info in STYLE_INFO), "SVG must support every style now"
