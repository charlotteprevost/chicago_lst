from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "analysis"
DATA = ROOT / "data"


def _load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_chicago_aoi_is_metric_and_contains_dc_points():
    aoi = json.loads((DATA / "chicago_dc_aoi.json").read_text(encoding="utf-8"))
    assert aoi["clip_crs"] == "EPSG:32616"
    assert aoi["crs"] == "EPSG:4326"
    assert aoi["area_km2"] > 100
    assert aoi["area_km2"] < 20000
    assert aoi["n_dropped_null"] >= 1
    west, south, east, north = aoi["west"], aoi["south"], aoi["east"], aoi["north"]
    assert east > west
    assert north > south

    raw = json.loads((DATA / "chicago_data_centers_183.geojson").read_text(encoding="utf-8"))
    n_pts = 0
    for feat in raw["features"]:
        geom = feat.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        lon, lat = geom["coordinates"][:2]
        n_pts += 1
        assert west <= lon <= east
        assert south <= lat <= north
    assert n_pts == aoi["n_points"]
    assert n_pts >= 3


def test_convex_hull_simple_square():
    aoi_mod = _load_module("chicago_dc_aoi_builder", ANALYSIS / "27_make_chicago_dc_aoi.py")
    hull = aoi_mod.convex_hull([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.5, 0.5)])
    assert len(hull) == 4


def test_same_pass_grouping_picks_latest_and_keeps_siblings(tmp_path):
    pub = _load_module("publish_ecostress", ANALYSIS / "25_publish_latest_ecostress_cog.py")
    names = [
        "ECO_L2T_LSTE_20250101T000000_A_LST.tif",
        "ECO_L2T_LSTE_20250731T180421_A_LST.tif",
        "ECO_L2T_LSTE_20250731T180513_A_LST.tif",
        "ECO_L2T_LSTE_20250731T180513_B_LST.tif",
    ]
    files = []
    for name in names:
        p = tmp_path / name
        p.write_bytes(b"")
        files.append(p)
    groups = pub.group_by_timestamp(files, r"(\d{8}T\d{6})", "%Y%m%dT%H%M%S")
    assert len(groups) == 2
    latest_dt, latest_tiles = pub.choose_latest_group(files, r"(\d{8}T\d{6})", "%Y%m%dT%H%M%S")
    assert latest_dt.strftime("%Y%m%dT%H%M%S") in {"20250731T180421", "20250731T180513"}
    assert len(latest_tiles) == 3


def test_study_runner_wires_collapse_and_coverage():
    text = (ANALYSIS / "23_run_il_ecostress_dc_study.py").read_text(encoding="utf-8")
    assert "30_collapse_and_filter_observations.py" in text
    assert "33_export_coverage_tables.py" in text
    assert "chicago_dc_aoi.json" in text
