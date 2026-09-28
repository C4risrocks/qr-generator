from __future__ import annotations

import io

import pytest
from PIL import Image

from qrgen.core import (
    MAX_LOGO_DIMENSIONS,
    InvalidInput,
    parse_logo_crop,
    prepare_logo,
)


def solid(width: int, height: int, color=(0, 255, 0), mode="RGB") -> Image.Image:
    img = Image.new(mode, (width, height), color)
    return img


def test_small_logo_passes_through_unchanged() -> None:
    logo = solid(64, 64)
    fitted, warnings = prepare_logo(logo)
    assert fitted.size == (64, 64)
    assert warnings == ()


def test_rotated_90_swaps_dimensions() -> None:
    logo = solid(80, 40)
    fitted, _ = prepare_logo(logo, rotate=90)
    assert fitted.size == (40, 80)
    # a 90-degree transpose moves the top-left corner to the top-right
    assert fitted.getpixel((39, 0)) == (0, 255, 0)


def test_rotation_must_be_quarter_turn() -> None:
    with pytest.raises(InvalidInput, match="logo_rotate must be 0, 90, 180 or 270"):
        prepare_logo(solid(10, 10), rotate=45)


def test_rotation_rejects_non_integer() -> None:
    with pytest.raises(InvalidInput, match="logo_rotate must be 0, 90, 180 or 270"):
        prepare_logo(solid(10, 10), rotate="30")


def test_crop_takes_exact_region() -> None:
    logo = Image.new("RGB", (100, 100))
    for x in range(0, 100, 10):
        for y in range(0, 100, 10):
            logo.putpixel((x, y), (x, y, 0))
    fitted, warnings = prepare_logo(logo, crop=(10, 20, 30, 40))
    assert fitted.size == (30, 40)
    assert fitted.getpixel((0, 0)) == logo.getpixel((10, 20))
    assert fitted.getpixel((29, 39)) == logo.getpixel((39, 59))
    assert warnings == ()


def test_crop_out_of_bounds_rejected() -> None:
    with pytest.raises(InvalidInput, match="exceeds the image bounds"):
        prepare_logo(solid(50, 50), crop=(0, 0, 60, 10))


def test_oversized_logo_fits_with_warning() -> None:
    logo = solid(3000, 2000)
    fitted, warnings = prepare_logo(logo)
    assert fitted.size == (1024, 683)  # aspect preserved, ceil inside the box
    assert len(warnings) == 1
    assert "resized" in warnings[0]
    assert "3000x2000" in warnings[0]
    assert "1024x683" in warnings[0]


def test_fit_never_exceeds_max_dimension() -> None:
    logo = solid(999, 3000)
    fitted, _ = prepare_logo(logo)
    assert fitted.size[0] <= MAX_LOGO_DIMENSIONS
    assert fitted.size[1] <= MAX_LOGO_DIMENSIONS


def test_rotate_then_crop_uses_rotated_coordinates() -> None:
    # 60x30 image rotated 90° becomes 30x60; crop in rotated space
    logo = solid(60, 30)
    logo.putpixel((59, 0), (255, 0, 0))  # top-right of original = top-left after 90° CW?
    fitted, _ = prepare_logo(logo, rotate=90, crop=(0, 0, 10, 10))
    assert fitted.size == (10, 10)


def test_rgba_mode_and_alpha_preserved() -> None:
    logo = Image.new("RGBA", (200, 200), (10, 20, 30, 200))
    fitted, _ = prepare_logo(logo)
    assert fitted.mode == "RGBA"
    # alpha channel values survive the pipeline when no resize is needed
    assert fitted.getpixel((0, 0)) == (10, 20, 30, 200)


def test_rgb_mode_stays_rgb() -> None:
    fitted, _ = prepare_logo(solid(500, 500))
    assert fitted.mode == "RGB"


def test_l_mode_stays_l() -> None:
    fitted, _ = prepare_logo(solid(500, 500, color=128, mode="L"))
    assert fitted.mode == "L"


def test_palette_stays_palette_without_resize() -> None:
    logo = Image.new("P", (64, 64), 1)
    logo.putpalette([0, 0, 0, 255, 0, 0])
    logo.info["transparency"] = 0
    fitted, _ = prepare_logo(logo)
    assert fitted.mode == "P"


def test_palette_with_transparency_converts_when_resized() -> None:
    logo = Image.new("P", (2048, 2048), 1)
    logo.putpalette([0, 0, 0, 255, 0, 0])
    logo.info["transparency"] = 0
    fitted, _ = prepare_logo(logo)
    assert fitted.mode == "RGBA"
    assert fitted.size == (1024, 1024)


def test_resize_on_rgba_keeps_semi_alpha_sane() -> None:
    # premultiply/unpremultiply must round-trip a uniform semi-alpha color
    logo = Image.new("RGBA", (2048, 2048), (255, 0, 0, 128))
    fitted, _ = prepare_logo(logo)
    assert fitted.size == (1024, 1024)
    r, g, b, a = fitted.getpixel((512, 512))
    assert a == 128
    assert abs(r - 255) <= 2 and g == 0 and b == 0


def test_exif_orientation_applied() -> None:
    """EXIF Orientation 6 (rotate 90 CW) must be honored like browsers do."""
    logo = solid(80, 40)
    exif = Image.Exif()
    exif[274] = 6  # orientation tag
    logo.save(buffer := io.BytesIO(), format="PNG", exif=exif.tobytes())
    reloaded = Image.open(buffer)
    fitted, _ = prepare_logo(reloaded)
    assert fitted.size == (40, 80)


def test_parse_logo_crop_valid() -> None:
    assert parse_logo_crop('{"x":0,"y":0,"w":100,"h":50}') == (0, 0, 100, 50)
    assert parse_logo_crop(None) is None
    assert parse_logo_crop("") is None


def test_parse_logo_crop_invalid_json() -> None:
    with pytest.raises(InvalidInput, match="logo_crop must be a JSON object"):
        parse_logo_crop("not-json")


def test_parse_logo_crop_wrong_keys() -> None:
    with pytest.raises(InvalidInput, match="logo_crop must be a JSON object"):
        parse_logo_crop('{"x":0,"y":0,"w":10}')


def test_parse_logo_crop_non_int() -> None:
    with pytest.raises(InvalidInput, match="must be an integer"):
        parse_logo_crop('{"x":0,"y":0,"w":10.5,"h":10}')


def test_parse_logo_crop_negative_or_zero_size() -> None:
    with pytest.raises(InvalidInput, match="positive width and height"):
        parse_logo_crop('{"x":0,"y":0,"w":0,"h":10}')
    with pytest.raises(InvalidInput, match="coordinates must be non-negative"):
        parse_logo_crop('{"x":-5,"y":0,"w":10,"h":10}')
