from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input_csv", required=True)
    ap.add_argument("--output_geojson", required=True)
    ap.add_argument(
        "--use_nominatim",
        action="store_true",
        help="Geocode via OpenStreetMap Nominatim (rate-limited). Requires network access.",
    )
    ap.add_argument("--address_field", default="full_address")
    ap.add_argument("--name_field", default="name")
    args = ap.parse_args()

    df = pd.read_csv(args.input_csv)

    # If lat/lon already exist, use them.
    if {"lat", "lon"}.issubset(df.columns) and df["lat"].notna().any() and df["lon"].notna().any():
        gdf = gpd.GeoDataFrame(
            df,
            geometry=[Point(xy) for xy in zip(df["lon"], df["lat"])],
            crs="EPSG:4326",
        )
        out = Path(args.output_geojson)
        out.parent.mkdir(parents=True, exist_ok=True)
        gdf.to_file(out, driver="GeoJSON")
        print(f"✅ Wrote: {out}")
        return

    if not args.use_nominatim:
        raise SystemExit(
            "No lat/lon columns found. Re-run with --use_nominatim to geocode, "
            "or add lat/lon columns to the CSV."
        )

    # Geocode via Nominatim (respectful rate limiting).
    from geopy.extra.rate_limiter import RateLimiter
    from geopy.geocoders import Nominatim

    geolocator = Nominatim(user_agent="urban_heat_live_dc_geocoder", timeout=15)
    geocode = RateLimiter(
        geolocator.geocode,
        min_delay_seconds=1,
        max_retries=3,
        error_wait_seconds=5,
        swallow_exceptions=True,
    )

    lats = []
    lons = []
    geoms = []
    for _, row in df.iterrows():
        addr = str(row.get(args.address_field, "") or "").strip()
        loc = geocode(addr) if addr else None
        if loc:
            lats.append(loc.latitude)
            lons.append(loc.longitude)
            geoms.append(Point(loc.longitude, loc.latitude))
        else:
            lats.append(None)
            lons.append(None)
            geoms.append(None)

    df["lat"] = lats
    df["lon"] = lons
    gdf = gpd.GeoDataFrame(df, geometry=geoms, crs="EPSG:4326")

    out = Path(args.output_geojson)
    out.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out, driver="GeoJSON")
    print(f"✅ Wrote: {out}")


if __name__ == "__main__":
    main()

