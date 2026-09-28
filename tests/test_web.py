from __future__ import annotations

from pathlib import Path

WEB_DIR = Path(__file__).resolve().parents[1] / "web"


def test_svg_form_omits_png_only_options() -> None:
    """Regression: switching to SVG must not send raster-only options.

    The UI keeps the user's PNG configuration in state but must omit logo,
    frame and text when requesting SVG, and send transparency as configured
    (native in SVG), otherwise the export would not mirror the preview.
    """
    form_js = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    svg_branch = form_js.split('if (state.format === "svg")', 1)
    assert len(svg_branch) == 2, "buildFormData must branch on SVG format"
    branch = svg_branch[1].split("return form;", 1)[0]
    assert 'form.append("logo"' not in branch
    assert 'form.append("transparent_background", state.transparent)' in branch
    assert '"frame_color", ""' in branch
    assert '"title", ""' in branch
    assert '"subtitle", ""' in branch


def test_logo_editor_fields_sent_only_when_set() -> None:
    """The crop and rotation travel with the original file, only when set."""
    form_js = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert 'if (state.logoCrop) form.append("logo_crop", JSON.stringify(state.logoCrop));' in form_js
    assert 'if (state.logoRotate) form.append("logo_rotate", String(state.logoRotate));' in form_js
    # the SVG branch must still omit the logo entirely (and its metadata)
    branch = form_js.split('if (state.format === "svg")', 1)[1].split("return form;", 1)[0]
    assert "logo_crop" not in branch
    assert "logo_rotate" not in branch
