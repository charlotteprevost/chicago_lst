from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="collapsed_aoi_dt_usable.csv")
    ap.add_argument("--out_dir", required=True, help="Output directory")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.input)
    if "dt" in df.columns:
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce", utc=True)

    if "is_usable" in df.columns:
        df = df[df["is_usable"] == True].copy()  # noqa: E712

    # Summary: mean over AOIs per (date, buffer, group) + coverage diagnostics
    agg = (
        df.groupby(["date", "buffer_m", "is_data_center"], as_index=False)
        .agg(
            mean=("mean", "mean"),
            median=("median", "mean"),
            p90=("p90", "mean"),
            n_aois=("aoi_id", "nunique"),
            pixels=("pixels", "sum"),
        )
        .copy()
    )

    pivot = agg.pivot_table(
        index=["date", "buffer_m"],
        columns="is_data_center",
        values=["mean", "median", "p90", "n_aois", "pixels"],
    )
    pivot.columns = [f"{m}_{'dc' if int(k)==1 else 'ctrl'}" for m, k in pivot.columns.to_list()]
    pivot = pivot.reset_index()
    for m in ["mean", "median", "p90"]:
        dc = f"{m}_dc"
        ctrl = f"{m}_ctrl"
        if dc in pivot.columns and ctrl in pivot.columns:
            pivot[f"{m}_diff_dc_minus_ctrl"] = pivot[dc] - pivot[ctrl]

    out_path = out_dir / "summary_effects_by_date_buffer_usable.csv"
    pivot.to_csv(out_path, index=False)
    print(f"✅ Wrote: {out_path}")


if __name__ == "__main__":
    main()

