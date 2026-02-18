from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point, box


# Rough Illinois bounds (lon/lat)
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


def load_boundary(boundary_path: Optional[str], bbox: Bounds) -> gpd.GeoSeries:
    if boundary_path:
        p = Path(boundary_path)
        if not p.exists():
            raise SystemExit(f"Missing boundary file: {p}")
        gdf = gpd.read_file(p)
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        gdf = gdf.to_crs("EPSG:4326")
        geom = gdf.unary_union
        return gpd.GeoSeries([geom], crs="EPSG:4326")
    # fallback to bbox polygon
    return gpd.GeoSeries([box(bbox.west, bbox.south, bbox.east, bbox.north)], crs="EPSG:4326")

def dc_metro_polygon(dc_wgs84: gpd.GeoDataFrame, hull_buffer_km: float) -> gpd.GeoSeries:
    """
    Create a 'metro' polygon based on the convex hull of data center points,
    buffered outward by hull_buffer_km.
    """
    if dc_wgs84.empty:
        raise SystemExit("No data center geometries found to build metro polygon.")
    if hull_buffer_km <= 0:
        raise SystemExit("--metro_hull_buffer_km must be > 0")

    g = dc_wgs84.copy()
    g = g.to_crs("EPSG:4326")
    metric = g.estimate_utm_crs() or "EPSG:3857"
    gm = g.to_crs(metric)
    hull = gm.unary_union.convex_hull
    poly_m = hull.buffer(float(hull_buffer_km) * 1000.0)
    poly = gpd.GeoSeries([poly_m], crs=metric).to_crs("EPSG:4326")
    return poly


def sample_points_within(
    polygon_wgs84,
    bbox: Bounds,
    n: int,
    *,
    seed: int,
    max_tries: int,
    avoid_points_wgs84: Optional[gpd.GeoSeries],
    min_distance_m: float,
) -> gpd.GeoDataFrame:
    rng = random.Random(seed)

    # distance checks in metric CRS
    poly_gdf = gpd.GeoDataFrame({"_": [1]}, geometry=[polygon_wgs84], crs="EPSG:4326")
    metric = poly_gdf.estimate_utm_crs() or "EPSG:3857"
    poly_m = poly_gdf.to_crs(metric).geometry.iloc[0]

    avoid_m = None
    if avoid_points_wgs84 is not None and len(avoid_points_wgs84) > 0 and min_distance_m > 0:
        avoid_gdf = gpd.GeoDataFrame({"_": np.arange(len(avoid_points_wgs84))}, geometry=avoid_points_wgs84, crs="EPSG:4326")
        avoid_m = avoid_gdf.to_crs(metric).geometry

    pts: List[Point] = []
    tries = 0
    while len(pts) < n and tries < max_tries:
        tries += 1
        lon = rng.uniform(bbox.west, bbox.east)
        lat = rng.uniform(bbox.south, bbox.north)
        p = Point(lon, lat)
        if not polygon_wgs84.contains(p):
            continue

        if avoid_m is not None:
            # project point and enforce distance
            pm = gpd.GeoSeries([p], crs="EPSG:4326").to_crs(metric).iloc[0]
            if avoid_m.distance(pm).min() < float(min_distance_m):
                continue

        pts.append(p)

    if len(pts) < n:
        raise SystemExit(
            f"Could only place {len(pts)}/{n} control points after {tries} tries. "
            f"Reduce min_distance_m or increase bbox/max_tries."
        )

    return gpd.GeoDataFrame({"control_id": [f"ctrl_{i:04d}" for i in range(len(pts))]}, geometry=pts, crs="EPSG:4326")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data_centers", required=True, help="Input data center points GeoJSON (EPSG:4326)")
    ap.add_argument("--illinois_boundary", default=None, help="Optional Illinois boundary polygon file (GeoJSON/GPKG)")
    ap.add_argument("--bbox", default=None, help="Fallback bbox west,south,east,north (default: IL rough bbox)")
    ap.add_argument(
        "--metro_hull_buffer_km",
        type=float,
        default=None,
        help="If set, sample controls within a convex-hull 'metro' around data centers buffered by this many km (recommended).",
    )
    ap.add_argument("--n_controls", type=int, default=None, help="Number of control points (default: equals number of data centers)")
    ap.add_argument("--min_distance_m", type=float, default=5000.0, help="Min distance from any data center (meters)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--max_tries", type=int, default=500000)
    ap.add_argument("--output", required=True, help="Output control points GeoJSON")
    args = ap.parse_args()

    dc_path = Path(args.data_centers)
    if not dc_path.exists():
        raise SystemExit(f"Missing data_centers file: {dc_path}")

    dc = gpd.read_file(dc_path)
    if dc.crs is None:
        dc = dc.set_crs("EPSG:4326")
    dc = dc.to_crs("EPSG:4326")
    dc = dc.dropna(subset=["geometry"]).copy()

    if args.metro_hull_buffer_km is not None:
        boundary = dc_metro_polygon(dc, float(args.metro_hull_buffer_km))
        poly = boundary.geometry.iloc[0]
        minx, miny, maxx, maxy = boundary.total_bounds
        bbox = Bounds(float(minx), float(miny), float(maxx), float(maxy))
    else:
        bbox = parse_bbox(args.bbox)
        boundary = load_boundary(args.illinois_boundary, bbox)
        poly = boundary.geometry.iloc[0]

    n = int(args.n_controls) if args.n_controls is not None else int(len(dc))
    controls = sample_points_within(
        poly,
        bbox,
        n,
        seed=int(args.seed),
        max_tries=int(args.max_tries),
        avoid_points_wgs84=dc.geometry,
        min_distance_m=float(args.min_distance_m),
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    controls.to_file(out_path, driver="GeoJSON")
    print(f"✅ Wrote control points: {out_path} ({len(controls)} points)")


if __name__ == "__main__":
    main()

