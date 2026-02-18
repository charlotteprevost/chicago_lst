from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import geopandas as gpd
import requests


STAC_API = "https://planetarycomputer.microsoft.com/api/stac/v1"


@dataclass(frozen=True)
class CovSpec:
    name: str
    ctype: str  # "numeric" | "categorical"
    stats: Optional[List[str]] = None
    classes: Optional[List[int]] = None
    collection_hint: Optional[str] = None
    asset_preference: Optional[List[str]] = None
    datetime: Optional[str] = None


def aoi_bbox_wgs84(aois_path: str) -> List[float]:
    gdf = gpd.read_file(aois_path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    gdf = gdf.to_crs("EPSG:4326")
    minx, miny, maxx, maxy = gdf.total_bounds
    return [float(minx), float(miny), float(maxx), float(maxy)]


def list_collections() -> List[Dict[str, Any]]:
    # STAC collections listing is paginated; follow next links.
    out: List[Dict[str, Any]] = []
    url = f"{STAC_API}/collections"
    while url:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        js = r.json()
        out.extend(js.get("collections", []))
        nxt = None
        for link in js.get("links", []):
            if link.get("rel") == "next":
                nxt = link.get("href")
                break
        url = nxt
    return out


def pick_collection_id(collections: Iterable[Dict[str, Any]], hint: str) -> str:
    hint_l = hint.lower()
    # Prefer exact id match, then substring in id, then substring in title.
    for c in collections:
        if str(c.get("id", "")).lower() == hint_l:
            return str(c["id"])
    for c in collections:
        if hint_l in str(c.get("id", "")).lower():
            return str(c["id"])
    for c in collections:
        if hint_l in str(c.get("title", "")).lower():
            return str(c["id"])
    raise SystemExit(f"Could not find any collection matching hint: {hint!r}")


def suggest_collections(collections: Iterable[Dict[str, Any]], keywords: List[str], limit: int = 25) -> List[str]:
    """
    Return collection ids that match any keyword in id/title, ordered by a simple score.
    """
    scored: List[Tuple[int, str]] = []
    for c in collections:
        cid = str(c.get("id", "")).strip()
        title = str(c.get("title", "")).strip()
        hay = f"{cid} {title}".lower()
        score = 0
        for kw in keywords:
            kw_l = kw.lower()
            if kw_l in hay:
                # id matches are stronger than title matches
                score += 3 if kw_l in cid.lower() else 1
        if score > 0 and cid:
            scored.append((score, cid))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [cid for _, cid in scored[:limit]]


def pick_collection_id_any(collections: Iterable[Dict[str, Any]], hints: List[str]) -> Optional[str]:
    for h in hints:
        try:
            return pick_collection_id(collections, h)
        except SystemExit:
            continue
    return None


def stac_search_first_item(collection_id: str, bbox: List[float], datetime: Optional[str]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"collections": [collection_id], "bbox": bbox, "limit": 1}
    if datetime:
        payload["datetime"] = datetime
    r = requests.post(f"{STAC_API}/search", json=payload, timeout=60)
    r.raise_for_status()
    js = r.json()
    feats = js.get("features", [])
    if not feats:
        raise SystemExit(f"No items found for collection={collection_id!r} bbox={bbox} datetime={datetime!r}")
    return feats[0]


def sign_href(href: str) -> str:
    # Planetary Computer signing endpoint (works for Azure-hosted assets).
    r = requests.get("https://planetarycomputer.microsoft.com/api/sas/v1/sign", params={"href": href}, timeout=60)
    r.raise_for_status()
    return str(r.json()["href"])


def pick_asset_href(item: Dict[str, Any], preference: Optional[List[str]] = None) -> Tuple[str, str]:
    assets: Dict[str, Any] = dict(item.get("assets", {}) or {})
    if not assets:
        raise SystemExit("STAC item has no assets")
    if preference:
        for k in preference:
            if k in assets and "href" in assets[k]:
                return k, str(assets[k]["href"])
    # Fallback: first asset with .tif/.tiff href
    for k, a in assets.items():
        href = str(a.get("href", ""))
        if href.lower().endswith((".tif", ".tiff")):
            return k, href
    # Last resort: first asset
    k0 = next(iter(assets.keys()))
    return k0, str(assets[k0].get("href", ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aois", required=True, help="AOIs GeoJSON (e.g., outputs_ecostress_il_qc/aois_all.geojson)")
    ap.add_argument("--out", default="covariates.json", help="Output manifest JSON path")
    ap.add_argument("--nlcd_year", default="2016", help="NLCD year for landcover (default: 2016)")
    args = ap.parse_args()

    bbox = aoi_bbox_wgs84(args.aois)

    # Static NLCD landcover (direct Azure COG). This is NOT in STAC.
    # Note: the blob container can require a SAS token; we sign it via Planetary Computer.
    nlcd_url_unsigned = f"https://cpdataeuwest.blob.core.windows.net/cpdata/raw/nlcd/conus/30m/{args.nlcd_year}.tif"
    nlcd_url = sign_href(nlcd_url_unsigned)

    # Desired covariates: DEM, nightlights, NLCD landcover.
    # Note: "nightlights" collection availability can change over time; we select it dynamically.
    covs: List[CovSpec] = [
        CovSpec(
            name="elevation_m",
            ctype="numeric",
            stats=["mean"],
            collection_hint="cop-dem-glo-30",
            asset_preference=["data", "dem", "elevation", "asset"],
        ),
        CovSpec(
            name="nightlights",
            ctype="numeric",
            stats=["mean"],
            # We'll resolve this from collections list using several common hints.
            # If none match, we'll skip nightlights (and print suggestions).
            collection_hint="__AUTO__",
            asset_preference=["data", "dnb", "asset", "cog", "image"],
            # Prefer an annual product if available; if not, the first matching collection wins.
            datetime=None,
        ),
        CovSpec(
            name="nlcd_landcover",
            ctype="categorical",
            classes=[21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 82, 90, 95],
        ),
    ]

    collections = list_collections()

    out_covs: List[Dict[str, Any]] = []
    for c in covs:
        if c.name == "nlcd_landcover":
            out_covs.append(
                {
                    "name": c.name,
                    "type": "categorical",
                    "path": nlcd_url,
                    "classes": c.classes,
                }
            )
            continue

        assert c.collection_hint
        if c.name == "nightlights" and c.collection_hint == "__AUTO__":
            # Try a few known naming patterns.
            hints = [
                "nightlights",
                "night-lights",
                "blackmarble",
                "black-marble",
                "dnb",
                "vnp46",
                "eog",
                "noaa",
                "viirs",
            ]
            collection_id = pick_collection_id_any(collections, hints)
            if collection_id is None:
                suggestions = suggest_collections(collections, ["night", "marble", "dnb", "vnp", "eog", "light", "noaa", "viir"])
                print("⚠️ Could not auto-detect a Planetary Computer nightlights collection.")
                if suggestions:
                    print("   Closest collection ids I found:")
                    for s in suggestions[:15]:
                        print(f"   - {s}")
                print("   Proceeding without nightlights; you can add it later by editing covariates.json.")
                continue
        else:
            collection_id = pick_collection_id(collections, c.collection_hint)

        item = stac_search_first_item(collection_id=collection_id, bbox=bbox, datetime=c.datetime)
        asset_key, href = pick_asset_href(item, preference=c.asset_preference)

        # Sign so rasterio can open it.
        signed = sign_href(href)

        out_covs.append(
            {
                "name": c.name,
                "type": c.ctype,
                "path": signed,
                "stats": c.stats or ["mean"],
                "source": {
                    "stac_collection": collection_id,
                    "stac_item": item.get("id"),
                    "asset_key": asset_key,
                },
            }
        )

    manifest = {
        "notes": "Auto-generated covariate manifest (remote COG URLs).",
        "bbox_wgs84": bbox,
        "covariates": out_covs,
    }

    out_path = Path(args.out)
    out_path.write_text(json.dumps(manifest, indent=2))
    print(f"✅ Wrote: {out_path}")
    print("Next:")
    print(f"  python 32_extract_static_covariates.py --aois {args.aois} --manifest {out_path} --out outputs_ecostress_il_qc/aoi_covariates.csv")


if __name__ == "__main__":
    main()

