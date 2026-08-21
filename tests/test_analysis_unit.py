from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "analysis"


def _load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


parser_mod = _load_module("parse_dc", ANALYSIS / "12_parse_chicago_data_centers.py")
enrich_mod = _load_module("enrich_opening_dates", ANALYSIS / "14_enrich_data_center_opening_dates.py")


def test_norm_space_and_split_zip_city():
    assert parser_mod.norm_space("  a   b  ") == "a b"
    assert parser_mod.split_zip_city("60616 ChicagoDigital Realty") == ("60616", "ChicagoDigital Realty")
    assert parser_mod.split_zip_city("No zip here") == ("", "No zip here")


def test_strip_trailing_operator():
    city = parser_mod.strip_trailing_operator("ChicagoDigital Realty", "Digital Realty")
    assert city == "Chicago"
    assert parser_mod.strip_trailing_operator("Chicago", "Some Operator") == "Chicago"


def test_parse_blocks_parses_and_dedupes():
    lines = [
        "Alpha DC",
        "Operator A",
        "123 Main St",
        "60601 ChicagoOperator A",
        "Alpha DC",
        "Operator A",
        "123 Main St",
        "60601 ChicagoOperator A",
    ]
    rows = parser_mod.parse_blocks(lines)
    assert len(rows) == 1
    row = rows[0]
    assert row.name == "Alpha DC"
    assert row.operator == "Operator A"
    assert row.state == "IL"
    assert row.country == "USA"
    assert "123 Main St" in row.full_address


def test_normalize_url():
    assert enrich_mod.normalize_url("") == ""
    assert enrich_mod.normalize_url("example.com/page") == "https://example.com/page"
    assert enrich_mod.normalize_url("https://example.com") == "https://example.com"


def test_webmercator_tile_bounds_and_chicago_tile():
    pytest.importorskip("numpy")
    bake_mod = _load_module("bake_xyz", ANALYSIS / "26_bake_ecostress_xyz.py")
    minx, miny, maxx, maxy = bake_mod.tile_bounds_3857(0, 0, 0)
    assert minx < 0 < maxx
    assert miny < 0 < maxy
    x, y = bake_mod.lonlat_to_tile(-87.6298, 41.8781, 9)
    assert x == 131
    assert 180 <= y <= 190


def test_inferno_rgba_nodata_is_transparent(tmp_path):
    pytest.importorskip("numpy")
    import numpy as np

    bake_mod = _load_module("bake_xyz_rgba", ANALYSIS / "26_bake_ecostress_xyz.py")
    values = np.array([[0.0, 45.0], [22.5, 0.0]], dtype=np.float32)
    nodata = np.array([[False, False], [False, True]])
    rgba = bake_mod.inferno_rgba(values, 0.0, 45.0, nodata)
    assert rgba.shape == (2, 2, 4)
    assert rgba[1, 1, 3] == 0
    assert rgba[0, 0, 3] == 255
    out = tmp_path / "t.png"
    bake_mod.write_png_rgba(out, rgba)
    assert out.stat().st_size > 32
    meta = bake_mod.update_meta_json(
        tmp_path / "meta.json",
        tiles_url="https://example.com/{z}/{x}/{y}.png",
        scene_time="2025-07-31T18:05:13Z",
        minzoom=6,
        maxzoom=10,
    )
    assert "{z}" in meta["tiles_url"]
    assert meta["scene_time"] == "2025-07-31T18:05:13Z"
