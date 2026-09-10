"""Resolution of the directory that holds static web assets."""

from __future__ import annotations

import os
from pathlib import Path


def resolve_web_dir() -> Path:
    """Directory that holds the static web UI.

    Defaults to the repository layout (src/qrgen -> project root) and can be
    overridden with QRGEN_WEB_DIR, which packaged installs (e.g. the Docker
    image) must set because the package lives inside site-packages.
    """
    override = os.environ.get("QRGEN_WEB_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "web"


WEB_DIR = resolve_web_dir()
