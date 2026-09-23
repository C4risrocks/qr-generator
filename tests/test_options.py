from __future__ import annotations

import pytest
from PIL import Image

from qrgen.core import PNG, InvalidInput, QRConfig, parse_options


def test_defaults_when_source_empty() -> None:
    config = parse_options({}, data="hola")
    assert config == QRConfig(data="hola")


def test_string_form_values_coerced() -> None:
    config = parse_options(
        {
            "style": "circles",
            "box_size": "12",
            "border": "2",
            "logo_ratio": "0.25",
            "transparent_background": "true",
            "frame_color": "",
            "title": "",
            "subtitle": "",
        },
        data="hola",
    )
    assert config.style == "circles"
    assert config.box_size == 12
    assert config.border == 2
    assert config.logo_ratio == 0.25
    assert config.transparent_background is True
    assert config.frame_color is None
    assert config.title == ""


def test_bool_string_variants() -> None:
    for raw, expected in (("true", True), ("1", True), ("false", False), ("0", False)):
        config = parse_options({"transparent_background": raw}, data="h")
        assert config.transparent_background is expected


def test_invalid_bool_rejected() -> None:
    with pytest.raises(InvalidInput, match="transparent_background must be a boolean"):
        parse_options({"transparent_background": "maybe"}, data="h")


def test_invalid_int_rejected() -> None:
    with pytest.raises(InvalidInput, match="box_size must be an integer"):
        parse_options({"box_size": "twelve"}, data="h")


def test_invalid_float_rejected() -> None:
    with pytest.raises(InvalidInput, match="logo_ratio must be a number"):
        parse_options({"logo_ratio": "wide"}, data="h")


def test_unknown_keys_ignored() -> None:
    config = parse_options({"mystery": "x", "style": "dots"}, data="h")
    assert config.style == "dots"


def test_missing_keys_take_dataclass_defaults() -> None:
    config = parse_options({"foreground": "#111111"}, data="h")
    assert config.foreground == "#111111"
    assert config.background == "#ffffff"
    assert config.error_correction == "M"
    assert config.image_format == PNG


def test_transport_overrides() -> None:
    logo = Image.new("RGB", (8, 8))
    config = parse_options({"data": "ignored"}, data="payload", logo=logo)
    # override wins over the source value
    assert config.data == "payload"
    assert config.logo is logo


def test_unknown_override_rejected() -> None:
    with pytest.raises(InvalidInput):
        parse_options({}, data="h", not_a_field=1)


def test_cli_mapping_with_none_frame() -> None:
    """CLI: --frame omitted passes None straight through (no 'None' string)."""
    config = parse_options(
        {"frame_color": None, "title": "", "subtitle": ""},
        data="h",
    )
    assert config.frame_color is None
    assert config.title == ""
