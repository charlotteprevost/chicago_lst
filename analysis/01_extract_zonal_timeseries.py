from __future__ import annotations

import argparse
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import rasterio.mask
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from utils_config import Config, load_config


def parse_date_from_name(name: str, date_regex: str, date_format: str) -> Optional[str]:
    m = re.search(date_regex, name)
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(1), date_format)
        # If the format captures time-of-day, keep a timestamp. Otherwise keep YYYY-MM-DD.
        has_time = any(t in (date_format or "") for t in ("%H", "%M", "%S")) or ("T" in (m.group(1) or ""))
        if has_time:
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        return dt.date().isoformat()
    except Exception:
        return None


def transform_values(arr: np.ndarray, cfg: Config) -> np.ndarray:
    ts = cfg.value_transform
    if ts.type == "identity":
        return arr
    if ts.type == "scale_offset":
        scale = 1.0 if ts.scale is None else float(ts.scale)
        offset = 0.0 if ts.offset is None else float(ts.offset)
        return arr * scale + offset
    raise ValueError(f"Unknown value_transform.type: {ts.type}")


def safe_stat(arr: np.ndarray, stat: str) -> float:
    if arr.size == 0:
        return float("nan")
    finite = np.isfinite(arr)
    if finite.sum() == 0:
        return float("nan")
    vals = arr[finite]
    if stat == "mean":
        return float(vals.mean())
    if stat == "median":
        return float(np.median(vals))
    if stat == "p90":
        return float(np.percentile(vals, 90))
    if stat == "count":
        return float(vals.size)
    raise ValueError(f"Unknown stat: {stat}")


def iter_rasters(raster_dir: str, raster_glob: str) -> List[Path]:
    p = Path(raster_dir)
    return sorted(p.glob(raster_glob))


def load_aois(cfg: Config) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(cfg.aoi_path)
    if gdf.crs is None:
        gdf = gdf.set_crs(cfg.aoi_crs_if_missing)
    if cfg.aoi_id_field not in gdf.columns:
        # fall back to index
        gdf[cfg.aoi_id_field] = gdf.index.astype(str)
    if cfg.buffer_m is not None:
        # buffer in meters: project to a suitable metric CRS
        metric = gdf.estimate_utm_crs() or "EPSG:3857"
        gdf_m = gdf.to_crs(metric)
        gdf_m["geometry"] = gdf_m.geometry.buffer(float(cfg.buffer_m))
        gdf = gdf_m.to_crs(gdf.crs)
    return gdf[[cfg.aoi_id_field, "geometry"]].copy()


def zonal_stats_for_geom(
    src: rasterio.io.DatasetReader,
    geom: BaseGeometry,
    cfg: Config,
    *,
    cloud_src: Optional[rasterio.io.DatasetReader] = None,
    water_src: Optional[rasterio.io.DatasetReader] = None,
    qc_src: Optional[rasterio.io.DatasetReader] = None,
    lst_err_src: Optional[rasterio.io.DatasetReader] = None,
) -> Dict[str, float]:
    # Fast reject: skip non-overlapping shapes (very common with tiled rasters like ECOSTRESS).
    try:
        rb = box(*src.bounds)
        if not rb.intersects(geom):
            return {stat: float("nan") for stat in cfg.stats}
    except Exception:
        # If bounds/geometry checks fail, fall through and let rasterio decide.
        pass

    # Mask and crop to AOI geometry.
    try:
        out_img, _ = rasterio.mask.mask(src, [geom], crop=True, filled=True)
    except ValueError:
        # rasterio raises ValueError when shapes do not overlap raster.
        return {stat: float("nan") for stat in cfg.stats}
    data = out_img[0].astype("float32")

    # Mask explicit nodata
    nd = src.nodata
    if nd is not None:
        data[data == nd] = np.nan
    if cfg.nodata_equals is not None:
        data[data == float(cfg.nodata_equals)] = np.nan
    if cfg.nodata_below is not None:
        data[data < float(cfg.nodata_below)] = np.nan

    # Optional quality masking (ECOSTRESS companion rasters).
    if cfg.quality.enabled and cfg.quality.ecostress_companion_masks:
        keep = np.isfinite(data)

        def _mask_band(src_mask: Optional[rasterio.io.DatasetReader]) -> Optional[np.ndarray]:
            if src_mask is None:
                return None
            try:
                m_img, _ = rasterio.mask.mask(src_mask, [geom], crop=True, filled=True)
            except ValueError:
                return None
            return m_img[0]

        cloud = _mask_band(cloud_src)
        if cloud is not None:
            keep &= np.isin(cloud, list(cfg.quality.keep_cloud_values))

        water = _mask_band(water_src)
        if water is not None:
            keep &= np.isin(water, list(cfg.quality.keep_water_values))

        qc = _mask_band(qc_src)
        if qc is not None:
            qc_class = (qc.astype("uint16") & np.uint16(cfg.quality.qc_class_bitmask)).astype("uint8")
            keep &= np.isin(qc_class, list(cfg.quality.qc_keep_classes))

        if cfg.quality.max_lst_err is not None:
            err = _mask_band(lst_err_src)
            if err is not None:
                keep &= (err.astype("float32") <= float(cfg.quality.max_lst_err))

        data[~keep] = np.nan

    data = transform_values(data, cfg)
    return {stat: safe_stat(data, stat) for stat in cfg.stats}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="Path to config JSON")
    args = ap.parse_args()

    cfg = load_config(args.config)
    rasters = iter_rasters(cfg.raster_dir, cfg.raster_glob)
    if not rasters:
        raise SystemExit(
            f"No rasters found in {cfg.raster_dir!r} with glob {cfg.raster_glob!r}"
        )

    aois = load_aois(cfg)
    out_dir = Path(cfg.outputs_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Cache AOIs projected to each raster CRS (big speedup for tiled rasters).
    aois_by_crs: Dict[str, gpd.GeoDataFrame] = {}

    records: List[Dict[str, object]] = []
    for i, rp in enumerate(rasters):
        d = parse_date_from_name(rp.name, cfg.date_regex, cfg.date_format)
        if d is None:
            # Skip unknown-dated rasters; keep the pipeline strict.
            continue

        with rasterio.open(rp) as src:
            crs_key = None
            try:
                crs_key = src.crs.to_string()  # type: ignore[union-attr]
            except Exception:
                crs_key = str(src.crs)

            if crs_key not in aois_by_crs:
                aois_by_crs[crs_key] = aois.to_crs(src.crs)

            aois_r = aois_by_crs[crs_key]
            # Only process AOIs that intersect this tile's bounds.
            try:
                rb = box(*src.bounds)
                try:
                    idx = list(aois_r.sindex.intersection(rb.bounds))
                    aois_r = aois_r.iloc[idx].copy() if idx else aois_r.iloc[0:0].copy()
                    if not aois_r.empty:
                        aois_r = aois_r[aois_r.intersects(rb)].copy()
                except Exception:
                    aois_r = aois_r[aois_r.intersects(rb)].copy()
            except Exception:
                pass

            if aois_r.empty:
                continue

            # Open ECOSTRESS companion rasters (once per tile) if requested and present.
            cloud_src = water_src = qc_src = lst_err_src = None
            if cfg.quality.enabled and cfg.quality.ecostress_companion_masks and rp.name.endswith("_LST.tif"):
                base = rp.name[: -len("_LST.tif")]
                cand_cloud = rp.with_name(base + cfg.quality.cloud_suffix)
                cand_water = rp.with_name(base + cfg.quality.water_suffix)
                cand_qc = rp.with_name(base + cfg.quality.qc_suffix)
                cand_err = rp.with_name(base + cfg.quality.lst_err_suffix)
                try:
                    if cand_cloud.exists():
                        cloud_src = rasterio.open(cand_cloud)
                    if cand_water.exists():
                        water_src = rasterio.open(cand_water)
                    if cand_qc.exists():
                        qc_src = rasterio.open(cand_qc)
                    if cand_err.exists():
                        lst_err_src = rasterio.open(cand_err)
                except Exception:
                    cloud_src = water_src = qc_src = lst_err_src = None
            for _, row in aois_r.iterrows():
                aoi_id = row[cfg.aoi_id_field]
                stats = zonal_stats_for_geom(
                    src,
                    row.geometry,
                    cfg,
                    cloud_src=cloud_src,
                    water_src=water_src,
                    qc_src=qc_src,
                    lst_err_src=lst_err_src,
                )
                records.append(
                    {
                        "project": cfg.project_name,
                        "date": d,
                        "aoi_id": str(aoi_id),
                        "raster": rp.name,
                        "crs": str(src.crs),
                        "units": cfg.value_units,
                        **stats,
                    }
                )

            for _ds in (cloud_src, water_src, qc_src, lst_err_src):
                try:
                    if _ds is not None:
                        _ds.close()
                except Exception:
                    pass

    if not records:
        # Most commonly: date_regex/date_format doesn't match the filenames.
        examples = [p.name for p in rasters[:20]]
        raise SystemExit(
            "No zonal-stat records were produced.\n\n"
            "Most likely: your `date_regex` / `date_format` did not match any raster filenames, "
            "so every raster was skipped.\n\n"
            f"- raster_dir: {cfg.raster_dir}\n"
            f"- raster_glob: {cfg.raster_glob}\n"
            f"- date_regex: {cfg.date_regex!r}\n"
            f"- date_format: {cfg.date_format!r}\n"
            f"- example filenames (first {len(examples)}):\n"
            + "\n".join([f"  - {e}" for e in examples])
            + "\n"
        )

    df = pd.DataFrame.from_records(records)
    # Preserve original timestamps if present; sort using a parsed datetime helper.
    df["_dt"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
    df = df.sort_values(["aoi_id", "_dt"]).drop(columns=["_dt"]).reset_index(drop=True)

    out_path = out_dir / "timeseries.csv"
    df.to_csv(out_path, index=False)
    print(f"✅ Wrote: {out_path}")


if __name__ == "__main__":
    main()

