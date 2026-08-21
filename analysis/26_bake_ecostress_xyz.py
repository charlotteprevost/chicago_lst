from __future__ import annotations

import argparse
import json
import math
import struct
import zlib
from pathlib import Path
from typing import Any

import numpy as np


WEB_MERCATOR_ORIGIN = 20037508.342789244
TILE_SIZE = 256
EPSG_3857 = "EPSG:3857"

# Inferno-like stops (matches the TiTiler colormap_name=inferno look closely enough).
_INFERNO_STOPS = np.array(
    [
        [0.00, 0, 0, 4],
        [0.25, 87, 16, 110],
        [0.50, 188, 55, 84],
        [0.75, 249, 142, 9],
        [1.00, 252, 255, 164],
    ],
    dtype=np.float64,
)


def tile_bounds_3857(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """WebMercatorQuad tile bounds in EPSG:3857 meters (minx, miny, maxx, maxy)."""
    n = 2 ** int(z)
    size = (WEB_MERCATOR_ORIGIN * 2) / n
    minx = -WEB_MERCATOR_ORIGIN + x * size
    maxy = WEB_MERCATOR_ORIGIN - y * size
    maxx = minx + size
    miny = maxy - size
    return minx, miny, maxx, maxy


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** int(z)
    x = int(math.floor((lon + 180.0) / 360.0 * n))
    lat_rad = math.radians(lat)
    y = int(
        math.floor(
            (1.0 - math.log(math.tan(lat_rad) + (1.0 / math.cos(lat_rad))) / math.pi) / 2.0 * n
        )
    )
    x = min(max(x, 0), n - 1)
    y = min(max(y, 0), n - 1)
    return x, y


def inferno_rgba(values: np.ndarray, vmin: float, vmax: float, nodata_mask: np.ndarray) -> np.ndarray:
    span = max(vmax - vmin, 1e-6)
    t = np.clip((values - vmin) / span, 0.0, 1.0)
    stops = _INFERNO_STOPS
    idx = np.searchsorted(stops[:, 0], t, side="right")
    idx = np.clip(idx, 1, len(stops) - 1)
    x0 = stops[idx - 1, 0]
    x1 = stops[idx, 0]
    w = np.clip((t - x0) / np.maximum(x1 - x0, 1e-9), 0.0, 1.0)[..., None]
    rgb0 = stops[idx - 1, 1:4]
    rgb1 = stops[idx, 1:4]
    rgb = rgb0 + (rgb1 - rgb0) * w
    out = np.zeros(values.shape + (4,), dtype=np.uint8)
    out[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out[..., 3] = np.where(nodata_mask, 0, 255).astype(np.uint8)
    return out


def write_png_rgba(path: Path, arr: np.ndarray) -> None:
    if arr.ndim != 3 or arr.shape[2] != 4:
        raise ValueError("write_png_rgba expects HxWx4 uint8")
    h, w = arr.shape[:2]
    raw = b"".join(b"\x00" + arr[i].tobytes() for i in range(h))
    compressed = zlib.compress(raw, 9)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def update_meta_json(
    meta_json: Path,
    *,
    tiles_url: str = "",
    scene_time: str = "",
    minzoom: int | None = None,
    maxzoom: int | None = None,
) -> dict[str, Any]:
    if meta_json.exists():
        try:
            meta: dict[str, Any] = json.loads(meta_json.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    else:
        meta = {}
    meta.setdefault("tms", "WebMercatorQuad")
    if tiles_url:
        meta["tiles_url"] = tiles_url
    if scene_time:
        meta["scene_time"] = scene_time
    if minzoom is not None:
        meta["minzoom"] = int(minzoom)
    if maxzoom is not None:
        meta["maxzoom"] = int(maxzoom)
    meta_json.parent.mkdir(parents=True, exist_ok=True)
    meta_json.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def _bbox_tiles(west: float, south: float, east: float, north: float, z: int) -> tuple[int, int, int, int]:
    x0, y1 = lonlat_to_tile(west, south, z)
    x1, y0 = lonlat_to_tile(east, north, z)
    xmin, xmax = min(x0, x1), max(x0, x1)
    ymin, ymax = min(y0, y1), max(y0, y1)
    return xmin, ymin, xmax, ymax


def bake_xyz(
    cog_path: str,
    out_dir: Path,
    *,
    minzoom: int,
    maxzoom: int,
    bbox: tuple[float, float, float, float],
    vmin: float,
    vmax: float,
) -> int:
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_bounds
    from rasterio.warp import Resampling, reproject

    west, south, east, north = bbox
    written = 0
    with rasterio.open(cog_path) as src:
        for z in range(minzoom, maxzoom + 1):
            xmin, ymin, xmax, ymax = _bbox_tiles(west, south, east, north, z)
            for x in range(xmin, xmax + 1):
                for y in range(ymin, ymax + 1):
                    minx, miny, maxx, maxy = tile_bounds_3857(z, x, y)
                    dst = np.full((TILE_SIZE, TILE_SIZE), np.nan, dtype=np.float32)
                    dst_transform = from_bounds(minx, miny, maxx, maxy, TILE_SIZE, TILE_SIZE)
                    reproject(
                        source=rasterio.band(src, 1),
                        destination=dst,
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=dst_transform,
                        dst_crs=CRS.from_epsg(3857),
                        resampling=Resampling.bilinear,
                        src_nodata=src.nodata,
                        dst_nodata=np.nan,
                    )
                    nodata = ~np.isfinite(dst)
                    if src.nodata is not None:
                        nodata |= np.isclose(dst, float(src.nodata))
                    if bool(np.all(nodata)):
                        continue
                    rgba = inferno_rgba(np.where(nodata, vmin, dst), vmin, vmax, nodata)
                    write_png_rgba(out_dir / str(z) / str(x) / f"{y}.png", rgba)
                    written += 1
    return written


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Bake a static WebMercatorQuad XYZ PNG pyramid from the ECOSTRESS COG "
            "so the map can skip on-demand TiTiler rendering."
        )
    )
    p.add_argument(
        "--cog",
        default="",
        help="Local COG path, or /vsicurl/<https URL> for the public COG.",
    )
    p.add_argument(
        "--out-dir",
        default="../data/tiles/ecostress",
        help="Output tile root (default: ../data/tiles/ecostress).",
    )
    p.add_argument("--min-zoom", type=int, default=9)
    p.add_argument("--max-zoom", type=int, default=12)
    p.add_argument(
        "--bbox",
        default="",
        help="west,south,east,north in EPSG:4326. Default: data/chicago_dc_aoi.json.",
    )
    p.add_argument("--vmin", type=float, default=0.0, help="Colormap min (°C).")
    p.add_argument("--vmax", type=float, default=45.0, help="Colormap max (°C).")
    p.add_argument(
        "--meta-json",
        default="../data/ecostress_highres_latest.json",
        help="Frontend metadata JSON to update.",
    )
    p.add_argument(
        "--public-tiles-url",
        default="",
        help="Public XYZ template, e.g. https://..../tiles/{z}/{x}/{y}.png",
    )
    p.add_argument("--scene-time", default="", help="ISO-8601 snapshot time written to metadata.")
    p.add_argument(
        "--update-meta-only",
        action="store_true",
        help="Only write tiles_url / scene_time into metadata JSON.",
    )
    return p


def _default_chicago_bbox() -> str:
    p = Path(__file__).resolve().parent.parent / "data" / "chicago_dc_aoi.json"
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8"))
        return f"{raw['west']:.6f},{raw['south']:.6f},{raw['east']:.6f},{raw['north']:.6f}"
    return "-88.380031,41.520783,-87.465940,42.434148"


def main() -> None:
    args = _build_arg_parser().parse_args()
    meta_json = Path(args.meta_json).expanduser().resolve()
    bbox_csv = args.bbox.strip() or _default_chicago_bbox()
    west, south, east, north = [float(v) for v in bbox_csv.split(",")]

    if args.update_meta_only:
        if not args.public_tiles_url:
            raise SystemExit("--update-meta-only requires --public-tiles-url.")
        update_meta_json(
            meta_json,
            tiles_url=args.public_tiles_url,
            scene_time=args.scene_time,
            minzoom=args.min_zoom,
            maxzoom=args.max_zoom,
        )
        print(f"Updated metadata JSON: {meta_json}")
        return

    if not args.cog:
        raise SystemExit("--cog is required unless --update-meta-only is used.")

    out_dir = Path(args.out_dir).expanduser().resolve()
    written = bake_xyz(
        args.cog,
        out_dir,
        minzoom=args.min_zoom,
        maxzoom=args.max_zoom,
        bbox=(west, south, east, north),
        vmin=args.vmin,
        vmax=args.vmax,
    )
    print(f"Wrote {written} PNG tiles under {out_dir}")
    if args.public_tiles_url:
        update_meta_json(
            meta_json,
            tiles_url=args.public_tiles_url,
            scene_time=args.scene_time,
            minzoom=args.min_zoom,
            maxzoom=args.max_zoom,
        )
        print(f"Updated metadata JSON: {meta_json}")
    else:
        print("No --public-tiles-url; metadata JSON unchanged. Host the tiles, then pass the XYZ template.")


if __name__ == "__main__":
    main()
