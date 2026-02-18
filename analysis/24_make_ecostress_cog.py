from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Build a Cloud Optimized GeoTIFF (COG) from an input raster. "
            "Auto mode prefers PyQGIS when available, otherwise uses rasterio."
        )
    )
    p.add_argument("--input-raster", required=True, help="Input raster path (GeoTIFF/LST raster).")
    p.add_argument(
        "--output-cog",
        default="outputs_ecostress_il_qc/ecostress_il_lst_70m_latest.cog.tif",
        help="Output COG path (default: outputs_ecostress_il_qc/ecostress_il_lst_70m_latest.cog.tif).",
    )
    p.add_argument(
        "--engine",
        choices=["auto", "pyqgis", "rasterio"],
        default="auto",
        help="COG engine to use (default: auto).",
    )
    p.add_argument(
        "--compression",
        choices=["DEFLATE", "LZW", "ZSTD", "NONE"],
        default="DEFLATE",
        help="COG compression type (default: DEFLATE).",
    )
    p.add_argument(
        "--overview-resampling",
        choices=["nearest", "average", "bilinear", "cubic", "lanczos"],
        default="average",
        help="Overview resampling method (default: average).",
    )
    p.add_argument(
        "--blocksize",
        type=int,
        default=512,
        help="Tile block size for COG (default: 512).",
    )
    p.add_argument(
        "--public-cog-url",
        default="",
        help=(
            "Optional public HTTPS URL for the uploaded COG. "
            "If provided, the metadata JSON is updated automatically."
        ),
    )
    p.add_argument(
        "--meta-json",
        default="../data/ecostress_highres_latest.json",
        help="Path to frontend metadata JSON to update (default: ../data/ecostress_highres_latest.json).",
    )
    return p


def _ensure_paths(input_raster: Path, output_cog: Path) -> None:
    if not input_raster.exists():
        raise SystemExit(f"Input raster not found: {input_raster}")
    output_cog.parent.mkdir(parents=True, exist_ok=True)


def _try_pyqgis_translate(
    input_raster: Path,
    output_cog: Path,
    compression: str,
    overview_resampling: str,
    blocksize: int,
) -> bool:
    """
    Run COG conversion via PyQGIS processing (gdal:translate) when QGIS is available.
    """
    try:
        import processing  # type: ignore
    except Exception:
        return False

    # gdal:translate algorithm does not expose COG creation options as structured
    # fields in a stable way across QGIS versions, so we pass them in EXTRA.
    extra = (
        "-of COG "
        f"-co COMPRESS={compression} "
        f"-co BLOCKSIZE={blocksize} "
        f"-co OVERVIEW_RESAMPLING={overview_resampling} "
        "-co BIGTIFF=IF_SAFER "
        "-co NUM_THREADS=ALL_CPUS"
    )
    params = {
        "INPUT": str(input_raster),
        "TARGET_CRS": None,
        "NODATA": None,
        "COPY_SUBDATASETS": False,
        "OPTIONS": "",
        "EXTRA": extra,
        "DATA_TYPE": 0,  # use input layer data type
        "OUTPUT": str(output_cog),
    }
    try:
        processing.run("gdal:translate", params)
    except Exception as exc:
        raise RuntimeError(f"PyQGIS gdal:translate failed: {exc}") from exc
    return True


def _try_rasterio_copy(
    input_raster: Path,
    output_cog: Path,
    compression: str,
    overview_resampling: str,
    blocksize: int,
) -> bool:
    """
    Run COG conversion via rasterio driver='COG'.
    """
    try:
        from rasterio.shutil import copy as rio_copy
    except Exception:
        return False

    try:
        rio_copy(
            str(input_raster),
            str(output_cog),
            driver="COG",
            compress=compression,
            blocksize=blocksize,
            overview_resampling=overview_resampling,
            BIGTIFF="IF_SAFER",
            NUM_THREADS="ALL_CPUS",
        )
    except Exception as exc:
        raise RuntimeError(f"Rasterio COG copy failed: {exc}") from exc
    return True


def _update_meta_json(meta_json: Path, public_cog_url: str) -> None:
    if not public_cog_url:
        return
    if not (public_cog_url.startswith("http://") or public_cog_url.startswith("https://")):
        raise SystemExit("public_cog_url must be an HTTP(S) URL.")

    if meta_json.exists():
        try:
            meta: dict[str, Any] = json.loads(meta_json.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    else:
        meta = {}

    # Preserve existing render/tms defaults if present.
    meta.setdefault("tms", "WebMercatorQuad")
    meta.setdefault(
        "render",
        {
            "colormap_name": "inferno",
            "rescale": "0,45",
            "format": "png",
        },
    )
    meta["note"] = "Updated by analysis/24_make_ecostress_cog.py"
    meta["cog_url"] = public_cog_url

    meta_json.parent.mkdir(parents=True, exist_ok=True)
    meta_json.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Updated metadata JSON: {meta_json}")


def main() -> None:
    args = _build_arg_parser().parse_args()
    input_raster = Path(args.input_raster).expanduser().resolve()
    output_cog = Path(args.output_cog).expanduser().resolve()
    meta_json = Path(args.meta_json).expanduser().resolve()

    _ensure_paths(input_raster, output_cog)

    print(f"Input raster: {input_raster}")
    print(f"Output COG : {output_cog}")
    print(f"Engine     : {args.engine}")

    used_engine = ""
    if args.engine in ("auto", "pyqgis"):
        try:
            if _try_pyqgis_translate(
                input_raster=input_raster,
                output_cog=output_cog,
                compression=args.compression,
                overview_resampling=args.overview_resampling,
                blocksize=args.blocksize,
            ):
                used_engine = "pyqgis"
        except Exception as exc:
            if args.engine == "pyqgis":
                raise
            print(f"PyQGIS path failed ({exc}); trying rasterio fallback...")

    if not used_engine and args.engine in ("auto", "rasterio"):
        ok = _try_rasterio_copy(
            input_raster=input_raster,
            output_cog=output_cog,
            compression=args.compression,
            overview_resampling=args.overview_resampling,
            blocksize=args.blocksize,
        )
        if ok:
            used_engine = "rasterio"

    if not used_engine:
        raise SystemExit(
            "Could not build COG. Install/use QGIS (PyQGIS) or ensure rasterio with COG driver is available."
        )

    print(f"COG built successfully via {used_engine}: {output_cog}")

    if args.public_cog_url:
        _update_meta_json(meta_json=meta_json, public_cog_url=args.public_cog_url)
    else:
        print("No --public-cog-url provided; metadata JSON unchanged.")


if __name__ == "__main__":
    main()

