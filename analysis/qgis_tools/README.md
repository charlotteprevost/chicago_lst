## Canonical QGIS/PyQGIS lane (Chicago LST)

PyQGIS helpers used when converting ECOSTRESS rasters to COG live here. They are optional; the web map uses rasterio/GDAL when PyQGIS is not available.

### Source of truth for this repo

- Analysis scripts: `chicago_lst/analysis/`
- Web-facing outputs: `chicago_lst/data/`
- This module: `chicago_lst/analysis/qgis_tools/`

### Rules

- New QGIS/PyQGIS helpers for Chicago LST go here (or in a submodule under this path).
- Legacy scripts under `_archive/qgis_legacy_candidate/` are reference-only.
- Keep reproducible web-facing outputs in `analysis/` and `data/`.
- Desktop `.qgz` files for other portfolio apps (for example maritime surveillance) are not the source of truth for this project.
