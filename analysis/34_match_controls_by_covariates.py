from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler


@dataclass(frozen=True)
class MatchResult:
    data_center_aoi_id: str
    control_aoi_id: str
    buffer_m: float
    match_rank: int
    distance: float


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collapsed", required=True, help="collapsed_aoi_dt_usable.csv (for AOI metadata)")
    ap.add_argument("--covariates", required=True, help="aoi_covariates.csv from 32_extract_static_covariates.py")
    ap.add_argument("--out", required=True, help="Output matched_controls.csv")
    ap.add_argument("--k", type=int, default=3, help="Controls per data center per buffer")
    ap.add_argument(
        "--features",
        default="impervious_pct__mean,nightlights__mean,elevation_m__mean",
        help="Comma-separated covariate columns to match on",
    )
    ap.add_argument("--no_reuse", action="store_true", help="Do not reuse a control AOI across matches")
    args = ap.parse_args()

    collapsed = pd.read_csv(args.collapsed)
    if "is_usable" in collapsed.columns:
        collapsed = collapsed[collapsed["is_usable"] == True].copy()  # noqa: E712

    # Build AOI-level table (dedupe across time).
    meta_cols = ["aoi_id", "is_data_center", "buffer_m", "site_id"]
    for c in meta_cols:
        if c not in collapsed.columns:
            raise SystemExit(f"Missing column in collapsed: {c}")
    meta = collapsed[meta_cols].drop_duplicates("aoi_id").copy()

    cov = pd.read_csv(args.covariates)
    if "aoi_id" not in cov.columns:
        raise SystemExit("Covariates CSV missing aoi_id")

    df = meta.merge(cov, on="aoi_id", how="left")
    feats: List[str] = [s.strip() for s in args.features.split(",") if s.strip()]
    for f in feats:
        if f not in df.columns:
            raise SystemExit(f"Missing feature column: {f}")

    results: List[MatchResult] = []
    used_controls: set[str] = set()

    for buffer_m, g in df.groupby("buffer_m"):
        dcs = g[g["is_data_center"] == 1].copy()
        ctrls = g[g["is_data_center"] == 0].copy()
        if dcs.empty or ctrls.empty:
            continue

        X_ctrl = ctrls[feats].apply(pd.to_numeric, errors="coerce").to_numpy()
        X_dc = dcs[feats].apply(pd.to_numeric, errors="coerce").to_numpy()

        # Standardize within-buffer.
        scaler = StandardScaler()
        X_all = np.vstack([X_ctrl, X_dc])
        scaler.fit(X_all)
        X_ctrl_s = scaler.transform(X_ctrl)
        X_dc_s = scaler.transform(X_dc)

        # Pairwise distances (euclidean) – buffer-level matching pool.
        # For most rigorous: add calipers + replacement control; we do greedy NN here.
        dist = ((X_dc_s[:, None, :] - X_ctrl_s[None, :, :]) ** 2).sum(axis=2) ** 0.5

        ctrl_ids = ctrls["aoi_id"].astype(str).to_list()
        for i, dc_row in enumerate(dcs.itertuples(index=False)):
            dc_id = str(getattr(dc_row, "aoi_id"))
            # candidate order
            order = np.argsort(dist[i, :])
            picked = 0
            rank = 0
            for j in order:
                c_id = str(ctrl_ids[int(j)])
                if args.no_reuse and c_id in used_controls:
                    continue
                results.append(
                    MatchResult(
                        data_center_aoi_id=dc_id,
                        control_aoi_id=c_id,
                        buffer_m=float(buffer_m),
                        match_rank=rank,
                        distance=float(dist[i, int(j)]),
                    )
                )
                used_controls.add(c_id)
                picked += 1
                rank += 1
                if picked >= int(args.k):
                    break

    out = pd.DataFrame([r.__dict__ for r in results])
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    print(f"✅ Wrote: {out_path} ({len(out)} matches)")


if __name__ == "__main__":
    main()

