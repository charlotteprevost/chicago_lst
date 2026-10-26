import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_CSV = ROOT / "data" / "chicago_data_centers_183.csv"


def _read_rows():
    with DATA_CSV.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        return reader.fieldnames or [], rows


def test_data_csv_has_expected_schema():
    fields, _ = _read_rows()
    expected = {
        "name",
        "operator",
        "city",
        "state",
        "full_address",
        "went_live_date",
        "went_live_date_precision",
        "went_live_source_url",
        "went_live_status",
    }
    assert expected.issubset(set(fields))


def test_went_live_status_domain():
    _, rows = _read_rows()
    allowed = {"verified", "needs_research", ""}
    statuses = {(r.get("went_live_status") or "").strip() for r in rows}
    assert statuses.issubset(allowed)
    assert "needs_research" in statuses


def test_verified_rows_have_date_and_source():
    _, rows = _read_rows()
    verified = [r for r in rows if (r.get("went_live_status") or "").strip() == "verified"]
    assert len(verified) >= 13
    for row in verified:
        assert (row.get("went_live_date") or "").strip()
        assert (row.get("went_live_source_url") or "").strip()


def test_dc_effect_geojson_opening_date_for_verified_sites():
    import json

    geo = json.loads((ROOT / "data" / "dc_effect_cumulative.geojson").read_text(encoding="utf-8"))
    by_name: dict[str, object] = {}
    for feat in geo.get("features") or []:
        props = feat.get("properties") or {}
        name = str(props.get("site_name") or "").strip().lower()
        if name:
            by_name[name] = props.get("opening_date")
    required = {
        "qts chicago 1 dc1",
        "coresite chicago (ch2)",
        "gdc - chicago ch1 data center",
        "gdc - chicago ch2 data center",
        "equinix ch3",
        "stream dc chicago i",
        "stream dc chicago ii",
        "equinix ch2",
        "2200 busse road (ch1)",
        "2299 busse road (ch2)",
        "aligned ord-01",
        "serverfarm chicago data center ch1",
        "skybox chicago i",
    }
    missing = required - set(by_name)
    assert not missing, f"verified site_name missing from GeoJSON: {sorted(missing)}"
    blank = [n for n in required if not by_name.get(n)]
    assert not blank, f"verified site_name has null opening_date: {sorted(blank)}"


def test_no_duplicate_name_address_pairs():
    _, rows = _read_rows()
    seen = set()
    for row in rows:
        key = (
            (row.get("name") or "").strip().lower(),
            (row.get("full_address") or "").strip().lower(),
        )
        if not key[0] and not key[1]:
            continue
        assert key not in seen
        seen.add(key)
