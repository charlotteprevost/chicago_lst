#!/usr/bin/env bash
set -euo pipefail

# Run from anywhere; resolve to this script's directory.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"

echo "Cleaning generated artifacts under: ${ROOT}"

rm -rf "${ROOT}/analysis/outputs_ecostress_il" || true
rm -rf "${ROOT}/analysis/outputs_ecostress_il_qc" || true
rm -rf "${ROOT}/analysis/ecostress_cache" || true
rm -rf "${ROOT}/analysis/demo_data/rasters" || true
rm -f  "${ROOT}/analysis/covariates.json" || true
rm -f  "${ROOT}/data/aoi_risk_latest.geojson" || true
rm -rf "${ROOT}/site" || true

echo "✅ Done."

