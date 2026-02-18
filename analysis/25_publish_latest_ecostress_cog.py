from __future__ import annotations

import argparse
import shutil
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import quote


def run(cmd: list[str], cwd: Path | None = None) -> None:
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    if p.returncode != 0:
        raise SystemExit(f"Command failed ({p.returncode}): {' '.join(cmd)}")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=(
            "Automate latest ECOSTRESS publication: choose latest LST tiles, mosaic, build one COG, "
            "optionally upload, and update data/ecostress_highres_latest.json."
        )
    )
    ap.add_argument(
        "--cache-dir",
        default="ecostress_cache",
        help="Directory containing ECOSTRESS GeoTIFF tiles (default: ecostress_cache).",
    )
    ap.add_argument(
        "--raster-glob",
        default="*_LST.tif",
        help="Glob pattern for candidate rasters inside cache-dir (default: *_LST.tif).",
    )
    ap.add_argument(
        "--date-regex",
        default=r"(\d{8}T\d{6})",
        help="Regex capture group used to parse timestamp from filename.",
    )
    ap.add_argument(
        "--date-format",
        default="%Y%m%dT%H%M%S",
        help="Datetime format for parsed capture group (default: %%Y%%m%%dT%%H%%M%%S).",
    )
    ap.add_argument(
        "--output-cog",
        default="outputs_ecostress_il_qc/ecostress_il_lst_70m_latest.cog.tif",
        help="Output COG path (default: outputs_ecostress_il_qc/ecostress_il_lst_70m_latest.cog.tif).",
    )
    ap.add_argument(
        "--meta-json",
        default="../data/ecostress_highres_latest.json",
        help="Frontend metadata JSON to update with public COG URL.",
    )
    ap.add_argument(
        "--engine",
        choices=["auto", "pyqgis", "rasterio"],
        default="auto",
        help="Engine forwarded to 24_make_ecostress_cog.py (default: auto).",
    )
    ap.add_argument(
        "--compression",
        choices=["DEFLATE", "LZW", "ZSTD", "NONE"],
        default="DEFLATE",
        help="COG compression forwarded to 24_make_ecostress_cog.py.",
    )
    ap.add_argument(
        "--overview-resampling",
        choices=["nearest", "average", "bilinear", "cubic", "lanczos"],
        default="average",
        help="Overview resampling forwarded to 24_make_ecostress_cog.py.",
    )
    ap.add_argument(
        "--upload-method",
        choices=["none", "scp", "rsync", "copy"],
        default="none",
        help="Optional upload method for the COG (default: none).",
    )
    ap.add_argument(
        "--upload-target",
        default="",
        help=(
            "Upload destination. "
            "For scp/rsync: user@host:/remote/dir/. "
            "For copy: /absolute/local/dir/."
        ),
    )
    ap.add_argument(
        "--public-base-url",
        default="",
        help=(
            "Public base URL prefix where uploaded COG is served, e.g. "
            "https://micha-server-hub.example.com/cog"
        ),
    )
    ap.add_argument(
        "--no-meta-update",
        action="store_true",
        help="Skip metadata update even when public-base-url is provided.",
    )
    return ap.parse_args()


def extract_timestamp_token(name: str, pattern: str) -> str | None:
    import re

    m = re.search(pattern, name)
    if not m:
        return None
    return m.group(1)


def choose_latest_group(files: Iterable[Path], date_regex: str, date_format: str) -> tuple[datetime, list[Path]]:
    groups: dict[datetime, list[Path]] = defaultdict(list)
    for fp in files:
        tok = extract_timestamp_token(fp.name, date_regex)
        if not tok:
            continue
        try:
            dt = datetime.strptime(tok, date_format)
        except Exception:
            continue
        groups[dt].append(fp)
    if not groups:
        raise SystemExit("No rasters matched timestamp parse. Check --raster-glob/--date-regex/--date-format.")
    latest_dt = max(groups.keys())
    latest_files = sorted(groups[latest_dt])
    return latest_dt, latest_files


def build_vrt(vrt_path: Path, rasters: list[Path]) -> None:
    if not shutil.which("gdalbuildvrt"):
        raise SystemExit("gdalbuildvrt not found. Install GDAL/QGIS, or run with a single input tile.")
    cmd = ["gdalbuildvrt", str(vrt_path)] + [str(p) for p in rasters]
    run(cmd)


def upload_file(src: Path, method: str, target: str) -> None:
    if method == "none":
        return
    if not target.strip():
        raise SystemExit("--upload-target is required when --upload-method is not 'none'.")
    if method == "scp":
        run(["scp", str(src), target])
        return
    if method == "rsync":
        run(["rsync", "-av", str(src), target])
        return
    if method == "copy":
        dst_dir = Path(target).expanduser().resolve()
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / src.name
        shutil.copy2(src, dst)
        print(f"Copied to: {dst}")
        return
    raise SystemExit(f"Unsupported upload method: {method}")


def join_public_url(base: str, filename: str) -> str:
    b = base.rstrip("/")
    return f"{b}/{quote(filename)}"


def main() -> None:
    args = parse_args()
    here = Path(__file__).parent
    cache_dir = (here / args.cache_dir).resolve()
    if not cache_dir.exists():
        raise SystemExit(f"Cache dir not found: {cache_dir}")

    candidates = sorted(cache_dir.glob(args.raster_glob))
    if not candidates:
        raise SystemExit(f"No rasters found in {cache_dir} matching {args.raster_glob}")

    latest_dt, latest_tiles = choose_latest_group(candidates, args.date_regex, args.date_format)
    print(f"Latest timestamp: {latest_dt.isoformat()} ({len(latest_tiles)} tiles)")

    out_cog = (here / args.output_cog).resolve()
    out_cog.parent.mkdir(parents=True, exist_ok=True)

    if len(latest_tiles) == 1:
        source_for_cog = latest_tiles[0]
        print(f"Single latest tile selected: {source_for_cog.name}")
    else:
        vrt = out_cog.with_suffix(".latest.vrt")
        build_vrt(vrt, latest_tiles)
        source_for_cog = vrt
        print(f"Built latest mosaic VRT: {vrt}")

    # Reuse the COG builder (PyQGIS-first).
    cog_builder = here / "24_make_ecostress_cog.py"
    cmd = [
        "python3",
        str(cog_builder),
        "--input-raster",
        str(source_for_cog),
        "--output-cog",
        str(out_cog),
        "--engine",
        args.engine,
        "--compression",
        args.compression,
        "--overview-resampling",
        args.overview_resampling,
    ]
    run(cmd, cwd=here)

    upload_file(out_cog, args.upload_method, args.upload_target)

    if args.public_base_url.strip() and not args.no_meta_update:
        public_url = join_public_url(args.public_base_url, out_cog.name)
        cmd_meta = [
            "python3",
            str(cog_builder),
            "--update-meta-only",
            "--public-cog-url",
            public_url,
            "--meta-json",
            str((here / args.meta_json).resolve()),
        ]
        run(cmd_meta, cwd=here)
        print(f"Metadata updated with public URL: {public_url}")
    elif args.public_base_url.strip():
        print("Public URL provided but metadata update skipped (--no-meta-update).")
    else:
        print("No --public-base-url provided; metadata JSON not updated.")

    print(f"Done. COG: {out_cog}")


if __name__ == "__main__":
    main()

