from __future__ import annotations

from pathlib import Path

WEB_DIR = Path(__file__).resolve().parents[1] / "web"


def test_svg_form_omits_png_only_options() -> None:
    """Regression: switching to SVG must not send PNG-only options.

    The UI keeps the user's PNG configuration in state but must omit logo,
    transparency, frame and text when requesting SVG, otherwise /api/generate
    answers 400 (see core._parse_config).
    """
    form_js = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    svg_branch = form_js.split('if (state.format === "svg")', 1)
    assert len(svg_branch) == 2, "buildFormData must branch on SVG format"
    branch = svg_branch[1].split("return form;", 1)[0]
    assert 'form.append("logo"' not in branch
    assert '"transparent_background", false' in branch
    assert '"frame_color", ""' in branch
    assert '"title", ""' in branch
    assert '"subtitle", ""' in branch
