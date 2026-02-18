from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple


IL_BBOX = (-91.52, 36.97, -87.0, 42.51)  # west, south, east, north


@dataclass(frozen=True)
class Bounds:
    west: float
    south: float
    east: float
    north: float


def parse_bbox(s: Optional[str]) -> Bounds:
    if not s:
        return Bounds(*IL_BBOX)
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be 'west,south,east,north'")
    w, so, e, n = map(float, parts)
    return Bounds(w, so, e, n)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="Start date/time (YYYY-MM-DD or ISO8601)")
    ap.add_argument("--end", required=True, help="End date/time (YYYY-MM-DD or ISO8601)")
    ap.add_argument("--bbox", default=None, help="west,south,east,north (default: Illinois rough bbox)")
    ap.add_argument("--out_dir", required=True, help="Download/cache directory for GeoTIFFs")
    ap.add_argument("--max_granules", type=int, default=2000, help="Limit per chunk for search/download")
    ap.add_argument(
        "--chunk_days",
        type=int,
        default=30,
        help="Search/download in time chunks (days). Useful for multi-year runs. (default: 30)",
    )
    ap.add_argument(
        "--short_name",
        default="ECO_L2T_LSTE",
        help="CMR short_name (default ECO_L2T_LSTE, ECOSTRESS tiled LST&E ~70m)",
    )
    ap.add_argument(
        "--auth",
        default="environment",
        choices=["environment", "netrc", "token", "none"],
        help=(
            "Auth strategy: "
            "environment uses EARTHDATA_USERNAME/EARTHDATA_PASSWORD env vars; "
            "netrc uses ~/.netrc; "
            "token uses EARTHDATA_TOKEN env var; "
            "none only searches and prints results."
        ),
    )
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    bbox = parse_bbox(args.bbox)
    chunk_days = int(args.chunk_days)
    if chunk_days <= 0:
        raise SystemExit("--chunk_days must be > 0")

    try:
        import earthaccess  # type: ignore
    except Exception as e:
        raise SystemExit(
            "Missing dependency 'earthaccess'. Install it inside analysis/.venv:\n"
            "  pip install earthaccess\n"
            f"Original error: {e}"
        )

    if args.auth != "none":
        # Non-interactive login. Prefer token or netrc to avoid passwords.
        try:
            if args.auth == "environment":
                # Requires:
                #   export EARTHDATA_USERNAME=...
                #   export EARTHDATA_PASSWORD=...
                earthaccess.login(strategy="environment")
            elif args.auth == "netrc":
                # Requires ~/.netrc with Earthdata Login machine entry.
                earthaccess.login(strategy="netrc")
            elif args.auth == "token":
                # Requires:
                #   export EARTHDATA_TOKEN=...
                import os

                tok = (os.environ.get("EARTHDATA_TOKEN") or "").strip()
                if not tok:
                    raise SystemExit("EARTHDATA_TOKEN is not set (required for --auth token).")
                earthaccess.login(strategy="token", token=tok)
        except SystemExit:
            raise
        except Exception as e:
            raise SystemExit(
                "Earthdata login failed.\n"
                "- For --auth environment: set EARTHDATA_USERNAME and EARTHDATA_PASSWORD\n"
                "- For --auth token: set EARTHDATA_TOKEN\n"
                "- For --auth netrc: configure ~/.netrc\n"
                f"Original error: {e}"
            )

    def _parse_dt(s: str) -> datetime:
        s = (s or "").strip()
        if "T" in s:
            # ISO timestamp
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc)
        # YYYY-MM-DD
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)

    start_dt = _parse_dt(str(args.start))
    end_dt = _parse_dt(str(args.end))
    if end_dt <= start_dt:
        raise SystemExit("end must be after start")

    total_found = 0
    total_downloaded = 0

    cur = start_dt
    while cur < end_dt:
        nxt = min(end_dt, cur + timedelta(days=chunk_days))
        temporal = (cur.date().isoformat(), nxt.date().isoformat())
        results = earthaccess.search_data(
            short_name=args.short_name,
            bounding_box=(bbox.west, bbox.south, bbox.east, bbox.north),
            temporal=temporal,
            count=int(args.max_granules),
        )
        total_found += len(results)
        print(f"✅ Found granules: {len(results)} for {args.short_name} in bbox for {temporal[0]} → {temporal[1]}")
        if args.auth == "none":
            cur = nxt
            continue

        if results:
            paths = earthaccess.download(results, local_path=str(out_dir))
            flat: List[str] = []
            for p in paths:
                if isinstance(p, (list, tuple)):
                    flat.extend([str(x) for x in p])
                else:
                    flat.append(str(p))
            existing = [str(p) for p in map(Path, flat) if Path(p).exists()]
            total_downloaded += len(existing)

        cur = nxt

    if args.auth == "none":
        print("Auth disabled (search-only). Re-run with --auth environment to download.")
        return

    # Summarize downloads
    tif = [p for p in out_dir.glob("*.tif")]
    print(f"✅ Total granules found (sum of chunks): {total_found}")
    print(f"✅ Total files downloaded (sum of chunks): {total_downloaded}")
    print(f"✅ GeoTIFFs currently in cache: {len(list(tif))} at {out_dir}")


if __name__ == "__main__":
    main()

