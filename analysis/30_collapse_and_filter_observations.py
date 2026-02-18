from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    v = pd.to_numeric(values, errors="coerce")
    w = pd.to_numeric(weights, errors="coerce")
    m = np.isfinite(v.to_numpy()) & np.isfinite(w.to_numpy()) & (w.to_numpy() > 0)
    if m.sum() == 0:
        return float("nan")
    return float(np.average(v.to_numpy()[m], weights=w.to_numpy()[m]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="regression_ready_rows.csv")
    ap.add_argument("--out_dir", required=True, help="Output directory (same as pipeline outputs)")
    ap.add_argument(
        "--min_abs_pixels",
        type=float,
        default=5.0,
        help="Absolute minimum pixel count per observation",
    )
    ap.add_argument(
        "--min_frac_of_p95",
        type=float,
        default=0.25,
        help="Keep obs with count >= this * (AOI p95 count)",
    )
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.input)
    if "dt" in df.columns:
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce", utc=True)

    # Collapse duplicate tile hits: one row per AOI per timestamp.
    group_keys = [
        "date",
        "dt",
        "aoi_id",
        "is_data_center",
        "buffer_m",
        "site_id",
        "site_name",
        "lon",
        "lat",
    ]
    for k in group_keys:
        if k not in df.columns:
            raise SystemExit(f"Missing required column: {k}")

    # Collapse duplicate tile hits: one row per AOI per timestamp.
    # We compute pixel-weighted means across tiles. (median/p90 aren't strictly aggregable;
    # this is an approximation that reduces duplicate-tile bias.)
    def _collapse(group: pd.DataFrame) -> pd.Series:
        w = group["count"]
        return pd.Series(
            {
                "n_tiles": group["raster"].nunique(),
                "pixels": float(pd.to_numeric(group["count"], errors="coerce").fillna(0.0).sum()),
                "mean": weighted_mean(group["mean"], w),
                "median": weighted_mean(group["median"], w),
                "p90": weighted_mean(group["p90"], w),
            }
        )

    collapsed = df.groupby(group_keys).apply(_collapse).reset_index()

    out_collapsed = out_dir / "collapsed_aoi_dt.csv"
    collapsed.to_csv(out_collapsed, index=False)

    # Build a per-AOI coverage reference from p95 of pixels.
    p95 = (
        collapsed.groupby("aoi_id", as_index=False)
        .agg(p95_pixels=("pixels", lambda s: float(np.nanpercentile(pd.to_numeric(s, errors="coerce"), 95))))
        .copy()
    )
    usable = collapsed.merge(p95, on="aoi_id", how="left")
    usable["min_pixels_threshold"] = np.maximum(
        float(args.min_abs_pixels),
        float(args.min_frac_of_p95) * pd.to_numeric(usable["p95_pixels"], errors="coerce").fillna(0.0),
    )
    usable["is_usable"] = pd.to_numeric(usable["pixels"], errors="coerce").fillna(0.0) >= usable["min_pixels_threshold"]

    out_usable = out_dir / "collapsed_aoi_dt_usable.csv"
    usable.to_csv(out_usable, index=False)

    # Quick stats
    kept = int(usable["is_usable"].sum())
    total = int(len(usable))
    print(f"✅ Wrote: {out_collapsed}")
    print(f"✅ Wrote: {out_usable} (kept {kept}/{total} observations)")


if __name__ == "__main__":
    main()

