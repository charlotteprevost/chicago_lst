## Case Study A — Night Heat Risk Index (time series + anomaly)

This folder is the **analysis pipeline** for `chicago_lst`.

Goal: turn a stack of dated LST rasters (or thermal proxy rasters) into:

- **Tidy time series** per area-of-interest (AOI)
- **Anomaly metrics** (vs. a baseline climatology)
- A **“night heat risk index”** per AOI
- **GeoJSON overlays** that the web map can render

### What you get (outputs)

By default, scripts write to:

- `outputs/timeseries.csv`: one row per (AOI, date)
- `outputs/aoi_summary_latest.csv`: one row per AOI (latest date metrics)
- `outputs/aoi_summary_full.csv`: one row per (AOI, month) baseline + trend metrics
- `../data/aoi_risk_latest.geojson`: GeoJSON overlay for the webapp (joined to AOI geometry)

Note: most generated artifacts are intentionally ignored by git. See `../.gitignore` and
`clean_generated.sh` for one-command cleanup.

### Quick start (demo data)

These commands generate synthetic rasters + AOIs so you can verify the pipeline even if your
real rasters are large or stored elsewhere.

### Clean setup (recommended)

Do **not** install into conda `base`. Create a dedicated env for this project.

```bash
cd /Users/cha/Sync/PRIVATE/10_PROJECTS/webapps/chicago_lst/analysis
conda env create -f environment.yml
conda activate chicago_lst
```

### Earthdata authentication (required to download ECOSTRESS)

To download ECOSTRESS tiles via `earthaccess`, you need an Earthdata login. **Prefer token or `.netrc`** so you don’t have to paste passwords into shells/logs.

- **Token (recommended)**:
  - Create an Earthdata token in your Earthdata profile.
  - `export EARTHDATA_TOKEN="..."`
  - Run fetch/pipeline with `--auth token`.

- **`.netrc` (recommended)**:
  - Configure `~/.netrc` for Earthdata Login and run `chmod 600 ~/.netrc`
  - Run fetch/pipeline with `--auth netrc`.

- **Username/password env vars (works, but avoid long-term)**:
  - `export EARTHDATA_USERNAME="..."`
  - `export EARTHDATA_PASSWORD="..."`
  - Run fetch/pipeline with `--auth environment`.

If you prefer `venv`, you can still use:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python 00_make_demo_data.py
python 01_extract_zonal_timeseries.py --config config.demo.json
python 02_compute_anomaly_and_risk.py --config config.demo.json
python 03_export_geojson.py --config config.demo.json
```

Open the webapp and point it at `data/aoi_risk_latest.geojson` once we wire the layer in.

### Chicago data centers (183 list → CSV → GeoJSON)

We paste a list of 183 Chicago-area data centers into:

- `../data/chicago_data_centers_183_source.txt`
- **Source**: `https://www.datacentermap.com/usa/illinois/chicago/`

Parsed it into a clean CSV:

```bash
python 12_parse_chicago_data_centers.py \
  --input ../data/chicago_data_centers_183_source.txt \
  --output ../data/chicago_data_centers_183.csv
```

Then geocode the locations (to GeoJSON):

```bash
python 13_geocode_data_centers.py \
  --input_csv ../data/chicago_data_centers_183.csv \
  --output_geojson ../data/chicago_data_centers_183.geojson \
  --use_nominatim
```

Now we can buffer these points and run Case Study A (risk scoring) on the buffers.

### AOIs

Create a config (copy `config.example.json`) and set:

- `aoi_path` + `aoi_id_field`
- `raster_dir` (folder containing dated GeoTIFFs)
- `date_regex` + `date_format` so dates parse from filenames
- `value_units` and `value_transform` if needed (Kelvin→C, scale factors, etc.)

### Why this sells to employers

- **Geospatial ETL**: raster → per-area time series
- **Time-series feature engineering**: anomalies, percentiles, heat-night frequency
- **Model-ready tables**: tidy data for Case Study B (classification) and C (drivers/inference)

---

## Illinois ECOSTRESS study (data centers vs controls)

Goal: **Illinois-only**, **on-demand** ECOSTRESS tiled LST (~70 m) pulled via API, then zonal stats on:

- buffered **data center** points (250/500/1000 m)
- buffered **matched control** points (random placement, far from data centers)

Outputs:

- `outputs_ecostress_il_qc/timeseries.csv` (raw zonal stats)
- `outputs_ecostress_il_qc/timeseries_enriched.csv` (adds group/buffer/site metadata)
- `outputs_ecostress_il_qc/summary_effects_by_date_buffer.csv` (DC vs control delta per date+buffer)
- `outputs_ecostress_il_qc/regression_ready_rows.csv` (one row per AOI per timestamp)

### Prereqs (Earthdata auth)

`earthaccess` downloads require a NASA Earthdata login. Use env vars (non-interactive):

```bash
export EARTHDATA_USERNAME="your_username"
export EARTHDATA_PASSWORD="your_password"
```

### 1) Ensure you have data center points

If you don’t already have a `data_centers.geojson`, you can generate one from the pasted list:

```bash
python 12_parse_chicago_data_centers.py \
  --input ../data/chicago_data_centers_183_source.txt \
  --output ../data/chicago_data_centers_183.csv

python 13_geocode_data_centers.py \
  --input_csv ../data/chicago_data_centers_183.csv \
  --output_geojson ../data/chicago_data_centers_183.geojson \
  --use_nominatim
```

### 2) Run the Illinois ECOSTRESS pipeline

Example (30-day window):

```bash
python 23_run_il_ecostress_dc_study.py \
  --data_centers ../data/chicago_data_centers_183.geojson \
  --start 2025-07-01 \
  --end 2025-07-31 \
  --buffers_m 250,500,1000 \
  --outputs_dir outputs_ecostress_il_qc
```

Notes:

- If you have an Illinois boundary polygon, pass `--illinois_boundary path/to/illinois.geojson` to avoid bbox-only controls.
- The pipeline converts **Kelvin → Celsius** and applies **rigorous QC masking** by default (clear-sky, non-water, QC class 0/1) using companion rasters (`*_cloud.tif`, `*_water.tif`, `*_QC.tif`).

### High-resolution tiles in the web map (Render + TiTiler)

NASA GIBS is great for global browse layers, but it does **not** provide ECOSTRESS 70m LST tiles via WMTS. To show high-res ECOSTRESS in the web map, we use:

- **GitHub Pages** for the static frontend (`frontend/`)
- **Render** for a TiTiler-based dynamic tile server (`/cog/...`)

Setup:

- Deploy `render.yaml` on Render (it runs TiTiler in Docker).
- Set `frontend/config.js` → `titilerBaseUrl` to your Render URL.
- Put a **public COG URL** in `data/ecostress_highres_latest.json` (`cog_url`).

Then the “Illinois high‑res • ECOSTRESS LST (70m)” dataset will render as a tile layer.

### 2.5) Build a COG from your raster (PyQGIS-first)

Use the helper script to convert a local raster to COG before upload:

```bash
python 24_make_ecostress_cog.py \
  --input-raster /absolute/path/to/your_latest_lst.tif \
  --output-cog outputs_ecostress_il_qc/ecostress_il_lst_70m_latest.cog.tif \
  --engine auto
```

- `--engine auto` tries **PyQGIS** first, then falls back to rasterio COG driver.
- Recommended upload target: S3/R2/GCS public object URL.

After uploading COG to public storage, update frontend metadata in one command:

```bash
python 24_make_ecostress_cog.py \
  --input-raster /absolute/path/to/your_latest_lst.tif \
  --output-cog outputs_ecostress_il_qc/ecostress_il_lst_70m_latest.cog.tif \
  --public-cog-url "https://your-bucket-or-domain/ecostress_il_lst_70m_latest.cog.tif" \
  --meta-json ../data/ecostress_highres_latest.json
```

This writes the COG URL into `../data/ecostress_highres_latest.json` so the web map can request tiles via TiTiler.

### 2.6) One-command latest publish (auto pick latest timestamp)

If `ecostress_cache/` has many `*_LST.tif` tiles, use the publisher to:

1. pick the **latest timestamp**
2. mosaic that timestamp's tiles (VRT)
3. build one COG
4. optionally upload
5. update `../data/ecostress_highres_latest.json`

```bash
python 25_publish_latest_ecostress_cog.py \
  --cache-dir ecostress_cache \
  --output-cog outputs_ecostress_il_qc/ecostress_il_lst_70m_latest.cog.tif \
  --engine auto
```

#### Upload to your own micha-server hub (SSH)

Example with `rsync`:

```bash
python 25_publish_latest_ecostress_cog.py \
  --cache-dir ecostress_cache \
  --upload-method rsync \
  --upload-target "youruser@yourserver:/var/www/cog/" \
  --public-base-url "https://your-public-domain/cog"
```

Example with `scp`:

```bash
python 25_publish_latest_ecostress_cog.py \
  --cache-dir ecostress_cache \
  --upload-method scp \
  --upload-target "youruser@yourserver:/var/www/cog/" \
  --public-base-url "https://your-public-domain/cog"
```

Requirements for self-hosted COG URL:

- URL must be publicly reachable by Render (HTTPS recommended)
- server must support byte-range requests (`Accept-Ranges: bytes`)
- CORS should allow `https://charlotteprevost.github.io`

### Data center opening dates (verified-only workflow)

To add go-live dates and citation URLs to `../data/chicago_data_centers_183.csv`:

1. Fill `data_center_opening_dates_manual.csv` with verified values.
2. Run:

```bash
python 14_enrich_data_center_opening_dates.py \
  --input-csv ../data/chicago_data_centers_183.csv \
  --manual-seeds-csv data_center_opening_dates_manual.csv \
  --queue-out opening_date_research_queue.csv
```

Outputs:

- updates `../data/chicago_data_centers_183.csv` with:
  - `went_live_date`
  - `went_live_date_precision`
  - `went_live_source_url`
  - `went_live_source_title`
  - `went_live_source_notes`
  - `went_live_status`
- writes unresolved rows to `opening_date_research_queue.csv`

### 3) Collapse duplicate tile hits + filter to usable observations

ECOSTRESS is tiled; the same AOI+timestamp can be hit by multiple tiles. Collapse to one row per AOI per timestamp and filter low-coverage observations:

```bash
python 30_collapse_and_filter_observations.py \
  --input outputs_ecostress_il_qc/regression_ready_rows.csv \
  --out_dir outputs_ecostress_il_qc

python 31_recompute_summary_from_usable.py \
  --input outputs_ecostress_il_qc/collapsed_aoi_dt_usable.csv \
  --out_dir outputs_ecostress_il_qc
```

### 4) Covariates + covariate-matched controls (recommended)

Create a covariate manifest (start from `covariates.example.json`) and extract AOI-level covariates:

```bash
# Option A (recommended): auto-generate a working manifest with remote COG URLs
# (NLCD landcover + Planetary Computer DEM + a VIIRS nightlights collection)
python 25_build_covariates_manifest_pc.py \
  --aois outputs_ecostress_il_qc/aois_all.geojson \
  --out covariates.json

python 32_extract_static_covariates.py \
  --aois outputs_ecostress_il_qc/aois_all.geojson \
  --manifest covariates.json \
  --out outputs_ecostress_il_qc/aoi_covariates.csv

python 34_match_controls_by_covariates.py \
  --collapsed outputs_ecostress_il_qc/collapsed_aoi_dt_usable.csv \
  --covariates outputs_ecostress_il_qc/aoi_covariates.csv \
  --out outputs_ecostress_il_qc/matched_controls.csv
```

### 5) Build modeling table + first-pass model

```bash
python 36_build_modeling_table.py \
  --obs outputs_ecostress_il_qc/collapsed_aoi_dt_usable.csv \
  --covariates outputs_ecostress_il_qc/aoi_covariates.csv \
  --attrs data_center_attributes.csv \
  --out outputs_ecostress_il_qc/modeling_table.csv

python 40_model_panel.py \
  --input outputs_ecostress_il_qc/modeling_table.csv \
  --out_dir outputs_ecostress_il_qc/model
```
