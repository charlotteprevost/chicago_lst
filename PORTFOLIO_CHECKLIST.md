# Portfolio checklist — `chicago_lst`

Goal: make this project “portfolio‑ready” for **Remote Sensing / Raster Analytics Engineer** (secondary) while still readable for GeoSE roles.

## This week (high‑leverage)

- [ ] **Screenshots**: 2–3 screenshots showing high‑res layer + overlays (risk AOIs / data centers / effects).
- [ ] **Demo flow** (README): add a short “How to reproduce” flow:
  - run analysis pipeline → generate GeoJSON → open frontend → (optional) TiTiler layer.
- [ ] **Remote sensing rigor** (README): explicitly call out:
  - unit conversion (Kelvin→C), QC masking, nodata handling, CRS/units.
- [ ] **Outputs**: ensure `analysis/README.md` clearly lists outputs that feed the webapp.

## Nice to have (next)

- [ ] Add a small “Methods” note (anomaly/risk index definition + baseline choice).
- [ ] Add a quick QA/QC checklist (alignment, resampling, scaling factors).
- [ ] Add a “What I’d do next” section (modeling table + first-pass model).

