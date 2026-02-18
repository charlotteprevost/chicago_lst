import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


@dataclass(frozen=True)
class TransformSpec:
    type: str
    scale: Optional[float] = None
    offset: Optional[float] = None


@dataclass(frozen=True)
class QualitySpec:
    """
    Optional quality masking.

    For ECOSTRESS tiled LST, companion rasters exist alongside *_LST.tif:
    - *_cloud.tif (0 clear, 1 cloud)
    - *_water.tif (0 land, 1 water)
    - *_QC.tif (bitmask; lowest 2 bits often encode 0..3 quality class)
    - *_LST_err.tif (uncertainty)
    """

    enabled: bool = False
    ecostress_companion_masks: bool = False

    # ECOSTRESS companion suffixes
    cloud_suffix: str = "_cloud.tif"
    water_suffix: str = "_water.tif"
    qc_suffix: str = "_QC.tif"
    lst_err_suffix: str = "_LST_err.tif"

    # Keep rules
    keep_cloud_values: Sequence[int] = (0,)
    keep_water_values: Sequence[int] = (0,)
    qc_keep_classes: Sequence[int] = (0, 1)  # keep perfect+nominal
    qc_class_bitmask: int = 3  # use qc & 3 to get 0..3 class

    max_lst_err: Optional[float] = None


@dataclass(frozen=True)
class BaselineSpec:
    grouping: str  # "month" or "doy"
    min_obs_per_group: int


@dataclass(frozen=True)
class Config:
    project_name: str
    aoi_path: str
    aoi_id_field: str
    buffer_m: Optional[float]
    aoi_crs_if_missing: str
    raster_dir: str
    raster_glob: str
    date_regex: str
    date_format: str
    value_units: str
    nodata_below: Optional[float]
    nodata_equals: Optional[float]
    value_transform: TransformSpec
    stats: List[str]
    quality: QualitySpec
    baseline: BaselineSpec
    outputs_dir: str
    export_geojson_path: str


def load_config(path: str) -> Config:
    raw: Dict[str, Any] = json.loads(Path(path).read_text())
    vt = raw.get("value_transform", {}) or {}
    quality = raw.get("quality", {}) or {}
    baseline = raw.get("baseline", {}) or {}
    return Config(
        project_name=raw["project_name"],
        aoi_path=raw["aoi_path"],
        aoi_id_field=raw["aoi_id_field"],
        buffer_m=raw.get("buffer_m", None),
        aoi_crs_if_missing=raw.get("aoi_crs_if_missing", "EPSG:4326"),
        raster_dir=raw["raster_dir"],
        raster_glob=raw.get("raster_glob", "*.tif"),
        date_regex=raw["date_regex"],
        date_format=raw["date_format"],
        value_units=raw.get("value_units", "unknown"),
        nodata_below=raw.get("nodata_below", None),
        nodata_equals=raw.get("nodata_equals", None),
        value_transform=TransformSpec(
            type=vt.get("type", "identity"),
            scale=vt.get("scale", None),
            offset=vt.get("offset", None),
        ),
        stats=list(raw.get("stats", ["mean"])),
        quality=QualitySpec(
            enabled=bool(quality.get("enabled", False)),
            ecostress_companion_masks=bool(quality.get("ecostress_companion_masks", False)),
            cloud_suffix=str(quality.get("cloud_suffix", "_cloud.tif")),
            water_suffix=str(quality.get("water_suffix", "_water.tif")),
            qc_suffix=str(quality.get("qc_suffix", "_QC.tif")),
            lst_err_suffix=str(quality.get("lst_err_suffix", "_LST_err.tif")),
            keep_cloud_values=tuple(quality.get("keep_cloud_values", [0])),
            keep_water_values=tuple(quality.get("keep_water_values", [0])),
            qc_keep_classes=tuple(quality.get("qc_keep_classes", [0, 1])),
            qc_class_bitmask=int(quality.get("qc_class_bitmask", 3)),
            max_lst_err=quality.get("max_lst_err", None),
        ),
        baseline=BaselineSpec(
            grouping=baseline.get("grouping", "month"),
            min_obs_per_group=int(baseline.get("min_obs_per_group", 5)),
        ),
        outputs_dir=raw.get("outputs_dir", "outputs"),
        export_geojson_path=raw.get("export_geojson_path", "../data/aoi_risk_latest.geojson"),
    )

