from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


@dataclass
class DCRow:
    name: str
    operator: str
    street: str
    city: str
    state: str
    postal_code: str
    country: str
    full_address: str
    source: str
    notes: str


def norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def split_zip_city(line: str) -> Tuple[str, str]:
    """
    Try to parse "60616 ChicagoDigital Realty" or "60607 Chicago" -> ("60616", "Chicago")
    """
    line = norm_space(line)
    if not line:
        return "", ""
    m = re.match(r"^(\d{5})(.*)$", line)
    if not m:
        return "", line
    zipc = m.group(1)
    rest = norm_space(m.group(2))
    return zipc, rest


def strip_trailing_operator(city_part: str, operator: str) -> str:
    if not city_part:
        return ""
    if not operator:
        return city_part
    # Sometimes the operator is concatenated with no delimiter: "ChicagoDigital Realty"
    op = operator.strip()
    # Remove if appears at end (case-insensitive), with or without space.
    for candidate in (op, op.replace(" ", "")):
        if city_part.lower().endswith(candidate.lower()):
            return norm_space(city_part[: -len(candidate)])
    return city_part


def looks_like_operator(line: str) -> bool:
    # Heuristic: operators tend to be short and not start with numbers.
    line = norm_space(line)
    if not line:
        return False
    if ZIP_RE.search(line):
        return False
    if any(line.lower().startswith(x) for x in ("data center map", "data centers", "below you will", "to find other")):
        return False
    return True


def parse_blocks(lines: List[str]) -> List[DCRow]:
    rows: List[DCRow] = []
    i = 0
    while i < len(lines) - 2:
        name = lines[i]
        op = lines[i + 1] if i + 1 < len(lines) else ""
        street = lines[i + 2] if i + 2 < len(lines) else ""
        zip_city = lines[i + 3] if i + 3 < len(lines) else ""

        # Validate basic block pattern
        if not name or not looks_like_operator(op) or ZIP_RE.search(name):
            i += 1
            continue

        zipc, city_part = split_zip_city(zip_city)
        city = strip_trailing_operator(city_part, op)

        # Some entries have missing zip line, or the zip/city is on the next line
        if not zipc and i + 4 < len(lines) and ZIP_RE.search(lines[i + 4] or ""):
            zipc, city_part = split_zip_city(lines[i + 4])
            city = strip_trailing_operator(city_part, op)
            zip_city = lines[i + 4]
            i_advance = 5
        else:
            i_advance = 4

        street_norm = norm_space(street)
        op_norm = norm_space(op)
        name_norm = norm_space(name)

        # If street line itself includes city/state/zip, keep as full_address as-is.
        full_address = street_norm
        notes = ""

        # If we have a street + (zip or city), build a clean full address.
        if street_norm:
            parts = [street_norm]
            if city:
                parts.append(f"{city}, IL")
            if zipc:
                parts.append(zipc)
            parts.append("USA")
            full_address = ", ".join([p for p in parts if p])

        # Filter out obvious non-address placeholders
        if street_norm.lower() in ("tba", "within elk grove village", "within aurora, 41 miles from chicago"):
            notes = f"address_placeholder:{street_norm}"
            full_address = f"{city}, IL, USA" if city else "Illinois, USA"
            street_norm = ""

        rows.append(
            DCRow(
                name=name_norm,
                operator=op_norm,
                street=street_norm,
                city=city,
                state="IL",
                postal_code=zipc,
                country="USA",
                full_address=full_address,
                source="DataCenterMap (user-provided paste)",
                notes=notes,
            )
        )
        i += i_advance

    # De-duplicate by (name, street, postal_code)
    seen = set()
    out: List[DCRow] = []
    for r in rows:
        k = (r.name.lower(), r.street.lower(), r.postal_code)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Source text file")
    ap.add_argument("--output", required=True, help="Output CSV path")
    args = ap.parse_args()

    text = Path(args.input).read_text(errors="ignore")
    raw_lines = [norm_space(l) for l in text.splitlines()]
    lines = [l for l in raw_lines if l]

    rows = parse_blocks(lines)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "name",
                "operator",
                "street",
                "city",
                "state",
                "postal_code",
                "country",
                "full_address",
                "source",
                "notes",
            ],
        )
        w.writeheader()
        for r in rows:
            w.writerow(r.__dict__)

    print(f"✅ Parsed rows: {len(rows)}")
    print(f"✅ Wrote CSV: {out_path}")


if __name__ == "__main__":
    main()

