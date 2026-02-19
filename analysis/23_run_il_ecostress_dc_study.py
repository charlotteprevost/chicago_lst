from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import geopandas as gpd
import pandas as pd


IL_BBOX = (-91.52, 36.97, -87.0, 42.51)  # west, south, east, north
ECOSTRESS_FIRST_LIGHT = "2018-07-09"


def run(cmd: List[str], cwd: Path) -> None:
    p = subprocess.run(cmd, cwd=str(cwd))
    if p.returncode != 0:
        raise SystemExit(f"Command failed: {' '.join(cmd)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data_centers", required=True, help="Points GeoJSON of data centers (EPSG:4326)")
    ap.add_argument(
        "--data_center_attrs",
        default="data_center_attributes.csv",
        help="CSV with per-site attributes (opening_year at least). Joined on site_id if present; otherwise we generate deterministic site_ids.",
    )
    ap.add_argument("--start", default=None, help="Start date/time for ECOSTRESS search (YYYY-MM-DD or ISO8601). Default: auto.")
    ap.add_argument("--end", default=None, help="End date/time for ECOSTRESS search (YYYY-MM-DD or ISO8601). Default: today (UTC).")
    ap.add_argument("--buffers_m", default="250,500,1000", help="Comma-separated buffer distances in meters")
    ap.add_argument(
        "--primary_buffer_m",
        type=float,
        default=None,
        help="Primary buffer distance (meters). Default: max(buffers_m).",
    )
    ap.add_argument("--n_controls", type=int, default=None, help="Control points count (default: equals # data centers)")
    ap.add_argument("--min_distance_m", type=float, default=5000.0, help="Min control distance from any data center")
    ap.add_argument(
        "--metro_hull_buffer_km",
        type=float,
        default=30.0,
        help="Sample controls within a convex-hull 'metro' around data centers buffered by this many km (default: 30). Set to 0 to disable.",
    )
    ap.add_argument("--illinois_boundary", default=None, help="Optional Illinois boundary polygon file")
    ap.add_argument("--bbox", default=None, help="Fallback bbox west,south,east,north (default: IL rough bbox)")
    ap.add_argument("--cache_dir", default="ecostress_cache", help="Download/cache directory for ECOSTRESS GeoTIFFs")
    ap.add_argument("--outputs_dir", default="outputs_ecostress_il_qc", help="Where to write outputs")
    ap.add_argument(
        "--raster_glob",
        default="*_LST.tif",
        help="Raster glob within cache_dir (default: '*_LST.tif' to use only LST tiles)",
    )
    ap.add_argument(
        "--date_regex",
        default=r"(\d{8}T\d{6})",
        help="Regex capture group for datetime in filename",
    )
    ap.add_argument("--date_format", default="%Y%m%dT%H%M%S", help="strptime format for captured datetime")
    ap.add_argument(
        "--auth",
        default="environment",
        choices=["environment", "netrc", "token", "none"],
        help="ECOSTRESS download auth (passed to 22_fetch_ecostress_l2t_il.py)",
    )
    ap.add_argument("--max_granules", type=int, default=2000, help="Max granules to request from CMR (multi-year runs need bigger limits).")
    ap.add_argument(
        "--force_fetch",
        action="store_true",
        help="Force re-fetch even if cache_dir already has GeoTIFFs",
    )
    args = ap.parse_args()

    here = Path(__file__).parent
    out_dir = here / args.outputs_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = (here / args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Normalize buffers: fetch/extract all buffers (primary is the max buffer for headline stats).
    buffers = [float(x.strip()) for x in str(args.buffers_m).split(",") if x.strip()]
    if not buffers:
        raise SystemExit("No buffers_m provided.")
    primary_buffer_m = float(args.primary_buffer_m) if args.primary_buffer_m is not None else float(max(buffers))
    buffers_arg = ",".join([str(int(b)) if float(b).is_integer() else str(b) for b in sorted(set(buffers))])

    # Load data centers and ensure stable site_id.
    dc_in = gpd.read_file(args.data_centers)
    if dc_in.crs is None:
        dc_in = dc_in.set_crs("EPSG:4326")
    dc_in = dc_in.to_crs("EPSG:4326").dropna(subset=["geometry"]).copy()

    if "site_id" not in dc_in.columns:
        # Deterministic site_id based on name + rounded lon/lat.
        def _mk_id(row) -> str:
            name = str(row.get("name", "") or "").strip().lower()
            lon = float(row.geometry.x)
            lat = float(row.geometry.y)
            key = f"{name}|{lon:.6f}|{lat:.6f}"
            h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
            return f"dc_{h}"

        dc_in["site_id"] = dc_in.apply(_mk_id, axis=1)

    # Write a normalized DC file for downstream steps (controls + buffers).
    dc_norm_path = out_dir / "data_centers_norm.geojson"
    dc_in.to_file(dc_norm_path, driver="GeoJSON")

    # Load attributes (opening_year) if present.
    attrs_path = Path(args.data_center_attrs)
    attrs = None
    if attrs_path.exists():
        attrs = pd.read_csv(attrs_path)
        if "site_id" not in attrs.columns:
            raise SystemExit(f"{attrs_path} must include a site_id column.")
    else:
        # Not fatal; we'll just run with the user-provided start/end.
        attrs = None

    # Auto start/end dates (multi-year).
    start = args.start
    end = args.end
    if start is None:
        # User request: pull since ECOSTRESS inception.
        start = datetime.fromisoformat(ECOSTRESS_FIRST_LIGHT).date().isoformat()
    if end is None:
        end = datetime.now(timezone.utc).date().isoformat()

    # 1) Controls
    controls_points = out_dir / "controls_points.geojson"
    metro_km = float(args.metro_hull_buffer_km) if args.metro_hull_buffer_km is not None else None
    run(
        [
            "python",
            str(here / "21_make_matched_controls.py"),
            "--data_centers",
            str(dc_norm_path),
            "--output",
            str(controls_points),
            "--min_distance_m",
            str(args.min_distance_m),
            "--seed",
            "7",
            *(["--n_controls", str(args.n_controls)] if args.n_controls is not None else []),
            *(["--metro_hull_buffer_km", str(metro_km)] if metro_km is not None and metro_km > 0 else []),
            *(["--illinois_boundary", args.illinois_boundary] if args.illinois_boundary else []),
            *(["--bbox", args.bbox] if args.bbox else []),
        ],
        cwd=here,
    )

    # 2) Buffer AOIs for data centers and controls
    aois_dc = out_dir / "aois_data_centers.geojson"
    aois_ctrl = out_dir / "aois_controls.geojson"
    run(
        [
            "python",
            str(here / "20_make_aoi_buffers.py"),
            "--points",
            str(dc_norm_path),
            "--buffers_m",
            buffers_arg,
            "--group",
            "data_center",
            "--name_field",
            "name",
            "--id_field",
            "site_id",
            "--output",
            str(aois_dc),
        ],
        cwd=here,
    )
    run(
        [
            "python",
            str(here / "20_make_aoi_buffers.py"),
            "--points",
            str(controls_points),
            "--buffers_m",
            buffers_arg,
            "--group",
            "control",
            "--name_field",
            "control_id",
            "--id_field",
            "control_id",
            "--output",
            str(aois_ctrl),
        ],
        cwd=here,
    )

    # 3) Merge AOIs
    gdc = gpd.read_file(aois_dc)
    gct = gpd.read_file(aois_ctrl)
    gdc["is_data_center"] = 1
    gct["is_data_center"] = 0
    aois_all = gpd.GeoDataFrame(pd.concat([gdc, gct], ignore_index=True), crs="EPSG:4326")
    aois_all_path = out_dir / "aois_all.geojson"
    aois_all.to_file(aois_all_path, driver="GeoJSON")

    # 4) Fetch ECOSTRESS tiles for Illinois/date range
    has_any_tif = next(cache_dir.glob("*.tif"), None) is not None
    if has_any_tif and not args.force_fetch:
        print(f"ℹ️ Using existing ECOSTRESS cache (skipping download): {cache_dir}")
    else:
        fetch_cmd = [
            "python",
            str(here / "22_fetch_ecostress_l2t_il.py"),
            "--start",
            str(start),
            "--end",
            str(end),
            "--out_dir",
            str(cache_dir),
            "--auth",
            args.auth,
            "--max_granules",
            str(int(args.max_granules)),
            "--chunk_days",
            "30",
        ]
        if args.bbox:
            fetch_cmd += ["--bbox", args.bbox]
        run(fetch_cmd, cwd=here)

    # 5) Generate config for zonal stats extractor
    cfg = {
        "project_name": "ecostress_il_data_centers",
        "aoi_path": str(aois_all_path),
        "aoi_id_field": "aoi_id",
        "buffer_m": None,
        "aoi_crs_if_missing": "EPSG:4326",
        "raster_dir": str(cache_dir),
        "raster_glob": args.raster_glob,
        "date_regex": args.date_regex,
        "date_format": args.date_format,
        # ECOSTRESS LST is Kelvin; convert to Celsius early.
        "value_units": "degC",
        "nodata_below": None,
        "nodata_equals": None,
        "value_transform": {"type": "scale_offset", "scale": 1.0, "offset": -273.15},
        "quality": {
            # Rigorous default: keep clear-sky, non-water, (QC class 0/1).
            "enabled": True,
            "ecostress_companion_masks": True,
            "keep_cloud_values": [0],
            "keep_water_values": [0],
            "qc_keep_classes": [0, 1],
            "qc_class_bitmask": 3,
            # Optional: set to e.g. 2.0 to drop high-uncertainty pixels.
            "max_lst_err": None,
        },
        "stats": ["mean", "median", "p90", "count"],
        "baseline": {"grouping": "month", "min_obs_per_group": 3},
        "outputs_dir": str(out_dir),
        # Write directly into repo data/ so the frontend can load it.
        "export_geojson_path": str((here.parent / "data" / "aoi_risk_latest.geojson").resolve()),
    }
    cfg_path = out_dir / "config.ecostress_il.generated.json"
    cfg_path.write_text(json.dumps(cfg, indent=2))

    # 6) Run zonal stats extraction
    run(
        [
            "python",
            str(here / "01_extract_zonal_timeseries.py"),
            "--config",
            str(cfg_path),
        ],
        cwd=here,
    )

    # 7) Enrich timeseries with AOI metadata + produce summaries
    ts_path = out_dir / "timeseries.csv"
    if not ts_path.exists():
        raise SystemExit(f"Missing output: {ts_path}")
    ts = pd.read_csv(ts_path)

    meta_cols = ["aoi_id", "group", "site_id", "site_name", "buffer_m", "lon", "lat", "is_data_center"]
    meta = pd.DataFrame(aois_all.drop(columns="geometry"))[meta_cols].copy()
    df = ts.merge(meta, on="aoi_id", how="left")
    df["dt"] = pd.to_datetime(df["date"], errors="coerce", utc=True)

    # Join attributes + compute opening_date.
    # Keep both pre-open and post-open observations so the web map can explain
    # "before vs after data center opening" windows directly.
    if attrs is not None and "opening_year" in attrs.columns:
        attrs2 = attrs.copy()
        attrs2["opening_year"] = pd.to_numeric(attrs2["opening_year"], errors="coerce")
        attrs2["opening_date"] = pd.to_datetime(
            attrs2["opening_year"].dropna().astype(int).astype(str) + "-01-01",
            utc=True,
            errors="coerce",
        )
        df = df.merge(attrs2, on="site_id", how="left", suffixes=("", "_attr"))

    # Keep controls on the same timestamps as DC rows.
    dc_dates = df.loc[df["is_data_center"] == 1, "date"].dropna().unique().tolist()
    if dc_dates:
        df = df[(df["is_data_center"] == 1) | (df["date"].isin(dc_dates))].copy()

    out_enriched = out_dir / "timeseries_enriched.csv"
    df.to_csv(out_enriched, index=False)

    # Summary table: effect per date + buffer
    agg = (
        df.groupby(["date", "buffer_m", "is_data_center"], as_index=False)
        .agg(mean=("mean", "mean"), median=("median", "mean"), p90=("p90", "mean"), n=("count", "sum"))
        .copy()
    )
    # Pivot to DC vs control side-by-side
    pivot = agg.pivot_table(index=["date", "buffer_m"], columns="is_data_center", values=["mean", "median", "p90", "n"])
    pivot.columns = [f"{m}_{'dc' if int(k)==1 else 'ctrl'}" for m, k in pivot.columns.to_list()]
    pivot = pivot.reset_index()
    if "mean_dc" in pivot.columns and "mean_ctrl" in pivot.columns:
        pivot["mean_diff_dc_minus_ctrl"] = pivot["mean_dc"] - pivot["mean_ctrl"]
    out_summary = out_dir / "summary_effects_by_date_buffer.csv"
    pivot.to_csv(out_summary, index=False)

    # Regression-ready table (one row per AOI per timestamp)
    reg_ready = df[
        [
            "date",
            "dt",
            "aoi_id",
            "is_data_center",
            "buffer_m",
            "site_id",
            "site_name",
            "lon",
            "lat",
            "mean",
            "median",
            "p90",
            "count",
            "raster",
        ]
    ].copy()
    out_reg = out_dir / "regression_ready_rows.csv"
    reg_ready.to_csv(out_reg, index=False)

    print(f"✅ Wrote: {out_enriched}")
    print(f"✅ Wrote: {out_summary}")
    print(f"✅ Wrote: {out_reg}")
    print(f"ℹ️ ECOSTRESS cache: {cache_dir} (you can delete after run)")


if __name__ == "__main__":
    main()

