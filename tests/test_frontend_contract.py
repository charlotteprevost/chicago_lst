from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_config_has_required_dataset_ids_and_defaults():
    cfg = _read(FRONTEND / "config.js")
    assert "viirs_night_global" in cfg
    assert "ecostress_il_highres" in cfg
    assert 'defaultDatasetId: "ecostress_il_highres"' in cfg
    assert 'fallbackDatasetId: "viirs_night_global"' in cfg


def test_config_has_required_overlay_sources():
    cfg = _read(FRONTEND / "config.js")
    assert "../data/aoi_risk_latest.geojson" in cfg
    assert "../data/chicago_data_centers_183.geojson" in cfg
    assert "../data/dc_effect_cumulative.geojson" in cfg


def test_main_uses_fallback_and_titiler_paths():
    main_js = _read(FRONTEND / "main.js")
    assert "switchToFallback" in main_js
    assert "makeTitilerLayer" in main_js
    assert "buildTitilerTileUrlTemplate" in main_js
    assert "ecostress_highres_latest.json" in main_js


def test_main_exposes_expected_overlay_loaders():
    main_js = _read(FRONTEND / "main.js")
    for fn_name in ("loadRiskLayer", "loadDataCentersLayer", "loadEffectLayer"):
        assert re.search(rf"function\s+{fn_name}\s*\(", main_js)
