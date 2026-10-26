from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


CLIP_CRS = "EPSG:32616"  # UTM 16N — Chicago / northern Illinois, meters
POINT_CRS = "EPSG:4326"
BUFFER_M_DEFAULT = 10000.0


def _load_points_lonlat(path: Path) -> tuple[list[tuple[float, float]], int]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    pts: list[tuple[float, float]] = []
    n_null = 0
    for feat in raw.get("features") or []:
        geom = feat.get("geometry") if isinstance(feat, dict) else None
        if not geom or geom.get("type") != "Point":
            n_null += 1
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            n_null += 1
            continue
        lon, lat = float(coords[0]), float(coords[1])
        if not math.isfinite(lon) or not math.isfinite(lat):
            n_null += 1
            continue
        pts.append((lon, lat))
    if len(pts) < 3:
        raise SystemExit(f"Need at least 3 point geometries in {path} (got {len(pts)}).")
    return pts, n_null


def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone chain. Input/output (lon, lat)."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _feature_collection(
    geom_interface: dict[str, Any],
    buffer_m: float,
    area_km2: float,
    n_points: int,
    method: str,
) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "name": "chicago_dc_aoi",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "name": "Chicago data-center cluster",
                    "buffer_m": float(buffer_m),
                    "clip_crs": CLIP_CRS,
                    "area_km2": round(area_km2, 3),
                    "n_points": n_points,
                    "method": method,
                },
                "geometry": geom_interface,
            }
        ],
    }


def _meta_from_bounds(
    west: float,
    south: float,
    east: float,
    north: float,
    buffer_m: float,
    area_km2: float,
    n_points: int,
    method: str,
) -> dict[str, Any]:
    return {
        "crs": POINT_CRS,
        "clip_crs": CLIP_CRS,
        "buffer_m": float(buffer_m),
        "west": float(west),
        "south": float(south),
        "east": float(east),
        "north": float(north),
        "area_km2": round(area_km2, 3),
        "n_points": n_points,
        "bbox": f"{west:.6f},{south:.6f},{east:.6f},{north:.6f}",
        "method": method,
    }


def _try_metric_aoi(
    points: list[tuple[float, float]],
    buffer_m: float,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Convex hull + buffer in UTM 16N meters, then back to EPSG:4326."""
    try:
        from pyproj import Transformer
        from shapely.geometry import MultiPoint
        from shapely.ops import transform as shp_transform
    except Exception:
        return None

    to_utm = Transformer.from_crs(POINT_CRS, CLIP_CRS, always_xy=True)
    to_wgs = Transformer.from_crs(CLIP_CRS, POINT_CRS, always_xy=True)
    xy = [to_utm.transform(lon, lat) for lon, lat in points]
    hull_m = MultiPoint(xy).convex_hull.buffer(float(buffer_m))
    hull_wgs = shp_transform(lambda x, y, z=None: to_wgs.transform(x, y), hull_m)
    area_km2 = float(hull_m.area) / 1e6
    minx, miny, maxx, maxy = hull_wgs.bounds
    fc = _feature_collection(hull_wgs.__geo_interface__, buffer_m, area_km2, len(points), "utm16n_shapely")
    meta = _meta_from_bounds(minx, miny, maxx, maxy, buffer_m, area_km2, len(points), "utm16n_shapely")
    return fc, meta


def _fallback_aoi(
    points: list[tuple[float, float]],
    buffer_m: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Local equirectangular meters around mean lat (Chicago), then buffer the hull."""
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    lon0 = sum(lons) / len(lons)
    lat0 = sum(lats) / len(lats)
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(lat0))

    def to_xy(lon: float, lat: float) -> tuple[float, float]:
        return ((lon - lon0) * m_per_deg_lon, (lat - lat0) * m_per_deg_lat)

    def to_ll(x: float, y: float) -> tuple[float, float]:
        return (lon0 + x / m_per_deg_lon, lat0 + y / m_per_deg_lat)

    xy = [to_xy(lon, lat) for lon, lat in points]
    hull_xy = convex_hull(xy)
    # Expand hull vertices radially by buffer_m from centroid.
    cx = sum(p[0] for p in hull_xy) / len(hull_xy)
    cy = sum(p[1] for p in hull_xy) / len(hull_xy)
    buffered: list[tuple[float, float]] = []
    for x, y in hull_xy:
        dx, dy = x - cx, y - cy
        dist = math.hypot(dx, dy) or 1.0
        scale = (dist + buffer_m) / dist
        buffered.append((cx + dx * scale, cy + dy * scale))
    ring = [to_ll(x, y) for x, y in buffered]
    ring.append(ring[0])
    xs = [p[0] for p in buffered]
    ys = [p[1] for p in buffered]
    # Shoelace in meters²
    area_m2 = 0.5 * abs(
        sum(xs[i] * ys[(i + 1) % len(xs)] - xs[(i + 1) % len(xs)] * ys[i] for i in range(len(xs)))
    )
    lons_b = [p[0] for p in ring[:-1]]
    lats_b = [p[1] for p in ring[:-1]]
    west, east = min(lons_b), max(lons_b)
    south, north = min(lats_b), max(lats_b)
    geom = {"type": "Polygon", "coordinates": [[[lon, lat] for lon, lat in ring]]}
    fc = _feature_collection(geom, buffer_m, area_m2 / 1e6, len(points), "equirectangular_fallback")
    meta = _meta_from_bounds(west, south, east, north, buffer_m, area_m2 / 1e6, len(points), "equirectangular_fallback")
    return fc, meta


def load_bbox_wsen(path: Path) -> tuple[float, float, float, float]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return float(raw["west"]), float(raw["south"]), float(raw["east"]), float(raw["north"])


def bbox_csv(path: Path) -> str:
    w, s, e, n = load_bbox_wsen(path)
    return f"{w:.6f},{s:.6f},{e:.6f},{n:.6f}"


def default_aoi_json(here: Path | None = None) -> Path:
    root = (here or Path(__file__).parent).resolve().parent
    return root / "data" / "chicago_dc_aoi.json"


def build_aoi(points: list[tuple[float, float]], buffer_m: float) -> tuple[dict[str, Any], dict[str, Any]]:
    built = _try_metric_aoi(points, buffer_m)
    if built is not None:
        return built
    return _fallback_aoi(points, buffer_m)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Build a Chicago data-center cluster AOI: convex hull plus a metric buffer "
            f"(default {int(BUFFER_M_DEFAULT)} m in {CLIP_CRS})."
        )
    )
    ap.add_argument(
        "--points",
        default="../data/chicago_data_centers_183.geojson",
        help="Input DC points GeoJSON (EPSG:4326).",
    )
    ap.add_argument("--buffer-m", type=float, default=BUFFER_M_DEFAULT)
    ap.add_argument("--out-geojson", default="../data/chicago_dc_aoi.geojson")
    ap.add_argument("--out-json", default="../data/chicago_dc_aoi.json")
    args = ap.parse_args()

    here = Path(__file__).parent
    points_path = (here / args.points).resolve() if not Path(args.points).is_absolute() else Path(args.points)
    pts, n_null = _load_points_lonlat(points_path)
    fc, meta = build_aoi(pts, float(args.buffer_m))
    meta["n_dropped_null"] = n_null
    if fc["features"]:
        fc["features"][0]["properties"]["n_dropped_null"] = n_null

    out_gj = (here / args.out_geojson).resolve() if not Path(args.out_geojson).is_absolute() else Path(args.out_geojson)
    out_js = (here / args.out_json).resolve() if not Path(args.out_json).is_absolute() else Path(args.out_json)
    out_gj.parent.mkdir(parents=True, exist_ok=True)
    out_gj.write_text(json.dumps(fc, indent=2) + "\n", encoding="utf-8")
    out_js.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out_gj}")
    print(f"Wrote {out_js}")
    print(
        f"n_points={meta['n_points']} dropped_null={n_null} "
        f"bbox={meta['bbox']} area_km2={meta['area_km2']} crs_clip={CLIP_CRS}"
    )


if __name__ == "__main__":
    main()
