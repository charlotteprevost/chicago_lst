# Methods — Chicago DC Night Heat

Sources, QC, formulas, and interpretation notes. Recruiter-facing summary lives in the [root README](../README.md).

## Data sources

### Thermal imagery and map tiles

- **ECOSTRESS L2T LST (primary high-resolution thermal source)**
  - Accessed via Earthdata tooling in `analysis/22_fetch_ecostress_l2t_il.py`
  - Typical resolution used in this project: ~70 m LST tiles
  - Web map: one same-pass mosaic clipped to the Chicago data-center cluster (`analysis/25_publish_latest_ecostress_cog.py`, `data/chicago_dc_aoi.json`)
- **OpenStreetMap** basemap. If ECOSTRESS tiles fail, the street map stays and a banner reports that temperature tiles are unavailable (no second satellite product; VIIRS night lights are not used as a thermal proxy).

### Data center locations

- Source list page: `https://www.datacentermap.com/usa/illinois/chicago/`
- Raw paste archive: `data/chicago_data_centers_183_source.txt`
- Parsed table: `data/chicago_data_centers_183.csv`
- Geocoded points: `data/chicago_data_centers_183.geojson` (183 listed; 113 mapped with geometry)

### Derived overlays served to frontend

- `data/aoi_risk_latest.geojson` (optional heat-context score overlay)
- `data/dc_effect_cumulative.geojson` (data center vs control effect overlay)
- `data/ecostress_highres_latest.json` (current COG / XYZ pointer)

### Optional covariates (advanced modeling)

The analysis pipeline includes optional covariate extraction/matching steps (`32+` scripts). Those paths are research extras until a result chart ships; they are not required for the portfolio demo.

## Processing and QC pipeline

### Core workflow

1. Generate AOI buffers around data-center points and matched control points.
2. Fetch ECOSTRESS tiles for the requested date range.
3. Extract zonal statistics for each AOI and timestamp.
4. Apply quality controls and convert Kelvin to Celsius.
5. Compute risk and comparative metrics.
6. Export GeoJSON overlays for the web map.

### Quality control and transforms

- **Unit conversion**: `degC = Kelvin - 273.15`
- **QC masking defaults**:
  - keep clear-sky pixels (`cloud == 0`)
  - keep non-water pixels (`water == 0`)
  - keep QC classes `[0, 1]` with `qc_class_bitmask = 3`
- Companion masks referenced by filename convention: `*_cloud.tif`, `*_water.tif`, `*_QC.tif`

## Math and statistics

### A) Heat-context / anomaly layer (`analysis/02_compute_anomaly_and_risk.py`)

For each AOI and baseline group (month or day-of-year):

- `baseline_mean = mean(mean_temperature)`
- `baseline_std = std(mean_temperature)`
- `baseline_p90 = 90th percentile(mean_temperature)`

For each observation:

- `anomaly = mean - baseline_mean`
- `z = anomaly / baseline_std`
- `is_hot_night = 1 if mean > baseline_p90 else 0`

Trend: fit linear model `y = a + b*t` where `y` is AOI mean temperature and `t` is years since first observation (`trend_c_per_year = b`).

Recent hot-night frequency: `hot_nights_14 = sum(is_hot_night over latest 14 observations)`.

Risk score (clipped to 0..100):

- `z_clip = clip(z, -3, 6)`
- `freq_score = (hot_nights_14 / 14) * 25`
- `trend_score = clip(trend_c_per_year, 0, 5) * 5`
- `risk_score = clip(z_clip*10 + freq_score + trend_score, 0, 100)`

### B) Data center vs control (`analysis/23_*` + `analysis/06_*`)

Per timestamp and buffer size:

- `ctrl_mean = mean(control AOI values at same date+buffer)`
- `delta_c_dc_minus_ctrl = dc_value - ctrl_mean`

Cumulative per AOI metrics: `delta_mean_c` (pixel-count weighted), `delta_median_c`, `delta_p90_c`, `dc_mean_c`, `ctrl_mean_c`.

Pre/post opening windows when opening date is known: `dt < opening_date` vs `dt >= opening_date`, with exported counts and weighted means.

**Headline sample (July 2025, 500 m buffers, 113 sites):** mean `delta_mean_c` ≈ **+2.4 °C**. Observational, not causal.

### C) Summary tables

- `timeseries.csv`: zonal outputs per AOI/time
- `timeseries_enriched.csv`: metadata + group labels + optional opening fields
- `summary_effects_by_date_buffer.csv`: DC and control side-by-side
- `regression_ready_rows.csv`: one AOI-time row for downstream modeling

## Opening-date provenance policy

Opening dates in `chicago_data_centers_183.csv`:

- keep fields blank unless a trustworthy source URL is available
- store `went_live_source_url`, `went_live_source_title`, `went_live_source_notes`
- `verified` only with date + source URL; otherwise `needs_research`
- **13 of 113** map sites currently have a verified go-live

## Known limitations

- Data center list coverage is source-dependent and may be incomplete.
- Geocoding/address normalization introduces uncertainty.
- Remote sensing has missingness (cloud, QC masks, temporal gaps).
- Control matching reduces but does not eliminate confounding.
- Comparative deltas are observational indicators, not causal proof.
