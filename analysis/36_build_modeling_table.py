from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--obs", required=True, help="collapsed_aoi_dt_usable.csv")
    ap.add_argument("--covariates", default=None, help="aoi_covariates.csv (optional)")
    ap.add_argument("--attrs", default=None, help="data center attributes CSV keyed by site_id (optional)")
    ap.add_argument("--out", required=True, help="Output CSV path")
    args = ap.parse_args()

    obs = pd.read_csv(args.obs)
    if "dt" in obs.columns:
        obs["dt"] = pd.to_datetime(obs["dt"], errors="coerce", utc=True)

    out = obs.copy()

    if args.covariates:
        cov = pd.read_csv(args.covariates)
        if "aoi_id" not in cov.columns:
            raise SystemExit("Covariates CSV missing aoi_id")
        out = out.merge(cov, on="aoi_id", how="left", suffixes=("", "_cov"))

    if args.attrs:
        attrs = pd.read_csv(args.attrs)
        if "site_id" not in attrs.columns:
            raise SystemExit("Attrs CSV missing site_id")
        out = out.merge(attrs, on="site_id", how="left", suffixes=("", "_attr"))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    print(f"✅ Wrote: {out_path}")


if __name__ == "__main__":
    main()

