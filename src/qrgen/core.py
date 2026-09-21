"""Core QR generation logic shared by the CLI and the web server."""

from __future__ import annotations

import io
from dataclasses import dataclass, replace
from decimal import Decimal

from PIL import Image, ImageColor, ImageDraw, ImageFont
from qrcode import QRCode, constants
from qrcode.exceptions import DataOverflowError
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.colormasks import (
    HorizontalGradiantColorMask,
    RadialGradiantColorMask,
    SolidFillColorMask,
    VerticalGradiantColorMask,
)
from qrcode.image.styles.moduledrawers.pil import (
    ANTIALIASING_FACTOR,
    CircleModuleDrawer,
    GappedSquareModuleDrawer,
    HorizontalBarsDrawer,
    RoundedModuleDrawer,
    SquareModuleDrawer,
    VerticalBarsDrawer,
)
from qrcode.image.svg import SvgPathImage, svg_drawers

PNG = "png"
SVG = "svg"
FORMATS = (PNG, SVG)

STYLE_SQUARE = "square"
STYLE_GAPPED = "gapped"
STYLE_ROUNDED = "rounded"
STYLE_CIRCLES = "circles"
STYLE_DOTS = "dots"
STYLE_VERTICAL_BARS = "vertical-bars"
STYLE_HORIZONTAL_BARS = "horizontal-bars"

GRADIENT_NONE = "none"
GRADIENT_LINEAR_H = "linear-h"
GRADIENT_LINEAR_V = "linear-v"
GRADIENT_RADIAL = "radial"
GRADIENT_TYPES = (GRADIENT_NONE, GRADIENT_LINEAR_H, GRADIENT_LINEAR_V, GRADIENT_RADIAL)

ERROR_CORRECTION = {
    "L": constants.ERROR_CORRECT_L,
    "M": constants.ERROR_CORRECT_M,
    "Q": constants.ERROR_CORRECT_Q,
    "H": constants.ERROR_CORRECT_H,
}
EC_LEVELS = tuple(ERROR_CORRECTION)

DEFAULT_EC = "M"
MIN_CONTRAST_RATIO = 3.0
MAX_LOGO_RATIO = 0.3
MAX_LOGO_DIMENSIONS = 1024
MAX_DATA_LENGTH = 2048

MEDIA_TYPES = {PNG: "image/png", SVG: "image/svg+xml"}


@dataclass(frozen=True)
class StyleInfo:
    id: str
    label: str
    description: str
    png: bool
    svg: bool


STYLE_INFO: tuple[StyleInfo, ...] = (
    StyleInfo(STYLE_SQUARE, "Cuadrado", "Forma clásica, máxima compatibilidad de lectura", True, True),
    StyleInfo(STYLE_GAPPED, "Cuadrado separado", "Módulos con aire entre sí", True, True),
    StyleInfo(STYLE_ROUNDED, "Redondeado", "Esquinas suaves, aspecto moderno", True, False),
    StyleInfo(STYLE_CIRCLES, "Círculos", "Módulos circulares continuos", True, True),
    StyleInfo(STYLE_DOTS, "Puntos", "Módulos circulares pequeños", True, True),
    StyleInfo(STYLE_VERTICAL_BARS, "Barras verticales", "Barras verticales continuas", True, False),
    StyleInfo(STYLE_HORIZONTAL_BARS, "Barras horizontales", "Barras horizontales continuas", True, False),
)
STYLES = tuple(info.id for info in STYLE_INFO)
STYLE_LABELS = {info.id: info.label for info in STYLE_INFO}
SVG_CAPABLE_STYLES = frozenset(info.id for info in STYLE_INFO if info.svg)
# Single source of truth for the SVG fallbacks, shared by previews and export.
SVG_STYLE_FALLBACK_WARNING = "is not available in SVG; using plain squares"
SVG_GRADIENT_FALLBACK_WARNING = "gradients are not available in SVG; using solid foreground"


def _preview_style(style: str, svg_mode: bool) -> str:
    """Style actually rendered in previews, mirroring the SVG export fallback."""
    if svg_mode and style not in SVG_CAPABLE_STYLES:
        return STYLE_SQUARE
    return style

GRADIENT_INFO = (
    {"id": GRADIENT_NONE, "label": "Sólido"},
    {"id": GRADIENT_LINEAR_H, "label": "Lineal horizontal"},
    {"id": GRADIENT_LINEAR_V, "label": "Lineal vertical"},
    {"id": GRADIENT_RADIAL, "label": "Radial"},
)

PALETTES = (
    {"id": "ink", "label": "Tinta", "foreground": "#18181b", "background": "#ffffff"},
    {"id": "paper", "label": "Pergamino", "foreground": "#292524", "background": "#fafaf9"},
    {"id": "cobalt", "label": "Cobalto", "foreground": "#1d4ed8", "background": "#f5f8ff"},
    {"id": "forest", "label": "Bosque", "foreground": "#15803d", "background": "#f0fdf4"},
    {"id": "wine", "label": "Vino", "foreground": "#9f1239", "background": "#fff1f2"},
    {"id": "night", "label": "Noche", "foreground": "#f4f4f5", "background": "#18181b"},
    {"id": "sand", "label": "Arena", "foreground": "#c2410c", "background": "#fff7ed"},
    {"id": "ocean", "label": "Océano", "foreground": "#0e7490", "background": "#ecfeff"},
)

PRESETS = (
    {
        "id": "classic",
        "label": "Clásico",
        "description": "La opción segura para cualquier uso",
        "style": STYLE_SQUARE,
        "foreground": "#18181b",
        "background": "#ffffff",
        "gradient": GRADIENT_NONE,
        "gradient_to": "#18181b",
        "error_correction": "M",
    },
    {
        "id": "cobalt",
        "label": "Cobalto",
        "description": "Círculos azules sobre fondo claro",
        "style": STYLE_CIRCLES,
        "foreground": "#1d4ed8",
        "background": "#f5f8ff",
        "gradient": GRADIENT_NONE,
        "gradient_to": "#1d4ed8",
        "error_correction": "M",
    },
    {
        "id": "dusk",
        "label": "Atardecer",
        "description": "Gradiente radial naranja a rosa",
        "style": STYLE_CIRCLES,
        "foreground": "#ea580c",
        "background": "#fff7ed",
        "gradient": GRADIENT_RADIAL,
        "gradient_to": "#db2777",
        "error_correction": "M",
    },
    {
        "id": "electric",
        "label": "Eléctrico",
        "description": "Barras con gradiente lineal",
        "style": STYLE_VERTICAL_BARS,
        "foreground": "#0284c7",
        "background": "#f0f9ff",
        "gradient": GRADIENT_LINEAR_H,
        "gradient_to": "#7c3aed",
        "error_correction": "M",
    },
    {
        "id": "night",
        "label": "Noche",
        "description": "Puntos claros sobre fondo oscuro",
        "style": STYLE_DOTS,
        "foreground": "#f4f4f5",
        "background": "#18181b",
        "gradient": GRADIENT_NONE,
        "gradient_to": "#f4f4f5",
        "error_correction": "H",
    },
    {
        "id": "forest",
        "label": "Bosque",
        "description": "Redondeado verde sobre fondo claro",
        "style": STYLE_ROUNDED,
        "foreground": "#166534",
        "background": "#f0fdf4",
        "gradient": GRADIENT_NONE,
        "gradient_to": "#166534",
        "error_correction": "M",
    },
)


class InvalidInput(ValueError):
    """Raised when the requested options cannot produce a QR code."""


class DotsModuleDrawer(CircleModuleDrawer):
    """Circle module drawer that shrinks the circles to dot-sized shapes."""

    def __init__(self, size_ratio: float = 0.5) -> None:
        self.size_ratio = size_ratio

    def initialize(self, *args, **kwargs) -> None:
        super().initialize(*args, **kwargs)
        box_size = self.img.box_size
        fake_size = box_size * ANTIALIASING_FACTOR
        self.circle = Image.new(
            self.img.mode,
            (fake_size, fake_size),
            self.img.color_mask.back_color,
        )
        diameter = fake_size * self.size_ratio
        margin = (fake_size - diameter) / 2
        ImageDraw.Draw(self.circle).ellipse(
            (margin, margin, margin + diameter, margin + diameter),
            fill=self.img.paint_color,
        )
        self.circle = self.circle.resize(
            (box_size, box_size), Image.Resampling.LANCZOS
        )


def build_pil_drawer(style: str):
    return {
        STYLE_SQUARE: SquareModuleDrawer,
        STYLE_GAPPED: GappedSquareModuleDrawer,
        STYLE_ROUNDED: RoundedModuleDrawer,
        STYLE_CIRCLES: CircleModuleDrawer,
        STYLE_DOTS: DotsModuleDrawer,
        STYLE_VERTICAL_BARS: VerticalBarsDrawer,
        STYLE_HORIZONTAL_BARS: HorizontalBarsDrawer,
    }[style]()


def build_svg_drawer(style: str):
    return {
        STYLE_SQUARE: svg_drawers.SvgPathSquareDrawer,
        STYLE_GAPPED: svg_drawers.SvgPathSquareDrawer,
        STYLE_CIRCLES: svg_drawers.SvgPathCircleDrawer,
        STYLE_DOTS: svg_drawers.SvgPathCircleDrawer,
    }.get(
        style,
        svg_drawers.SvgPathSquareDrawer,
    )(size_ratio=Decimal("0.7") if style in (STYLE_GAPPED, STYLE_DOTS) else Decimal(1))


@dataclass(frozen=True)
class QRConfig:
    """Fully resolved options for one QR code."""

    data: str
    style: str = STYLE_SQUARE
    foreground: str = "#000000"
    background: str = "#ffffff"
    gradient: str = GRADIENT_NONE
    gradient_to: str = "#000000"
    error_correction: str = DEFAULT_EC
    box_size: int = 10
    border: int = 4
    image_format: str = PNG
    logo: Image.Image | None = None
    logo_ratio: float = 0.2
    transparent_background: bool = False
    frame_color: str | None = None
    title: str = ""
    subtitle: str = ""


@dataclass(frozen=True)
class QRResult:
    content: bytes
    image_format: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class Preview:
    style: str
    content: bytes


def parse_color(value: str) -> tuple[int, int, int]:
    try:
        color = ImageColor.getrgb(value)
    except ValueError as exc:
        raise InvalidInput(f"invalid color: {value!r}") from exc
    if len(color) == 4:
        color = color[:3]
    return tuple(color)  # type: ignore[return-value]


def validate_logo(image: Image.Image) -> None:
    """Reject logos whose pixel dimensions exceed the documented limit."""
    if image.width > MAX_LOGO_DIMENSIONS or image.height > MAX_LOGO_DIMENSIONS:
        raise InvalidInput(
            f"logo image too large ({image.width}x{image.height}); maximum is {MAX_LOGO_DIMENSIONS}x{MAX_LOGO_DIMENSIONS} px"
        )


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    channels = []
    for channel in rgb:
        c = channel / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(c1: tuple[int, int, int], c2: tuple[int, int, int]) -> float:
    l1, l2 = _relative_luminance(c1), _relative_luminance(c2)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _parse_config(
    config: QRConfig,
) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int], list[str]]:
    """Validate the config and return (foreground, background, gradient_to, warnings)."""
    warnings: list[str] = []

    data = config.data.strip()
    if not data:
        raise InvalidInput("data cannot be empty")
    if len(data) > MAX_DATA_LENGTH:
        raise InvalidInput(f"data exceeds the maximum length of {MAX_DATA_LENGTH} characters")

    if config.style not in STYLES:
        raise InvalidInput(
            f"unknown style {config.style!r}; choose from: {', '.join(STYLES)}"
        )
    if config.error_correction not in ERROR_CORRECTION:
        raise InvalidInput(
            f"unknown error correction {config.error_correction!r}; choose from: {', '.join(EC_LEVELS)}"
        )
    if config.image_format not in FORMATS:
        raise InvalidInput(
            f"unknown format {config.image_format!r}; choose from: {', '.join(FORMATS)}"
        )
    if config.gradient not in GRADIENT_TYPES:
        raise InvalidInput(
            f"unknown gradient {config.gradient!r}; choose from: {', '.join(GRADIENT_TYPES)}"
        )
    if config.box_size < 1:
        raise InvalidInput("box_size must be >= 1")
    if config.border < 0:
        raise InvalidInput("border must be >= 0")
    if not 0 < config.logo_ratio <= MAX_LOGO_RATIO:
        raise InvalidInput(f"logo_ratio must be between 0 and {MAX_LOGO_RATIO}")

    if config.transparent_background and (
        config.frame_color is not None or config.title or config.subtitle
    ):
        raise InvalidInput("frame and text require a solid background")

    foreground = parse_color(config.foreground)
    background = parse_color(config.background)
    if foreground == background:
        raise InvalidInput("foreground and background colors must differ")
    gradient_to = parse_color(config.gradient_to) if config.gradient != GRADIENT_NONE else foreground
    if config.gradient != GRADIENT_NONE and gradient_to == foreground:
        raise InvalidInput("gradient colors must differ")

    if (
        config.gradient == GRADIENT_NONE
        and not config.transparent_background
        and contrast_ratio(foreground, background) < MIN_CONTRAST_RATIO
    ):
        warnings.append(
            "low contrast between foreground and background; the QR may be hard to scan"
        )

    return foreground, background, gradient_to, warnings


def omit_png_only_options(config: QRConfig) -> tuple[QRConfig, tuple[str, ...]]:
    """SVG output cannot carry a logo, transparency, frame or text; return a
    config with those options reset (omitted, not rejected) plus one warning
    per dropped option, so every client gets the same lenient treatment the
    web UI applies before exporting."""
    if config.image_format != SVG:
        return config, ()
    dropped: list[str] = []
    if config.logo is not None:
        dropped.append("logo is not available in SVG; omitted")
    if config.transparent_background:
        dropped.append("transparent background is not available in SVG; using solid background")
    if config.frame_color is not None:
        dropped.append("frame is not available in SVG; omitted")
    if config.title.strip():
        dropped.append("title is not available in SVG; omitted")
    if config.subtitle.strip():
        dropped.append("subtitle is not available in SVG; omitted")
    coerced = replace(
        config,
        logo=None,
        transparent_background=False,
        frame_color=None,
        title="",
        subtitle="",
    )
    return coerced, tuple(dropped)


def _build_matrix(config: QRConfig, error_correction: int) -> QRCode:
    qr = QRCode(
        version=None,
        error_correction=error_correction,
        box_size=config.box_size,
        border=config.border,
    )
    qr.add_data(config.data.strip())
    try:
        qr.make(fit=True)
    except (DataOverflowError, ValueError) as exc:
        raise InvalidInput(
            "the data is too large for a QR code; shorten it or reduce error correction"
        ) from exc
    return qr


def _color_mask(config: QRConfig, fg: tuple[int, int, int], bg: tuple[int, int, int], gt: tuple[int, int, int]):
    if config.transparent_background:
        back_color = (*bg, 0)
        fg = (*fg, 255)
        gt = (*gt, 255)
    else:
        back_color = bg
    if config.gradient == GRADIENT_LINEAR_H:
        return HorizontalGradiantColorMask(back_color=back_color, left_color=fg, right_color=gt)
    if config.gradient == GRADIENT_LINEAR_V:
        return VerticalGradiantColorMask(back_color=back_color, top_color=fg, bottom_color=gt)
    if config.gradient == GRADIENT_RADIAL:
        return RadialGradiantColorMask(back_color=back_color, center_color=fg, edge_color=gt)
    return SolidFillColorMask(front_color=fg, back_color=back_color)


def _render_png(
    qr: QRCode,
    config: QRConfig,
    fg: tuple[int, int, int],
    bg: tuple[int, int, int],
    gt: tuple[int, int, int],
) -> Image.Image:
    kwargs: dict = {
        "module_drawer": build_pil_drawer(config.style),
        "color_mask": _color_mask(config, fg, bg, gt),
    }
    if config.logo is not None:
        kwargs.update(
            embedded_image=config.logo,
            embedded_image_ratio=config.logo_ratio,
        )
    return qr.make_image(image_factory=StyledPilImage, **kwargs).get_image()


def _render_svg(
    qr: QRCode,
    config: QRConfig,
    fg: tuple[int, int, int],
    bg: tuple[int, int, int],
    warnings: list[str],
) -> bytes:
    if config.style not in SVG_CAPABLE_STYLES:
        warnings.append(
            f"style {STYLE_LABELS[config.style]} {SVG_STYLE_FALLBACK_WARNING}"
        )
    if config.gradient != GRADIENT_NONE:
        warnings.append(SVG_GRADIENT_FALLBACK_WARNING)
    img = qr.make_image(
        image_factory=SvgPathImage,
        module_drawer=build_svg_drawer(config.style),
        fill_color=fg,
        back_color=bg,
    )
    return img.to_string(encoding="unicode").encode("utf-8")


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    """Truncate text with an ellipsis when it would overflow max_width."""
    ellipsis = "\u2026"
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    while len(text) > 1:
        text = text[:-1]
        if draw.textbbox((0, 0), text + ellipsis, font=font)[2] <= max_width:
            return text + ellipsis
    return text + ellipsis


def _compose_frame(
    img: Image.Image,
    config: QRConfig,
    fg: tuple[int, int, int],
    bg: tuple[int, int, int],
) -> Image.Image:
    """Add an optional frame, title and subtitle around the QR code."""
    title = config.title.strip()
    subtitle = config.subtitle.strip()
    qr_w = img.width
    side = max(12, int(qr_w * 0.08)) if config.frame_color is not None else 8
    pad = max(10, int(qr_w * 0.05))

    title_font = ImageFont.load_default(size=max(16, qr_w // 13))
    subtitle_font = ImageFont.load_default(size=max(12, qr_w // 18))
    title_h = title_font.size + pad if title else 0
    subtitle_h = subtitle_font.size + pad if subtitle else 0
    top = title_h + pad
    bottom = subtitle_h + pad

    canvas = Image.new(
        "RGB",
        (qr_w + side * 2, qr_w + top + bottom),
        parse_color(config.frame_color) if config.frame_color is not None else bg,
    )
    canvas.paste(img, (side, top, side + qr_w, top + qr_w))

    draw = ImageDraw.Draw(canvas)
    text_width = qr_w + side * 2 - pad * 2
    if title:
        title = _fit_text(draw, title, title_font, text_width)
        bbox = draw.textbbox((0, 0), title, font=title_font)
        x = (canvas.width - (bbox[2] - bbox[0])) / 2 - bbox[0]
        draw.text((x, pad), title, font=title_font, fill=fg)
    if subtitle:
        subtitle = _fit_text(draw, subtitle, subtitle_font, text_width)
        muted = tuple(int(f * 0.6 + b * 0.4) for f, b in zip(fg, bg))
        bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
        x = (canvas.width - (bbox[2] - bbox[0])) / 2 - bbox[0]
        draw.text((x, top + qr_w + pad), subtitle, font=subtitle_font, fill=muted)
    return canvas


def _resolve_error_correction(config: QRConfig, warnings: list[str]) -> int:
    error_correction = ERROR_CORRECTION[config.error_correction]
    if config.logo is not None and config.error_correction != "H":
        warnings.append(
            "error correction raised to H so the QR stays scannable under the logo"
        )
        error_correction = constants.ERROR_CORRECT_H
    return error_correction


def generate_qr(config: QRConfig) -> QRResult:
    """Generate a QR code and return its encoded bytes.

    Raises InvalidInput for anything that cannot be generated.
    """
    config, omitted = omit_png_only_options(config)
    fg, bg, gt, warnings = _parse_config(config)
    warnings.extend(omitted)
    error_correction = _resolve_error_correction(config, warnings)
    qr = _build_matrix(config, error_correction)

    if config.image_format == PNG:
        img = _render_png(qr, config, fg, bg, gt)
        if config.frame_color is not None or config.title.strip() or config.subtitle.strip():
            img = _compose_frame(img, config, fg, bg)
        content = _png_bytes(img)
    else:
        content = _render_svg(qr, config, fg, bg, warnings)

    return QRResult(content=content, image_format=config.image_format, warnings=tuple(warnings))


def _png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def generate_previews(config: QRConfig) -> tuple[list[Preview], Preview, tuple[str, ...]]:
    """Render small PNG thumbnails for every style plus a faithful preview of the
    selected configuration.

    The gallery thumbnails always use a fixed size and no frame or text, while
    the selected preview applies the real style, frame, title, subtitle, border
    and a capped box size so it matches the final download visually.

    When SVG output is requested, PNG-only options (logo, transparency,
    frame and text) are omitted exactly like the real SVG export, thumbnails
    use the same fallbacks (plain squares for unsupported styles, solid
    foreground for gradients) and the warnings match, so the preview never
    shows something the download cannot produce.
    """
    config, omitted = omit_png_only_options(config)
    if config.style not in STYLES:
        raise InvalidInput(
            f"unknown style {config.style!r}; choose from: {', '.join(STYLES)}"
        )
    # Validate the coerced configuration (sizes, colors), so previews and
    # downloads accept and reject the same inputs.
    _parse_config(config)
    svg_mode = config.image_format == SVG
    base = replace(
        config,
        style=STYLE_SQUARE,
        image_format=PNG,
        box_size=5,
        border=2,
        frame_color=None,
        title="",
        subtitle="",
    )
    fg, bg, gt, warnings = _parse_config(base)
    warnings = list(warnings)
    warnings.extend(omitted)
    if svg_mode:
        if config.style not in SVG_CAPABLE_STYLES:
            warnings.append(
                f"style {STYLE_LABELS[config.style]} {SVG_STYLE_FALLBACK_WARNING}"
            )
        if config.gradient != GRADIENT_NONE:
            warnings.append(SVG_GRADIENT_FALLBACK_WARNING)
            gt = fg
    error_correction = _resolve_error_correction(base, warnings)
    qr = _build_matrix(base, error_correction)

    previews = [
        Preview(
            style=style,
            content=_png_bytes(
                _render_png(qr, replace(base, style=_preview_style(style, svg_mode)), fg, bg, gt)
            ),
        )
        for style in STYLES
    ]

    selected_config = replace(
        config,
        style=_preview_style(config.style, svg_mode),
        image_format=PNG,
        box_size=min(max(config.box_size, 5), 12),
    )
    selected_qr = _build_matrix(selected_config, error_correction)
    selected_img = _render_png(selected_qr, selected_config, fg, bg, gt)
    if (
        not selected_config.transparent_background
        and (
            selected_config.frame_color is not None
            or selected_config.title.strip()
            or selected_config.subtitle.strip()
        )
    ):
        selected_img = _compose_frame(selected_img, selected_config, fg, bg)
    selected = Preview(style=config.style, content=_png_bytes(selected_img))

    return previews, selected, tuple(warnings)
