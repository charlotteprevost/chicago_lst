from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


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
