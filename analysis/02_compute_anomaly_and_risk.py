from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd

from utils_config import Config, load_config


def compute_trend_c_per_year(dates: pd.Series, values: pd.Series) -> float:
    x = pd.to_datetime(dates, errors="coerce")
    y = pd.to_numeric(values, errors="coerce")
    mask = x.notna() & y.notna()
    if mask.sum() < 5:
        return float("nan")

    # Fit y = a + b*t where t is in years since start.
    t0 = x[mask].min()
    t_years = (x[mask] - t0).dt.total_seconds() / (365.25 * 24 * 3600)
    b = np.polyfit(t_years.to_numpy(), y[mask].to_numpy(), 1)[0]
    return float(b)


def baseline_group_key(cfg: Config, dt: pd.Series) -> pd.Series:
    if cfg.baseline.grouping == "month":
        return dt.dt.month
    if cfg.baseline.grouping == "doy":
        return dt.dt.dayofyear
    raise ValueError(f"Unknown baseline.grouping: {cfg.baseline.grouping}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    cfg = load_config(args.config)
    out_dir = Path(cfg.outputs_dir)
    ts_path = out_dir / "timeseries.csv"
    if not ts_path.exists():
        raise SystemExit(f"Missing input: {ts_path}. Run 01_extract_zonal_timeseries.py first.")

    df = pd.read_csv(ts_path)
    if "mean" not in df.columns:
        raise SystemExit("Expected 'mean' column in timeseries.csv. Add 'mean' to config.stats.")

    df["dt"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
    df["group"] = baseline_group_key(cfg, df["dt"])

    # Baseline per (aoi_id, group) using mean temp.
    base = (
        df.groupby(["aoi_id", "group"], as_index=False)
        .agg(
            baseline_mean=("mean", "mean"),
            baseline_std=("mean", "std"),
            baseline_p90=("mean", lambda s: float(np.nanpercentile(s.to_numpy(), 90))),
            n_obs=("mean", "count"),
        )
        .copy()
    )
    base.loc[base["n_obs"] < cfg.baseline.min_obs_per_group, ["baseline_mean", "baseline_std", "baseline_p90"]] = np.nan

    df2 = df.merge(base, on=["aoi_id", "group"], how="left")
    df2["anomaly"] = df2["mean"] - df2["baseline_mean"]
    df2["z"] = df2["anomaly"] / df2["baseline_std"]
    df2["is_hot_night"] = (df2["mean"] > df2["baseline_p90"]).astype("int32")

    # Latest snapshot per AOI
    df2 = df2.sort_values(["aoi_id", "dt"])
    latest = df2.groupby("aoi_id", as_index=False).tail(1).copy()

    # Heat-night frequency over last 14 observations
    df2["rank_desc"] = df2.groupby("aoi_id")["dt"].rank(ascending=False, method="first")
    recent = df2[df2["rank_desc"] <= 14].copy()
    freq = recent.groupby("aoi_id", as_index=False).agg(hot_nights_14=("is_hot_night", "sum"))

    # Trend per AOI
    trend = (
        df2.groupby("aoi_id", as_index=False)
        .apply(lambda g: pd.Series({"trend_c_per_year": compute_trend_c_per_year(g["dt"], g["mean"])}))
        .reset_index(drop=True)
    )

    latest = latest.merge(freq, on="aoi_id", how="left").merge(trend, on="aoi_id", how="left")
    latest["hot_nights_14"] = latest["hot_nights_14"].fillna(0).astype(int)

    # Simple interpretable risk score (0–100-ish):
    # - z-score (clipped)
    # - hot-night frequency
    # - positive trend
    z_clip = latest["z"].clip(lower=-3, upper=6).fillna(0)
    freq_score = (latest["hot_nights_14"] / 14.0) * 25.0
    trend_score = latest["trend_c_per_year"].clip(lower=0, upper=5).fillna(0) * 5.0
    latest["risk_score"] = (z_clip * 10.0 + freq_score + trend_score).clip(lower=0, upper=100)

    out_ts = out_dir / "timeseries_with_anomaly.csv"
    out_latest = out_dir / "aoi_summary_latest.csv"
    out_base = out_dir / "aoi_summary_full.csv"

    df2.drop(columns=["rank_desc"], errors="ignore").to_csv(out_ts, index=False)
    latest.to_csv(out_latest, index=False)
    base.to_csv(out_base, index=False)

    print(f"✅ Wrote: {out_ts}")
    print(f"✅ Wrote: {out_latest}")
    print(f"✅ Wrote: {out_base}")


if __name__ == "__main__":
    main()

