from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, List

import geopandas as gpd
import pandas as pd


def parse_buffer_list(s: str) -> List[float]:
    parts = [p.strip() for p in (s or "").split(",") if p.strip()]
    out: List[float] = []
    for p in parts:
        out.append(float(p))
    if not out:
        raise ValueError("No buffer distances provided.")
    return out


def ensure_points(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.geometry.isna().any():
        gdf = gdf.dropna(subset=["geometry"]).copy()
    if not all(gdf.geometry.geom_type.isin(["Point", "MultiPoint"])):
        raise ValueError("Expected point geometries.")
    return gdf


def buffer_points_m(gdf_wgs84: gpd.GeoDataFrame, buffer_m: float) -> gpd.GeoSeries:
    metric = gdf_wgs84.estimate_utm_crs() or "EPSG:3857"
    gdf_m = gdf_wgs84.to_crs(metric)
    buffered = gdf_m.geometry.buffer(buffer_m)
    return gpd.GeoSeries(buffered, crs=metric).to_crs("EPSG:4326")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", required=True, help="Input points GeoJSON/GeoPackage/etc (EPSG:4326 preferred)")
    ap.add_argument("--name_field", default=None, help="Optional name field for labeling (e.g. 'name')")
    ap.add_argument("--id_field", default=None, help="Optional stable ID field; otherwise index-based")
    ap.add_argument("--buffers_m", default="250,500,1000", help="Comma-separated buffer distances in meters")
    ap.add_argument("--group", default="data_center", help="Group label written to AOIs (e.g. data_center/control)")
    ap.add_argument("--output", required=True, help="Output polygon GeoJSON path")
    args = ap.parse_args()

    in_path = Path(args.points)
    if not in_path.exists():
        raise SystemExit(f"Missing points file: {in_path}")

    gdf = gpd.read_file(in_path)
    if gdf.crs is None:
        # assume lon/lat
        gdf = gdf.set_crs("EPSG:4326")
    gdf = gdf.to_crs("EPSG:4326")
    gdf = ensure_points(gdf)

    buffers = parse_buffer_list(args.buffers_m)

    id_field = args.id_field
    if id_field and id_field not in gdf.columns:
        raise SystemExit(f"id_field {id_field!r} not found in columns: {list(gdf.columns)}")

    name_field = args.name_field
    if name_field and name_field not in gdf.columns:
        raise SystemExit(f"name_field {name_field!r} not found in columns: {list(gdf.columns)}")

    base = gdf.copy()
    if id_field:
        base["site_id"] = base[id_field].astype(str)
    else:
        base["site_id"] = [f"site_{i:04d}" for i in range(len(base))]
    base["site_name"] = base[name_field].astype(str) if name_field else base["site_id"]

    rows: List[dict] = []
    geoms = []
    for bm in buffers:
        buffered = buffer_points_m(base, bm)
        for i, geom in enumerate(buffered):
            site_id = str(base.iloc[i]["site_id"])
            rows.append(
                {
                    "aoi_id": f"{args.group}:{site_id}:buf_{int(bm)}m",
                    "group": args.group,
                    "site_id": site_id,
                    "site_name": str(base.iloc[i]["site_name"]),
                    "buffer_m": float(bm),
                    "lon": float(base.geometry.iloc[i].x),
                    "lat": float(base.geometry.iloc[i].y),
                }
            )
            geoms.append(geom)

    out_gdf = gpd.GeoDataFrame(pd.DataFrame.from_records(rows), geometry=geoms, crs="EPSG:4326")
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_gdf.to_file(out_path, driver="GeoJSON")
    print(f"✅ Wrote buffered AOIs: {out_path} ({len(out_gdf)} polygons)")


if __name__ == "__main__":
    main()

