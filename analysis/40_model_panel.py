from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Modeling table (e.g., output of 36_build_modeling_table.py)")
    ap.add_argument("--out_dir", required=True, help="Output directory")
    ap.add_argument("--y", default="mean", help="Outcome column (default: mean)")
    ap.add_argument(
        "--x",
        default="is_data_center + capacity_mw + C(tier) + opening_year",
        help="RHS of formula (without fixed effects)",
    )
    ap.add_argument(
        "--fixed_effects",
        default="day",
        choices=["none", "day"],
        help="Add time fixed effects (default: day)",
    )
    ap.add_argument("--cluster", default="site_id", help="Cluster-robust SE group column")
    ap.add_argument("--weight_col", default="pixels", help="Optional weights column (default: pixels)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.input)
    if "dt" in df.columns:
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce", utc=True)
        df["day"] = df["dt"].dt.strftime("%Y-%m-%d")
    else:
        df["day"] = pd.to_datetime(df["date"], errors="coerce", utc=True).dt.strftime("%Y-%m-%d")

    if "is_usable" in df.columns:
        df = df[df["is_usable"] == True].copy()  # noqa: E712

    y = args.y
    if y not in df.columns:
        raise SystemExit(f"Missing y column: {y}")

    # Minimal sanity: drop missing outcome and missing key columns.
    df = df[np.isfinite(pd.to_numeric(df[y], errors="coerce"))].copy()

    rhs = args.x
    if args.fixed_effects == "day":
        rhs = rhs + " + C(day)"

    formula = f"{y} ~ {rhs}"

    # Weights: clip to avoid zero/negative.
    w = None
    if args.weight_col and args.weight_col in df.columns:
        ww = pd.to_numeric(df[args.weight_col], errors="coerce").fillna(0.0)
        ww = ww.clip(lower=0.0)
        w = ww.to_numpy()
        # If all zero, ignore weights.
        if float(np.nanmax(w)) <= 0:
            w = None

    model = smf.wls(formula=formula, data=df, weights=w) if w is not None else smf.ols(formula=formula, data=df)
    res = model.fit(
        cov_type="cluster",
        cov_kwds={"groups": df[args.cluster]} if args.cluster in df.columns else None,
    )

    out_txt = out_dir / "model_summary.txt"
    out_txt.write_text(res.summary().as_text())

    out_params = out_dir / "model_params.csv"
    params = pd.DataFrame(
        {
            "term": res.params.index,
            "coef": res.params.values,
            "se": res.bse.values,
            "t": res.tvalues.values,
            "p": res.pvalues.values,
        }
    )
    params.to_csv(out_params, index=False)

    print(f"✅ Wrote: {out_txt}")
    print(f"✅ Wrote: {out_params}")


if __name__ == "__main__":
    main()

