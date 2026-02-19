from __future__ import annotations

import argparse
import csv
from pathlib import Path


REQUIRED_NEW_COLUMNS = [
    "went_live_date",
    "went_live_date_precision",
    "went_live_source_url",
    "went_live_source_title",
    "went_live_source_notes",
    "went_live_status",
]


def read_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]
    return fieldnames, rows


def write_csv_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fieldnames})


def normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return f"https://{u}"


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Add went-live date/source tracking columns to chicago_data_centers_183.csv "
            "and generate a research queue for unresolved rows."
        )
    )
    ap.add_argument(
        "--input-csv",
        default="../data/chicago_data_centers_183.csv",
        help="Path to data center CSV to enrich.",
    )
    ap.add_argument(
        "--manual-seeds-csv",
        default="data_center_opening_dates_manual.csv",
        help=(
            "Optional manual seed CSV with columns: "
            "name,operator,went_live_date,went_live_date_precision,went_live_source_url,"
            "went_live_source_title,went_live_source_notes."
        ),
    )
    ap.add_argument(
        "--queue-out",
        default="opening_date_research_queue.csv",
        help="Output CSV listing unresolved rows and suggested search queries.",
    )
    args = ap.parse_args()

    input_csv = Path(args.input_csv).expanduser().resolve()
    manual_csv = Path(args.manual_seeds_csv).expanduser().resolve()
    queue_out = Path(args.queue_out).expanduser().resolve()

    if not input_csv.exists():
        raise SystemExit(f"Input CSV not found: {input_csv}")

    base_fields, rows = read_csv_rows(input_csv)

    # Extend schema if needed.
    fields = list(base_fields)
    for col in REQUIRED_NEW_COLUMNS:
        if col not in fields:
            fields.append(col)

    # Optional manual seed map.
    seed_map: dict[tuple[str, str], dict[str, str]] = {}
    if manual_csv.exists():
        _, seed_rows = read_csv_rows(manual_csv)
        for s in seed_rows:
            name = (s.get("name") or "").strip().lower()
            op = (s.get("operator") or "").strip().lower()
            if not name:
                continue
            seed_map[(name, op)] = s

    unresolved: list[dict[str, str]] = []

    for r in rows:
        name = (r.get("name") or "").strip()
        operator = (r.get("operator") or "").strip()
        key = (name.lower(), operator.lower())

        # Keep existing values if already filled.
        has_date = bool((r.get("went_live_date") or "").strip())
        has_source = bool((r.get("went_live_source_url") or "").strip())

        if key in seed_map and (not has_date or not has_source):
            seed = seed_map[key]
            r["went_live_date"] = (seed.get("went_live_date") or r.get("went_live_date") or "").strip()
            r["went_live_date_precision"] = (
                seed.get("went_live_date_precision") or r.get("went_live_date_precision") or "year"
            ).strip()
            r["went_live_source_url"] = normalize_url(
                seed.get("went_live_source_url") or r.get("went_live_source_url") or ""
            )
            r["went_live_source_title"] = (
                seed.get("went_live_source_title") or r.get("went_live_source_title") or ""
            ).strip()
            r["went_live_source_notes"] = (
                seed.get("went_live_source_notes") or r.get("went_live_source_notes") or ""
            ).strip()

        # Set status deterministically.
        if (r.get("went_live_date") or "").strip() and (r.get("went_live_source_url") or "").strip():
            r["went_live_status"] = "verified"
        else:
            r["went_live_status"] = "needs_research"
            unresolved.append(
                {
                    "name": name,
                    "operator": operator,
                    "city": (r.get("city") or "").strip(),
                    "state": (r.get("state") or "").strip(),
                    "full_address": (r.get("full_address") or "").strip(),
                    "suggested_query_1": f"\"{name}\" \"{operator}\" \"opening\"",
                    "suggested_query_2": f"\"{name}\" \"{(r.get('city') or '').strip()}\" \"data center\" \"announced\"",
                    "suggested_query_3": f"\"{operator}\" \"{(r.get('city') or '').strip()}\" \"facility\" \"commissioned\"",
                }
            )

    write_csv_rows(input_csv, fields, rows)
    print(f"Updated CSV with date/source columns: {input_csv}")

    queue_fields = [
        "name",
        "operator",
        "city",
        "state",
        "full_address",
        "suggested_query_1",
        "suggested_query_2",
        "suggested_query_3",
    ]
    queue_out.parent.mkdir(parents=True, exist_ok=True)
    write_csv_rows(queue_out, queue_fields, unresolved)
    print(f"Wrote research queue ({len(unresolved)} unresolved): {queue_out}")


if __name__ == "__main__":
    main()

