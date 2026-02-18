from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import rasterio.mask


def safe_numeric(arr: np.ndarray, stat: str) -> float:
    finite = np.isfinite(arr)
    if finite.sum() == 0:
        return float("nan")
    v = arr[finite]
    if stat == "mean":
        return float(v.mean())
    if stat == "median":
        return float(np.median(v))
    if stat == "p90":
        return float(np.percentile(v, 90))
    if stat == "count":
        return float(v.size)
    raise ValueError(f"Unknown stat: {stat}")


def numeric_zonal(src: rasterio.io.DatasetReader, geom, stats: List[str]) -> Dict[str, float]:
    try:
        out_img, _ = rasterio.mask.mask(src, [geom], crop=True, filled=True)
    except ValueError:
        return {s: float("nan") for s in stats}
    data = out_img[0].astype("float32")
    nd = src.nodata
    if nd is not None:
        data[data == nd] = np.nan
    return {s: safe_numeric(data, s) for s in stats}


def categorical_zonal(src: rasterio.io.DatasetReader, geom, classes: List[int]) -> Dict[str, float]:
    try:
        out_img, _ = rasterio.mask.mask(src, [geom], crop=True, filled=True)
    except ValueError:
        return {f"frac_{c}": float("nan") for c in classes} | {"mode": float("nan")}
    data = out_img[0]
    nd = src.nodata
    if nd is not None:
        data = np.where(data == nd, -999999, data)
    flat = data.ravel()
    flat = flat[flat != -999999]
    if flat.size == 0:
        return {f"frac_{c}": float("nan") for c in classes} | {"mode": float("nan")}

    out: Dict[str, float] = {}
    # mode
    vals, counts = np.unique(flat, return_counts=True)
    out["mode"] = float(vals[int(np.argmax(counts))])
    # fractions for requested classes
    denom = float(flat.size)
    for c in classes:
        out[f"frac_{c}"] = float((flat == c).sum()) / denom
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aois", required=True, help="AOIs GeoJSON (aois_all.geojson)")
    ap.add_argument("--manifest", required=True, help="Covariate manifest JSON")
    ap.add_argument("--out", required=True, help="Output CSV path")
    args = ap.parse_args()

    aois = gpd.read_file(args.aois)
    if aois.crs is None:
        aois = aois.set_crs("EPSG:4326")
    if "aoi_id" not in aois.columns:
        raise SystemExit("AOIs missing required field: aoi_id")

    raw: Dict[str, Any] = json.loads(Path(args.manifest).read_text())
    covs: List[Dict[str, Any]] = list(raw.get("covariates", []))
    if not covs:
        raise SystemExit("Manifest has no covariates[]")

    rows: List[Dict[str, Any]] = []
    for _, a in aois.iterrows():
        rows.append({"aoi_id": str(a["aoi_id"])})
    out_df = pd.DataFrame(rows).set_index("aoi_id")

    for cov in covs:
        name = cov["name"]
        ctype = cov.get("type", "numeric")
        path = cov["path"]

        with rasterio.open(path) as src:
            aois_r = aois.to_crs(src.crs)
            if ctype == "numeric":
                stats = list(cov.get("stats", ["mean"]))
                vals: List[Dict[str, float]] = []
                for _, a in aois_r.iterrows():
                    vals.append(numeric_zonal(src, a.geometry, stats))
                tmp = pd.DataFrame(vals)
                tmp.index = aois["aoi_id"].astype(str).to_numpy()
                tmp = tmp.add_prefix(f"{name}__")
                out_df = out_df.join(tmp, how="left")
            elif ctype == "categorical":
                classes = list(map(int, cov.get("classes", [])))
                if not classes:
                    raise SystemExit(f"categorical covariate {name!r} missing classes[]")
                vals2: List[Dict[str, float]] = []
                for _, a in aois_r.iterrows():
                    vals2.append(categorical_zonal(src, a.geometry, classes))
                tmp2 = pd.DataFrame(vals2)
                tmp2.index = aois["aoi_id"].astype(str).to_numpy()
                tmp2 = tmp2.rename(columns={"mode": f"{name}__mode"}).rename(
                    columns={f"frac_{c}": f"{name}__frac_{c}" for c in classes}
                )
                out_df = out_df.join(tmp2, how="left")
            else:
                raise SystemExit(f"Unknown covariate type: {ctype}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.reset_index().to_csv(out_path, index=False)
    print(f"✅ Wrote: {out_path}")


if __name__ == "__main__":
    main()

