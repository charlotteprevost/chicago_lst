from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from utils_config import load_config

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DC_CSV = ROOT / "data" / "chicago_data_centers_183.csv"


def load_verified_opening_dates(csv_path: Path) -> pd.DataFrame:
    """Return name_key + opening_date for rows with verified cites."""
    rows = pd.read_csv(csv_path)
    if "went_live_status" not in rows.columns or "went_live_date" not in rows.columns:
        return pd.DataFrame(columns=["name_key", "opening_date"])
    status = rows["went_live_status"].astype(str).str.strip().str.lower()
    out = rows.loc[status == "verified", ["name", "went_live_date"]].copy()
    out["name_key"] = out["name"].astype(str).str.strip().str.lower()
    out["opening_date"] = pd.to_datetime(out["went_live_date"], errors="coerce", utc=True)
    out = out.dropna(subset=["opening_date", "name_key"])
    out = out[out["name_key"] != ""]
    return out.drop_duplicates("name_key")[["name_key", "opening_date"]]


def apply_verified_opening_dates(
    df: pd.DataFrame, verified: pd.DataFrame, name_col: str = "site_name"
) -> pd.DataFrame:
    """Fill opening_date from verified cites when the column is missing or null."""
    out = df.copy()
    if verified.empty or name_col not in out.columns:
        if "opening_date" not in out.columns:
            out["opening_date"] = pd.NaT
        return out
    mapped = out[name_col].astype(str).str.strip().str.lower().map(
        verified.set_index("name_key")["opening_date"]
    )
    if "opening_date" in out.columns:
        existing = pd.to_datetime(out["opening_date"], errors="coerce", utc=True)
        out["opening_date"] = existing.where(existing.notna(), mapped)
    else:
        out["opening_date"] = mapped
    return out


def weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    v = pd.to_numeric(values, errors="coerce")
    w = pd.to_numeric(weights, errors="coerce")
    m = np.isfinite(v.to_numpy()) & np.isfinite(w.to_numpy()) & (w.to_numpy() > 0)
    if m.sum() == 0:
        return float("nan")
    return float(np.average(v.to_numpy()[m], weights=w.to_numpy()[m]))


def iso_or_none(ts: pd.Timestamp | None) -> str | None:
    if ts is None or pd.isna(ts):
        return None
    return pd.Timestamp(ts).isoformat()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="Config JSON used for the run (generated or manual)")
    ap.add_argument("--out", required=True, help="Output GeoJSON path (e.g., ../data/dc_effect_cumulative.geojson)")
    ap.add_argument("--value_col", default="mean", help="Column from timeseries_enriched.csv (default: mean)")
    ap.add_argument("--buffer_m", type=float, default=None, help="Optional: restrict to a single buffer size (meters)")
    ap.add_argument(
        "--dc-csv",
        default=str(DEFAULT_DC_CSV),
        help="Data-center CSV with verified went_live_date columns.",
    )
    args = ap.parse_args()

    cfg = load_config(args.config)
    out_dir = Path(cfg.outputs_dir)
    ts_enriched = out_dir / "timeseries_enriched.csv"
    collapsed_usable = out_dir / "collapsed_aoi_dt_usable.csv"
    if collapsed_usable.exists():
        src = collapsed_usable
    elif ts_enriched.exists():
        src = ts_enriched
    else:
        raise SystemExit(f"Missing input: {ts_enriched} (run 23_run_il_ecostress_dc_study.py first)")

    aois = gpd.read_file(cfg.aoi_path)
    if aois.crs is None:
        aois = aois.set_crs(cfg.aoi_crs_if_missing)

    df = pd.read_csv(src)
    if "is_usable" in df.columns:
        df = df[df["is_usable"] == True].copy()  # noqa: E712
    if "count" not in df.columns and "pixels" in df.columns:
        df["count"] = df["pixels"]
    if src == collapsed_usable and ts_enriched.exists():
        extra = pd.read_csv(ts_enriched, usecols=lambda c: c in {"aoi_id", "opening_date", "opening_year"})
        if "aoi_id" in extra.columns and "opening_date" in extra.columns and "opening_date" not in df.columns:
            df = df.merge(extra.drop_duplicates("aoi_id"), on="aoi_id", how="left")
    if args.value_col not in df.columns:
        raise SystemExit(f"Missing column in {src.name}: {args.value_col}")
    for col in ["aoi_id", "date", "buffer_m", "is_data_center", "count"]:
        if col not in df.columns:
            raise SystemExit(f"Missing column in {src.name}: {col}")

    df["dt"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
    if args.buffer_m is not None:
        df = df[pd.to_numeric(df["buffer_m"], errors="coerce") == float(args.buffer_m)].copy()

    # Compute delta for every DC AOI on every timestamp: dc - control_mean(date, buffer)
    ctrl = df[df["is_data_center"] == 0].copy()
    dc = df[df["is_data_center"] == 1].copy()
    if ctrl.empty or dc.empty:
        raise SystemExit("Need both DC and control observations to compute cumulative effect.")

    ctrl_agg = (
        ctrl.groupby(["date", "buffer_m"], as_index=False)
        .agg(
            ctrl_mean=(args.value_col, "mean"),
            ctrl_n=(args.value_col, lambda s: int(np.isfinite(pd.to_numeric(s, errors="coerce")).sum())),
        )
        .copy()
    )
    dc2 = dc.merge(ctrl_agg, on=["date", "buffer_m"], how="left")
    dc2["delta_c_dc_minus_ctrl"] = pd.to_numeric(dc2[args.value_col], errors="coerce") - pd.to_numeric(dc2["ctrl_mean"], errors="coerce")

    # Aggregate over time per AOI (cumulative-to-date)
    def _p90(s: pd.Series) -> float:
        v = pd.to_numeric(s, errors="coerce").to_numpy()
        v = v[np.isfinite(v)]
        if v.size == 0:
            return float("nan")
        return float(np.percentile(v, 90))

    # Prefer verified went_live_date from the site CSV when attrs/opening_date is empty.
    dc_csv = Path(args.dc_csv)
    if dc_csv.exists() and "site_name" in dc2.columns:
        dc2 = apply_verified_opening_dates(dc2, load_verified_opening_dates(dc_csv))
    elif "opening_date" in dc2.columns:
        dc2["opening_date"] = pd.to_datetime(dc2["opening_date"], errors="coerce", utc=True)
    else:
        dc2["opening_date"] = pd.NaT

    grouped = []
    for aoi_id, g in dc2.groupby("aoi_id"):
        g = g.copy()
        g["dt"] = pd.to_datetime(g["dt"], errors="coerce", utc=True)
        open_dt = g["opening_date"].dropna().min() if g["opening_date"].notna().any() else pd.NaT
        pre = g[g["dt"] < open_dt] if pd.notna(open_dt) else g.iloc[0:0]
        post = g[g["dt"] >= open_dt] if pd.notna(open_dt) else g.iloc[0:0]

        grouped.append(
            {
                "aoi_id": str(aoi_id),
                "buffer_m": float(pd.to_numeric(g["buffer_m"], errors="coerce").dropna().iloc[0]),
                "n_obs": int(np.isfinite(pd.to_numeric(g["delta_c_dc_minus_ctrl"], errors="coerce")).sum()),
                "first_dt": g["dt"].min().isoformat() if pd.notna(g["dt"].min()) else None,
                "last_dt": g["dt"].max().isoformat() if pd.notna(g["dt"].max()) else None,
                "opening_date": iso_or_none(open_dt),
                "n_pre_open_obs": int(np.isfinite(pd.to_numeric(pre["delta_c_dc_minus_ctrl"], errors="coerce")).sum()),
                "n_post_open_obs": int(np.isfinite(pd.to_numeric(post["delta_c_dc_minus_ctrl"], errors="coerce")).sum()),
                "pre_open_first_dt": iso_or_none(pre["dt"].min() if not pre.empty else None),
                "pre_open_last_dt": iso_or_none(pre["dt"].max() if not pre.empty else None),
                "post_open_first_dt": iso_or_none(post["dt"].min() if not post.empty else None),
                "post_open_last_dt": iso_or_none(post["dt"].max() if not post.empty else None),
                "delta_mean_c": weighted_mean(g["delta_c_dc_minus_ctrl"], g["count"]),
                "delta_median_c": float(pd.to_numeric(g["delta_c_dc_minus_ctrl"], errors="coerce").median()),
                "delta_p90_c": _p90(g["delta_c_dc_minus_ctrl"]),
                "dc_mean_c": weighted_mean(g[args.value_col], g["count"]),
                "ctrl_mean_c": weighted_mean(g["ctrl_mean"], g["count"]),
                "delta_pre_open_mean_c": weighted_mean(pre["delta_c_dc_minus_ctrl"], pre["count"]) if not pre.empty else float("nan"),
                "delta_post_open_mean_c": weighted_mean(post["delta_c_dc_minus_ctrl"], post["count"]) if not post.empty else float("nan"),
                "dc_pre_open_mean_c": weighted_mean(pre[args.value_col], pre["count"]) if not pre.empty else float("nan"),
                "dc_post_open_mean_c": weighted_mean(post[args.value_col], post["count"]) if not post.empty else float("nan"),
                "ctrl_pre_open_mean_c": weighted_mean(pre["ctrl_mean"], pre["count"]) if not pre.empty else float("nan"),
                "ctrl_post_open_mean_c": weighted_mean(post["ctrl_mean"], post["count"]) if not post.empty else float("nan"),
            }
        )

    agg = pd.DataFrame(grouped)
    if agg.empty:
        raise SystemExit("No cumulative effect rows produced.")

    # Join geometry + id fields from AOIs
    dc_aois = aois[aois["aoi_id"].astype(str).isin(agg["aoi_id"].astype(str))].copy()
    out_gdf = dc_aois.merge(agg, on="aoi_id", how="left")

    keep = [
        "aoi_id",
        "site_id",
        "site_name",
        "buffer_m",
        "n_obs",
        "first_dt",
        "last_dt",
        "opening_date",
        "n_pre_open_obs",
        "n_post_open_obs",
        "pre_open_first_dt",
        "pre_open_last_dt",
        "post_open_first_dt",
        "post_open_last_dt",
        "delta_mean_c",
        "delta_median_c",
        "delta_p90_c",
        "dc_mean_c",
        "ctrl_mean_c",
        "delta_pre_open_mean_c",
        "delta_post_open_mean_c",
        "dc_pre_open_mean_c",
        "dc_post_open_mean_c",
        "ctrl_pre_open_mean_c",
        "ctrl_post_open_mean_c",
        "geometry",
    ]
    keep = [c for c in keep if c in out_gdf.columns]
    out_gdf = out_gdf[keep].copy()

    out_gdf = out_gdf.to_crs("EPSG:4326")
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_gdf.to_file(out_path, driver="GeoJSON")
    print(f"✅ Wrote: {out_path} ({len(out_gdf)} features)")


if __name__ == "__main__":
    main()

