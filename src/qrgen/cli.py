"""Command line interface for the QR generator."""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys
from pathlib import Path

from qrgen.core import (
    DEFAULT_EC,
    EC_LEVELS,
    FORMATS,
    GRADIENT_TYPES,
    PNG,
    PRESETS,
    STYLE_INFO,
    STYLES,
    InvalidInput,
    QRConfig,
    generate_qr,
    validate_logo,
)
from qrgen.inputs import parse_file, parse_text
from qrgen.passwords import hash_password

__version__ = "0.3.0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qrgen",
        description="Generate personalizable QR codes from a link, text or file.",
    )
    parser.add_argument("data", nargs="?", help="the link or text to encode")
    parser.add_argument(
        "--file", metavar="PATH", help="read UTF-8 text content from a file"
    )
    parser.add_argument(
        "-o", "--output", default="qr.png", help="output file path (default: qr.png)"
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=FORMATS,
        help="output format (default: inferred from the output extension, else png)",
    )
    parser.add_argument(
        "--style",
        choices=STYLES,
        default="square",
        help="module style of the QR code (default: square)",
    )
    parser.add_argument("--fg", "--foreground", default="#000000", help="module color, hex or name (default: #000000)")
    parser.add_argument("--bg", "--background", default="#ffffff", help="background color, hex or name (default: #ffffff)")
    parser.add_argument(
        "--gradient",
        choices=GRADIENT_TYPES,
        default="none",
        help="gradient fill for the modules (default: none)",
    )
    parser.add_argument(
        "--gradient-to",
        default=None,
        help="second color for the gradient (default: foreground)",
    )
    parser.add_argument(
        "--transparent-background",
        action="store_true",
        help="transparent background (PNG only)",
    )
    parser.add_argument(
        "-e",
        "--error-correction",
        choices=EC_LEVELS,
        default=DEFAULT_EC,
        help="error correction level (default: M)",
    )
    parser.add_argument("--box-size", type=int, default=10, help="pixels per module (default: 10)")
    parser.add_argument("--border", type=int, default=4, help="quiet zone width in modules (default: 4)")
    parser.add_argument("--logo", metavar="PATH", help="image to embed in the center (forces error correction H)")
    parser.add_argument(
        "--logo-ratio", type=float, default=0.2, help="logo size as a ratio of the QR width (default: 0.2)"
    )
    parser.add_argument(
        "--frame", metavar="COLOR", default=None, help="draw a frame around the QR (PNG only)"
    )
    parser.add_argument("--title", default="", help="title text above the QR (PNG only)")
    parser.add_argument("--subtitle", default="", help="subtitle text below the QR (PNG only)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def run_generate(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if args.file and args.data:
        print("error: provide data or --file, not both", file=sys.stderr)
        return 2
    if args.file:
        try:
            raw = Path(args.file).read_bytes()
        except OSError as exc:
            print(f"error: cannot read file {args.file!r}: {exc}", file=sys.stderr)
            return 2
        try:
            payload = parse_file(args.file, raw)
        except InvalidInput as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    elif args.data:
        try:
            payload = parse_text(args.data)
        except InvalidInput as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    else:
        print("error: provide data or --file", file=sys.stderr)
        return 2

    image_format = args.format
    if image_format is None:
        suffix = Path(args.output).suffix.lower().lstrip(".")
        image_format = suffix if suffix in FORMATS else PNG

    logo = None
    if args.logo:
        from PIL import Image

        try:
            logo = Image.open(args.logo)
            validate_logo(logo)
            logo.load()
        except (OSError, ValueError) as exc:
            print(f"error: cannot read logo image {args.logo!r}: {exc}", file=sys.stderr)
            return 2

    config = QRConfig(
        data=payload.content,
        style=args.style,
        foreground=args.fg,
        background=args.bg,
        gradient=args.gradient,
        gradient_to=args.gradient_to or args.fg,
        error_correction=args.error_correction,
        box_size=args.box_size,
        border=args.border,
        image_format=image_format,
        logo=logo,
        logo_ratio=args.logo_ratio,
        transparent_background=args.transparent_background,
        frame_color=args.frame,
        title=args.title,
        subtitle=args.subtitle,
    )

    try:
        result = generate_qr(config)
    except InvalidInput as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_bytes(result.content)
    print(f"QR code written to {args.output}")
    return 0


def run_styles() -> int:
    print("Estilos disponibles:")
    for info in STYLE_INFO:
        formats = ", ".join(f for f in ("png", "svg") if getattr(info, f))
        print(f"  {info.id:<16} {info.label:<20} {formats}")
    print("\nCombinaciones predefinidas:")
    for preset in PRESETS:
        print(f"  {preset['id']:<12} {preset['label']:<12} style={preset['style']}")
    return 0


def run_hash_password(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="qrgen hash-password")
    parser.add_argument("password", nargs="?", help="password (prompted if omitted)")
    parser.add_argument("--iterations", type=int, default=200_000)
    args = parser.parse_args(argv)
    password = args.password or getpass.getpass("Password: ")
    print(hash_password(password, args.iterations))
    return 0


def run_admin_setup(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="qrgen admin-setup",
        description="Print the .env variables needed to enable the admin panel.",
    )
    parser.add_argument("password", nargs="?", help="admin password (prompted if omitted)")
    parser.add_argument(
        "--username", default="admin", help="admin username (default: admin)"
    )
    parser.add_argument(
        "--session-secret",
        default=None,
        help="session secret (generated randomly if omitted)",
    )
    args = parser.parse_args(argv)
    password = args.password
    if password is None:
        password = getpass.getpass("Password: ")
    if not password:
        print("error: password must not be empty", file=sys.stderr)
        return 2
    if args.session_secret:
        session_secret = args.session_secret
        print(f"ADMIN_USERNAME={args.username}")
        print(f"ADMIN_PASSWORD_HASH={hash_password(password)}")
        print(f"SESSION_SECRET={session_secret}")
        return 0
    session_secret = secrets.token_urlsafe(32)
    print("# Add these variables to .env to enable the admin panel")
    print(f"ADMIN_USERNAME={args.username}")
    print(f"ADMIN_PASSWORD_HASH={hash_password(password)}")
    print(f"SESSION_SECRET={session_secret}")
    print("# (SESSION_SECRET was generated randomly; keep it secret)")
    return 0


def run_migrate(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="qrgen migrate")
    parser.add_argument("--wait", type=float, default=30, help="seconds to wait for the database")
    args = parser.parse_args(argv)
    os.environ.setdefault("QRGEN_MIGRATE_WAIT_SECONDS", str(args.wait))
    from qrgen import migrate

    return migrate.run_migrations()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "serve":
        from qrgen.server import run

        return run()
    if argv and argv[0] == "styles":
        return run_styles()
    if argv and argv[0] == "hash-password":
        return run_hash_password(argv[1:])
    if argv and argv[0] == "admin-setup":
        return run_admin_setup(argv[1:])
    if argv and argv[0] == "migrate":
        return run_migrate(argv[1:])
    return run_generate(argv)


if __name__ == "__main__":
    raise SystemExit(main())
