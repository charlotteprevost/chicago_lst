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
    assert len(verified) >= 1
    for row in verified:
        assert (row.get("went_live_date") or "").strip()
        assert (row.get("went_live_source_url") or "").strip()


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
