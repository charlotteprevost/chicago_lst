from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote


def run(cmd: list[str], cwd: Path | None = None) -> None:
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    if p.returncode != 0:
        raise SystemExit(f"Command failed ({p.returncode}): {' '.join(cmd)}")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=(
            "Publish one ECOSTRESS LST mosaic: pick the same-pass group with the best "
            "Chicago data-center coverage (not blindly latest), clip to the DC AOI, "
            "build a COG, optionally upload, and write scene_time + coverage into metadata."
        )
    )
    ap.add_argument("--cache-dir", default="ecostress_cache")
    ap.add_argument("--raster-glob", default="*_LST.tif")
    ap.add_argument("--date-regex", default=r"(\d{8}T\d{6})")
    ap.add_argument("--date-format", default="%Y%m%dT%H%M%S")
    ap.add_argument(
        "--select",
        choices=["max_coverage", "latest"],
        default="max_coverage",
        help="Which same-pass group to mosaic (default: max_coverage).",
    )
    ap.add_argument(
        "--dc-points",
        default="../data/chicago_data_centers_183.geojson",
        help="DC points used to score pass coverage.",
    )
    ap.add_argument(
        "--aoi-geojson",
        default="../data/chicago_dc_aoi.geojson",
        help="Clip polygon (hull + buffer). Skip clip if the file is missing.",
    )
    ap.add_argument("--coverage-buffer-m", type=float, default=500.0)
    ap.add_argument("--min-pixels", type=float, default=5.0, help="Usable 500 m buffer needs at least this many finite pixels.")
    ap.add_argument(
        "--output-cog",
        default="outputs_ecostress_il_qc/ecostress_il_lst_70m_latest.cog.tif",
    )
    ap.add_argument("--meta-json", default="../data/ecostress_highres_latest.json")
    ap.add_argument("--engine", choices=["auto", "pyqgis", "rasterio"], default="auto")
    ap.add_argument("--compression", choices=["DEFLATE", "LZW", "ZSTD", "NONE"], default="DEFLATE")
    ap.add_argument(
        "--overview-resampling",
        choices=["nearest", "average", "bilinear", "cubic", "lanczos"],
        default="average",
    )
    ap.add_argument("--upload-method", choices=["none", "scp", "rsync", "copy"], default="none")
    ap.add_argument("--upload-target", default="")
    ap.add_argument("--public-base-url", default="")
    ap.add_argument("--no-meta-update", action="store_true")
    ap.add_argument(
        "--score-only",
        action="store_true",
        help="Write pass scores into metadata coverage and exit (no mosaic/COG).",
    )
    return ap.parse_args()


def extract_timestamp_token(name: str, pattern: str) -> str | None:
    import re

    m = re.search(pattern, name)
    if not m:
        return None
    return m.group(1)


def group_by_timestamp(
    files: Iterable[Path], date_regex: str, date_format: str, cluster_seconds: int = 120
) -> dict[datetime, list[Path]]:
    groups: dict[datetime, list[Path]] = defaultdict(list)
    for fp in files:
        tok = extract_timestamp_token(fp.name, date_regex)
        if not tok:
            continue
        try:
            dt = datetime.strptime(tok, date_format)
        except Exception:
            continue
        groups[dt].append(fp)
    if not groups:
        raise SystemExit("No rasters matched timestamp parse. Check --raster-glob/--date-regex/--date-format.")
    return cluster_pass_groups(dict(groups), cluster_seconds)


def cluster_pass_groups(groups: dict[datetime, list[Path]], window_s: int = 120) -> dict[datetime, list[Path]]:
    """Merge filename clocks within ±window_s (same ISS overpass, mixed UTM tiles)."""
    if window_s <= 0:
        return groups
    dts = sorted(groups)
    clusters: list[list[datetime]] = []
    current = [dts[0]]
    for dt in dts[1:]:
        if (dt - current[-1]).total_seconds() <= float(window_s):
            current.append(dt)
        else:
            clusters.append(current)
            current = [dt]
    clusters.append(current)
    merged: dict[datetime, list[Path]] = {}
    for cluster in clusters:
        files: list[Path] = []
        for dt in cluster:
            files.extend(groups[dt])
        key = max(cluster, key=lambda d: (len(groups[d]), d))
        merged[key] = sorted(set(files))
    return merged


def choose_latest_group(files: Iterable[Path], date_regex: str, date_format: str) -> tuple[datetime, list[Path]]:
    groups = group_by_timestamp(files, date_regex, date_format)
    latest_dt = max(groups.keys())
    return latest_dt, sorted(groups[latest_dt])


def iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_dc_points(path: Path) -> list[tuple[float, float]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    pts: list[tuple[float, float]] = []
    for feat in raw.get("features") or []:
        geom = feat.get("geometry") if isinstance(feat, dict) else None
        if not geom or geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        if math.isfinite(lon) and math.isfinite(lat):
            pts.append((lon, lat))
    if not pts:
        raise SystemExit(f"No point geometries in {path}")
    return pts


def _is_nodata(value: float, nodata: float | None) -> bool:
    if not math.isfinite(value):
        return True
    if nodata is None:
        return False
    try:
        nd = float(nodata)
    except (TypeError, ValueError):
        return False
    if math.isnan(nd):
        return False
    return math.isclose(value, nd, rel_tol=0.0, abs_tol=1e-6)


def score_pass_point_coverage(tiles: list[Path], dc_points: list[tuple[float, float]]) -> int:
    """Count DC points with at least one finite LST pixel in this same-pass mosaic."""
    import rasterio
    from rasterio.warp import transform as rio_transform

    covered = [False] * len(dc_points)
    remaining = set(range(len(dc_points)))
    for tile in tiles:
        if not remaining:
            break
        with rasterio.open(tile) as src:
            order = list(remaining)
            lons = [dc_points[i][0] for i in order]
            lats = [dc_points[i][1] for i in order]
            xs, ys = rio_transform("EPSG:4326", src.crs, lons, lats)
            samples = list(src.sample(zip(xs, ys)))
            nodata = src.nodata
            still: set[int] = set()
            for idx, sample in zip(order, samples):
                val = float(sample[0]) if sample is not None and len(sample) else float("nan")
                if _is_nodata(val, nodata):
                    still.add(idx)
                    continue
                covered[idx] = True
            remaining = still
    return int(sum(1 for flag in covered if flag))


def count_buffers_with_pixels(
    raster_path: Path,
    dc_points: list[tuple[float, float]],
    buffer_m: float,
    min_pixels: float,
) -> int:
    """Share of 500 m buffers with usable pixels on the clipped mosaic."""
    import numpy as np
    import rasterio
    from pyproj import Transformer
    from rasterio.features import geometry_mask
    from shapely.geometry import Point
    from shapely.ops import transform as shp_transform

    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32616", always_xy=True)
    n_ok = 0
    with rasterio.open(raster_path) as src:
        to_raster = Transformer.from_crs("EPSG:32616", src.crs, always_xy=True)
        data = src.read(1)
        nodata = src.nodata
        valid = np.isfinite(data)
        if nodata is not None and not (isinstance(nodata, float) and math.isnan(float(nodata))):
            valid &= ~np.isclose(data, float(nodata))
        for lon, lat in dc_points:
            x, y = to_utm.transform(lon, lat)
            circ = Point(x, y).buffer(float(buffer_m))
            geom = shp_transform(lambda xx, yy, zz=None: to_raster.transform(xx, yy), circ)
            mask = geometry_mask([geom], out_shape=data.shape, transform=src.transform, invert=True)
            if int(np.count_nonzero(valid & mask)) >= float(min_pixels):
                n_ok += 1
    return n_ok


def clip_raster_to_aoi(src_path: Path, dst_path: Path, aoi_geojson: Path) -> None:
    import rasterio
    from rasterio.mask import mask
    from rasterio.warp import transform_geom
    from shapely.geometry import mapping, shape

    fc = json.loads(aoi_geojson.read_text(encoding="utf-8"))
    geoms_wgs = [mapping(shape(f["geometry"])) for f in fc.get("features") or [] if f.get("geometry")]
    if not geoms_wgs:
        raise SystemExit(f"No clip geometry in {aoi_geojson}")
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(src_path) as src:
        geoms = [transform_geom("EPSG:4326", src.crs, g) for g in geoms_wgs]
        nodata = src.nodata if src.nodata is not None else -9999.0
        out_image, out_transform = mask(src, geoms, crop=True, nodata=nodata)
        profile = src.profile.copy()
        profile.update(
            {
                "height": out_image.shape[1],
                "width": out_image.shape[2],
                "transform": out_transform,
                "nodata": nodata,
                "driver": "GTiff",
            }
        )
        with rasterio.open(dst_path, "w", **profile) as dst:
            dst.write(out_image)


def kelvin_to_celsius_if_needed(path: Path) -> None:
    """L2T LST is Kelvin; the web colormap expects Celsius (0–45)."""
    import numpy as np
    import rasterio

    with rasterio.open(path, "r+") as src:
        data = src.read(1)
        valid = np.isfinite(data)
        if not valid.any():
            return
        med = float(np.nanmedian(data[valid]))
        if med < 200:
            return
        data = data - 273.15
        src.write(data.astype(src.dtypes[0]), 1)
        print(f"Converted Kelvin → Celsius in {path.name} (median was {med:.1f} K)")


def mosaic_to_utm16(tiles: list[Path], dst: Path) -> None:
    """Warp mixed UTM 15N/16N L2T tiles into one EPSG:32616 mosaic (Chicago)."""
    if not shutil.which("gdalwarp"):
        raise SystemExit("gdalwarp not found. Install GDAL/QGIS.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    cmd = [
        "gdalwarp",
        "-t_srs",
        "EPSG:32616",
        "-r",
        "near",
        "-overwrite",
        "-multi",
    ] + [str(p) for p in tiles] + [str(dst)]
    run(cmd)


def upload_file(src: Path, method: str, target: str) -> None:
    if method == "none":
        return
    if not target.strip():
        raise SystemExit("--upload-target is required when --upload-method is not 'none'.")
    if method == "scp":
        run(["scp", str(src), target])
        return
    if method == "rsync":
        run(["rsync", "-av", str(src), target])
        return
    if method == "copy":
        dst_dir = Path(target).expanduser().resolve()
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / src.name
        shutil.copy2(src, dst)
        print(f"Copied to: {dst}")
        return
    raise SystemExit(f"Unsupported upload method: {method}")


def join_public_url(base: str, filename: str) -> str:
    b = base.rstrip("/")
    return f"{b}/{quote(filename)}"


def merge_meta_json(meta_json: Path, extra: dict[str, Any]) -> dict[str, Any]:
    if meta_json.exists():
        try:
            meta: dict[str, Any] = json.loads(meta_json.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    else:
        meta = {}
    meta.setdefault("tms", "WebMercatorQuad")
    meta.setdefault(
        "render",
        {"colormap_name": "inferno", "rescale": "0,45", "format": "png"},
    )
    meta.update(extra)
    meta_json.parent.mkdir(parents=True, exist_ok=True)
    meta_json.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"Updated metadata JSON: {meta_json}")
    return meta


def select_pass(
    groups: dict[datetime, list[Path]],
    dc_points: list[tuple[float, float]],
    mode: str,
) -> tuple[datetime, list[Path], dict[str, Any]]:
    latest_dt = max(groups.keys())
    latest_n = score_pass_point_coverage(groups[latest_dt], dc_points)
    scores: list[tuple[int, datetime]] = [(latest_n, latest_dt)]
    if mode == "latest":
        chosen_dt, chosen_n = latest_dt, latest_n
    else:
        best_dt, best_n = latest_dt, latest_n
        n_groups = len(groups)
        for i, (dt, tiles) in enumerate(sorted(groups.items(), key=lambda kv: kv[0], reverse=True), start=1):
            if dt == latest_dt:
                continue
            n = score_pass_point_coverage(tiles, dc_points)
            scores.append((n, dt))
            if i == 1 or i % 10 == 0 or n > best_n:
                print(f"  scored {i}/{n_groups} {iso_z(dt)} -> {n}/{len(dc_points)} (best {best_n})")
            if n > best_n or (n == best_n and dt > best_dt):
                best_n, best_dt = n, dt
        chosen_dt, chosen_n = best_dt, best_n
    stats = {
        "n_dc_sites": len(dc_points),
        "n_dc_with_pixels": int(chosen_n),
        "frac": round(chosen_n / max(len(dc_points), 1), 4),
        "select": mode,
        "latest_scene_time": iso_z(latest_dt),
        "latest_n_dc_with_pixels": int(latest_n),
        "buffer_m": 500,
        "method": "point_sample_then_buffer_count",
    }
    return chosen_dt, sorted(groups[chosen_dt]), stats


def main() -> None:
    args = parse_args()
    here = Path(__file__).parent
    cache_dir = (here / args.cache_dir).resolve()
    if not cache_dir.exists():
        raise SystemExit(f"Cache dir not found: {cache_dir}")

    candidates = sorted(cache_dir.glob(args.raster_glob))
    if not candidates:
        raise SystemExit(f"No rasters found in {cache_dir} matching {args.raster_glob}")

    dc_path = (here / args.dc_points).resolve() if not Path(args.dc_points).is_absolute() else Path(args.dc_points)
    dc_points = load_dc_points(dc_path)
    groups = group_by_timestamp(candidates, args.date_regex, args.date_format)
    chosen_dt, tiles, coverage = select_pass(groups, dc_points, args.select)
    print(
        f"Selected {iso_z(chosen_dt)} ({len(tiles)} tiles) "
        f"coverage {coverage['n_dc_with_pixels']}/{coverage['n_dc_sites']} "
        f"(latest {coverage['latest_scene_time']} = {coverage['latest_n_dc_with_pixels']})"
    )

    meta_json = (here / args.meta_json).resolve() if not Path(args.meta_json).is_absolute() else Path(args.meta_json)
    if args.score_only:
        published = dict(coverage)
        published["best_scene_time"] = iso_z(chosen_dt)
        published["best_n_dc_with_pixels"] = int(coverage["n_dc_with_pixels"])
        published["n_dc_with_pixels"] = int(coverage["latest_n_dc_with_pixels"])
        published["frac"] = round(
            coverage["latest_n_dc_with_pixels"] / max(coverage["n_dc_sites"], 1), 4
        )
        merge_meta_json(
            meta_json,
            {
                "note": "Updated by analysis/25_publish_latest_ecostress_cog.py (score-only)",
                "minzoom": 9,
                "maxzoom": 12,
                "coverage": published,
            },
        )
        return

    out_cog = (here / args.output_cog).resolve()
    out_cog.parent.mkdir(parents=True, exist_ok=True)

    if len(tiles) == 1:
        source_for_cog = tiles[0]
        print(f"Single tile selected: {source_for_cog.name}")
    else:
        warped = out_cog.with_suffix(".utm16.tif")
        mosaic_to_utm16(tiles, warped)
        source_for_cog = warped
        print(f"Warped same-pass mosaic to EPSG:32616: {warped}")

    aoi_path = (here / args.aoi_geojson).resolve() if not Path(args.aoi_geojson).is_absolute() else Path(args.aoi_geojson)
    if aoi_path.exists():
        clipped = out_cog.with_suffix(".clip.tif")
        clip_raster_to_aoi(source_for_cog, clipped, aoi_path)
        source_for_cog = clipped
        print(f"Clipped mosaic to AOI: {clipped}")
        kelvin_to_celsius_if_needed(clipped)
        try:
            n_buf = count_buffers_with_pixels(
                clipped, dc_points, float(args.coverage_buffer_m), float(args.min_pixels)
            )
            coverage["n_dc_with_pixels"] = int(n_buf)
            coverage["frac"] = round(n_buf / max(len(dc_points), 1), 4)
            coverage["buffer_m"] = float(args.coverage_buffer_m)
            coverage["min_pixels"] = float(args.min_pixels)
            print(f"500 m buffer coverage on clip: {n_buf}/{len(dc_points)}")
        except Exception as exc:
            print(f"Buffer coverage count skipped ({exc})")
    else:
        print(f"AOI GeoJSON not found ({aoi_path}); publishing unclipped mosaic.")

    cog_builder = here / "24_make_ecostress_cog.py"
    cmd = [
        "python3",
        str(cog_builder),
        "--input-raster",
        str(source_for_cog),
        "--output-cog",
        str(out_cog),
        "--engine",
        args.engine,
        "--compression",
        args.compression,
        "--overview-resampling",
        args.overview_resampling,
    ]
    run(cmd, cwd=here)

    upload_file(out_cog, args.upload_method, args.upload_target)

    extra: dict[str, Any] = {
        "note": "Updated by analysis/25_publish_latest_ecostress_cog.py",
        "minzoom": 9,
        "maxzoom": 12,
        "coverage": coverage,
    }
    uploaded_public = bool(args.public_base_url.strip()) and not args.no_meta_update
    if uploaded_public:
        extra["scene_time"] = iso_z(chosen_dt)
        extra["cog_url"] = join_public_url(args.public_base_url, out_cog.name)
        extra["coverage"] = coverage
        cmd_meta = [
            "python3",
            str(cog_builder),
            "--update-meta-only",
            "--public-cog-url",
            extra["cog_url"],
            "--meta-json",
            str(meta_json),
        ]
        run(cmd_meta, cwd=here)
        merge_meta_json(meta_json, extra)
    elif args.no_meta_update:
        print("Metadata update skipped (--no-meta-update).")
    else:
        # Public COG is still the previously published file (usually latest). Don't
        # relabel scene_time until that file is replaced.
        published = dict(coverage)
        published["best_scene_time"] = iso_z(chosen_dt)
        published["best_n_dc_with_pixels"] = int(coverage["n_dc_with_pixels"])
        published["n_dc_with_pixels"] = int(coverage["latest_n_dc_with_pixels"])
        published["frac"] = round(
            coverage["latest_n_dc_with_pixels"] / max(coverage["n_dc_sites"], 1), 4
        )
        extra["coverage"] = published
        extra["scene_time"] = iso_z(chosen_dt)
        merge_meta_json(meta_json, extra)
        print("No --public-base-url; kept existing cog_url; wrote scene_time/coverage for the local mosaic.")

    print(f"Done. COG: {out_cog}")


if __name__ == "__main__":
    main()
