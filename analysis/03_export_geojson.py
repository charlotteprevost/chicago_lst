from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd

from utils_config import load_config


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    cfg = load_config(args.config)

    out_dir = Path(cfg.outputs_dir)
    latest_path = out_dir / "aoi_summary_latest.csv"
    if not latest_path.exists():
        raise SystemExit(f"Missing input: {latest_path}. Run 02_compute_anomaly_and_risk.py first.")

    aois = gpd.read_file(cfg.aoi_path)
    if aois.crs is None:
        aois = aois.set_crs(cfg.aoi_crs_if_missing)

    latest = pd.read_csv(latest_path)
    latest = latest.rename(columns={"aoi_id": cfg.aoi_id_field})

    gdf = aois.merge(latest, on=cfg.aoi_id_field, how="left")

    # Keep the overlay lightweight
    keep_cols = [
        cfg.aoi_id_field,
        # Context (if present in AOIs)
        "group",
        "site_id",
        "site_name",
        "buffer_m",
        "is_data_center",
        "risk_score",
        "mean",
        "anomaly",
        "z",
        "hot_nights_14",
        "trend_c_per_year",
        "date",
        "units",
        "project",
        "geometry",
    ]
    keep_cols = [c for c in keep_cols if c in gdf.columns]
    gdf = gdf[keep_cols].copy()

    out_path = Path(cfg.export_geojson_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure WGS84 for web mapping
    gdf = gdf.to_crs("EPSG:4326")
    gdf.to_file(out_path, driver="GeoJSON")
    print(f"✅ Wrote: {out_path}")


if __name__ == "__main__":
    main()

