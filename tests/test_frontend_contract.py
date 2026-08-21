from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_config_has_required_dataset_ids_and_defaults():
    cfg = _read(FRONTEND / "config.js")
    assert "viirs_night_global" not in cfg
    assert "fallbackDatasetId" not in cfg
    assert "ecostress_il_highres" in cfg
    assert 'defaultDatasetId: "ecostress_il_highres"' in cfg
    assert "chicago_dc_aoi.json" in cfg
    assert "zoom: 10" in cfg


def test_config_has_required_overlay_sources():
    cfg = _read(FRONTEND / "config.js")
    assert "../data/aoi_risk_latest.geojson" in cfg
    assert "../data/chicago_data_centers_183.geojson" in cfg
    assert "../data/dc_effect_cumulative.geojson" in cfg


def test_main_uses_titiler_paths_and_chicago_clamp():
    main_js = _read(FRONTEND / "main.js")
    assert "switchToFallback" not in main_js
    assert "markThermalUnavailable" in main_js
    assert "Temperature tiles unavailable" in main_js
    assert "makeTitilerLayer" in main_js
    assert "buildTitilerTileUrlTemplate" in main_js
    assert "ecostress_highres_latest.json" in main_js
    assert "probeXyzTemplate" in main_js
    assert "tiles_url" in main_js
    assert "wakeTitiler" in main_js
    assert "updateWhenIdle" in main_js
    assert "maxBoundsViscosity" in main_js
    assert "panInsideBounds" not in main_js
    assert "viirs_night_global" not in main_js
    assert "GibsTimeLayer" not in main_js


def test_main_exposes_expected_overlay_loaders():
    main_js = _read(FRONTEND / "main.js")
    for fn_name in ("fetchRiskData", "loadDataCentersLayer", "loadEffectLayer"):
        assert re.search(rf"function\s+{fn_name}\s*\(", main_js)


def test_html_is_ecostress_only_without_timeline():
    html = _read(FRONTEND / "index.html")
    assert 'id="timeControls"' not in html
    assert 'id="dataset"' not in html
    assert "Chicago night heat" in html
    assert "plain English" not in html
    assert 'id="overlayDC"' in html
    assert 'id="metricCoverage"' in html
    assert re.search(r'id="overlayRisk"[^>]*checked', html) is None
    assert re.search(r'id="overlayEffect"[^>]*checked', html) is None
    assert re.search(r'id="overlayDC"[^>]*checked', html)


def test_css_viewport_shell_and_hidden_override():
    css = _read(FRONTEND / "css" / "style.css")
    assert "flex-direction: column" in css
    assert "calc(100% - 58px)" not in css
    assert "[hidden]" in css
    assert "display: none !important" in css
    assert "max-height: min(40vh, calc(100% - 20px))" in css


def test_help_starts_closed():
    main_js = _read(FRONTEND / "main.js")
    assert "setHelpPanelOpen(false)" in main_js
    assert "setHelpPanelOpen(true)" not in main_js


def test_pages_workflow_bakes_xyz_tiles():
    pages = _read(ROOT / ".github" / "workflows" / "pages.yml")
    assert "26_bake_ecostress_xyz.py" in pages
    assert "site/data/tiles/ecostress" in pages
    assert "chicago_dc_aoi.json" in pages
    assert "--min-zoom 9" in pages
