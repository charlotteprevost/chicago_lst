## chicago_lst

Portfolio project focused on **nighttime land surface temperature (LST)** and **data center locations**.

- **Web map**: NASA GIBS tiles + lightweight GeoJSON overlays
- **Analysis**: time series, anomaly/risk scoring, and (next) classification/inference

### Data center address source (Chicago list)

The Chicago data center address list was compiled from `https://www.datacentermap.com/usa/illinois/chicago/`
and pasted into `data/chicago_data_centers_183_source.txt` (then parsed + geocoded by `analysis/` scripts).

## Deploy structure (recommended)

- **Frontend**: GitHub Pages (`frontend/` + `data/` copied by `.github/workflows/pages.yml`)
- **Backend**: Render (TiTiler Docker service from `backend/`)

This matches the repo layout and keeps hosting simple:
- static web app and GeoJSON on Pages
- COG tile serving on Render

## Publish checklist

1. **GitHub Pages**
   - In repo settings, set Pages source to **GitHub Actions**.
   - Push to `main` to trigger `.github/workflows/pages.yml`.
   - Live URL: `https://charlotteprevost.github.io/chicago_lst/`

2. **Render backend (if high-res ECOSTRESS tiles are needed)**
   - Create a Render service from this repo using `render.yaml`.
   - Service name should be `chicago-lst-tiles`.
   - Confirm health endpoint: `/`
   - Expected base URL in frontend config: `https://chicago-lst-tiles.onrender.com`

3. **Frontend config**
   - `frontend/config.js` must point `titilerBaseUrl` to your Render service URL.
   - If the Render URL differs, update this value and push again.
   - Default map layer uses TiTiler high-res ECOSTRESS and auto-falls back to GIBS if TiTiler/COG is unavailable.

