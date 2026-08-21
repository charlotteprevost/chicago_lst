from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def _write_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    print(f"✅ Wrote: {path} ({len(df)} rows)")


def coverage_from_collapsed(
    usable: pd.DataFrame,
    n_dc_sites: int,
    buffer_m: float = 500.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = usable.copy()
    if "is_usable" in df.columns:
        df = df[df["is_usable"] == True].copy()  # noqa: E712
    if "is_data_center" in df.columns:
        df = df[pd.to_numeric(df["is_data_center"], errors="coerce") == 1].copy()
    if "buffer_m" in df.columns:
        df = df[pd.to_numeric(df["buffer_m"], errors="coerce") == float(buffer_m)].copy()
    if df.empty:
        raise SystemExit("No usable DC rows at the requested buffer after collapse.")

    df["dt"] = pd.to_datetime(df["dt"] if "dt" in df.columns else df["date"], errors="coerce", utc=True)
    if "date" not in df.columns:
        df["date"] = df["dt"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    n_nights = int(df["date"].nunique())
    by_pass = (
        df.groupby("date", as_index=False)
        .agg(n_dc_observed=("site_id", "nunique"), n_rows=("aoi_id", "count"))
        .copy()
    )
    by_pass["n_dc_sites"] = int(n_dc_sites)
    by_pass["frac"] = by_pass["n_dc_observed"] / float(max(n_dc_sites, 1))
    by_pass = by_pass.sort_values("date")

    by_site = (
        df.groupby(["site_id", "site_name"], as_index=False, dropna=False)
        .agg(n_obs=("date", "nunique"), first_dt=("dt", "min"), last_dt=("dt", "max"))
        .copy()
    )
    by_site["n_study_nights"] = n_nights
    by_site["frac_nights"] = by_site["n_obs"] / float(max(n_nights, 1))
    if "first_dt" in by_site.columns:
        by_site["first_dt"] = pd.to_datetime(by_site["first_dt"], utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        by_site["last_dt"] = pd.to_datetime(by_site["last_dt"], utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return by_pass, by_site


def coverage_from_effect_geojson(path: Path) -> pd.DataFrame:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for feat in raw.get("features") or []:
        p = feat.get("properties") or {}
        rows.append(
            {
                "site_id": p.get("site_id") or p.get("aoi_id"),
                "site_name": p.get("site_name") or p.get("name"),
                "buffer_m": p.get("buffer_m"),
                "n_obs": p.get("n_obs"),
                "first_dt": p.get("first_dt"),
                "last_dt": p.get("last_dt"),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Export per-pass and per-site observation coverage for the combined time series. "
            "Does not mosaic rasters."
        )
    )
    ap.add_argument("--out_dir", required=True, help="Pipeline outputs directory")
    ap.add_argument("--buffer_m", type=float, default=500.0)
    ap.add_argument("--n_dc_sites", type=int, default=None)
    ap.add_argument("--web_json", default="../data/coverage_latest.json")
    ap.add_argument("--effect_geojson", default="../data/dc_effect_cumulative.geojson")
    args = ap.parse_args()

    here = Path(__file__).parent
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = (here / out_dir).resolve()

    usable_path = out_dir / "collapsed_aoi_dt_usable.csv"
    by_pass = None
    by_site = None
    if usable_path.exists():
        usable = pd.read_csv(usable_path)
        n_sites = args.n_dc_sites
        if n_sites is None:
            dc = usable[pd.to_numeric(usable.get("is_data_center", 0), errors="coerce") == 1]
            n_sites = int(dc["site_id"].nunique()) if "site_id" in dc.columns else 0
        by_pass, by_site = coverage_from_collapsed(usable, int(n_sites), float(args.buffer_m))
        _write_csv(out_dir / "coverage_by_pass.csv", by_pass)
        _write_csv(out_dir / "coverage_by_site.csv", by_site)
    else:
        effect_path = (
            (here / args.effect_geojson).resolve()
            if not Path(args.effect_geojson).is_absolute()
            else Path(args.effect_geojson)
        )
        if not effect_path.exists():
            raise SystemExit(f"Need {usable_path} or {effect_path}")
        by_site = coverage_from_effect_geojson(effect_path)
        if args.buffer_m is not None and "buffer_m" in by_site.columns:
            by_site = by_site[pd.to_numeric(by_site["buffer_m"], errors="coerce") == float(args.buffer_m)].copy()
        n_nights = int(pd.to_numeric(by_site["n_obs"], errors="coerce").max() or 0)
        by_site["n_study_nights"] = n_nights
        by_site["frac_nights"] = pd.to_numeric(by_site["n_obs"], errors="coerce") / float(max(n_nights, 1))
        _write_csv(out_dir / "coverage_by_site.csv", by_site)
        print("ℹ️ No collapsed usable table; skipped coverage_by_pass.csv")

    n_dc = args.n_dc_sites
    if n_dc is None:
        n_dc = int(by_site["site_id"].nunique()) if by_site is not None and "site_id" in by_site.columns else 0

    snapshot = {}
    if by_pass is not None and not by_pass.empty:
        last = by_pass.iloc[-1]
        snapshot = {
            "scene_time": str(last["date"]),
            "n_dc_with_pixels": int(last["n_dc_observed"]),
            "n_dc_sites": int(last["n_dc_sites"]),
            "frac": float(last["frac"]),
        }

    web = {
        "buffer_m": float(args.buffer_m),
        "n_dc_sites": int(n_dc),
        "n_study_nights": int(by_site["n_study_nights"].iloc[0]) if by_site is not None and len(by_site) else 0,
        "median_n_obs": (
            float(np.nanmedian(pd.to_numeric(by_site["n_obs"], errors="coerce")))
            if by_site is not None and len(by_site)
            else None
        ),
        "snapshot": snapshot,
        "note": "Per-site series can be combined without one night covering every site. Same-timestamp DC−control deltas only.",
    }
    web_path = (here / args.web_json).resolve() if not Path(args.web_json).is_absolute() else Path(args.web_json)
    web_path.parent.mkdir(parents=True, exist_ok=True)
    web_path.write_text(json.dumps(web, indent=2) + "\n", encoding="utf-8")
    print(f"✅ Wrote: {web_path}")


if __name__ == "__main__":
    main()
