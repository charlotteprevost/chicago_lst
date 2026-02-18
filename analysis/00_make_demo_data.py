from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box


def main() -> None:
    out_dir = Path(__file__).parent / "demo_data"
    rasters_dir = out_dir / "rasters"
    rasters_dir.mkdir(parents=True, exist_ok=True)

    # Make 3 AOIs over a small lat/lon grid.
    # We'll create a 0.5° x 0.5° raster covering:
    # lon: [-88.2, -87.7], lat: [41.7, 42.2] (rough Chicago-ish bbox)
    # Pixel size: 0.01° (~1 km-ish). 50x50 grid.
    west = -88.2
    north = 42.2
    px = 0.01
    width = 50
    height = 50
    transform = from_origin(west, north, px, px)

    # AOIs: three boxes; one "hotter" region.
    aoi_geoms = [
        ("aoi_cool", box(-88.15, 41.75, -87.95, 41.95)),
        ("aoi_mid", box(-88.00, 41.85, -87.80, 42.05)),
        ("aoi_hotspot", box(-87.92, 41.75, -87.75, 41.92)),
    ]
    gdf = gpd.GeoDataFrame(
        {"aoi_id": [a[0] for a in aoi_geoms]},
        geometry=[a[1] for a in aoi_geoms],
        crs="EPSG:4326",
    )
    gdf.to_file(out_dir / "aois.geojson", driver="GeoJSON")

    # Create 30 days of rasters with:
    # - a seasonal-ish sine wave
    # - a positive trend
    # - a fixed hotspot offset
    # - a bit of noise + nodata edges
    start = date(2025, 1, 1)
    nodata = -9999.0

    xs = np.arange(width)[None, :].repeat(height, axis=0)
    ys = np.arange(height)[:, None].repeat(width, axis=1)

    # hotspot: right-bottom quadrant
    hotspot = (xs > 30) & (ys > 30)

    for i in range(30):
        d = start + timedelta(days=i)
        seasonal = 2.5 * math.sin(i / 30 * 2 * math.pi)
        trend = 0.05 * i  # +0.05°C/day

        base = 18.0 + seasonal + trend
        arr = base + np.random.normal(0, 0.6, size=(height, width)).astype("float32")
        arr[hotspot] += 4.0

        # Add some nodata border pixels to test masking logic.
        arr[:2, :] = nodata
        arr[:, :2] = nodata

        out_path = rasters_dir / f"lst_night_{d.isoformat()}.tif"
        with rasterio.open(
            out_path,
            "w",
            driver="GTiff",
            height=height,
            width=width,
            count=1,
            dtype="float32",
            crs="EPSG:4326",
            transform=transform,
            nodata=nodata,
            compress="lzw",
        ) as dst:
            dst.write(arr, 1)

    print(f"✅ Wrote demo AOIs: {out_dir / 'aois.geojson'}")
    print(f"✅ Wrote demo rasters: {rasters_dir} (30 GeoTIFFs)")


if __name__ == "__main__":
    main()

