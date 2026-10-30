"""FloodLens exposure output schemas, version 1 (frozen).

These Pydantic models are the single source of truth. ``scripts/export_schemas.py`` writes them to
``schemas/v1/*.schema.json``; the API serves exactly these shapes. Pure pydantic: no geospatial
imports, so the Lambda package stays small.

Terminology (binding): the index is a *relative screening exposure*. Rainfall figures are
*rainfall accumulation* (mm over a stated period), never flood depth. Classes are relative ranks of
the terrain-only baseline index, never risk levels or probabilities.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0.0"
DISCLAIMER = (
    "Relative screening index from terrain, hydrology and mapped water. Not a flood probability, "
    "depth, extent, forecast or official warning; not validated against observed floods."
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScenarioType(StrEnum):
    """What forces a scenario. These three must never be conflated."""

    TERRAIN_BASELINE = "terrain_baseline"  # terrain/hydrology only, no rainfall input
    HYPOTHETICAL_RAINFALL = "hypothetical_rainfall_accumulation"  # user/analyst-chosen mm, not data
    OBSERVED_RAINFALL = "observed_rainfall"  # real satellite rainfall accumulation (e.g. IMERG)


class Bbox(_Model):
    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)


# ---- shared building blocks -------------------------------------------------------------------


class ScenarioRef(_Model):
    scenario_id: str = Field(pattern=r"^[a-z0-9_]+$", examples=["baseline", "rain_200mm"])
    scenario_type: ScenarioType
    rainfall_accumulation_mm: float | None = Field(
        default=None, ge=0,
        description="Rainfall accumulation forcing in mm over `accumulation_period`; null for the "
                    "terrain baseline. NOT a flood depth.")
    accumulation_period: str | None = Field(default=None, examples=["24h hypothetical"])
    rainfall_source: str | None = Field(
        default=None, description="Dataset id of the rainfall data for observed scenarios; null "
                                  "for hypothetical ones.")


# ---- 1. regional exposure summary ---------------------------------------------------------------


class RegionExposureSummary(_Model):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    region_id: str = Field(examples=["chennai"])
    boundary_id: str = Field(examples=["chennai_core_geoboundaries"])
    scenario: ScenarioRef
    n_classes: int = Field(ge=2, le=10)
    class_basis: Literal["quantiles_of_terrain_baseline_index"] = "quantiles_of_terrain_baseline_index"
    region_area_km2: float = Field(ge=0)
    scored_land_area_km2: float = Field(ge=0)
    area_km2_by_class: dict[str, float]
    share_by_class: dict[str, float]
    mean_index: float = Field(ge=0, le=1)
    mean_completeness: float = Field(ge=0, le=1)
    road_length_km_touching_top_class: float | None = Field(default=None, ge=0)
    road_length_km_all: float | None = Field(default=None, ge=0)
    facilities_in_top_class: dict[str, int] | None = None
    facilities_total: dict[str, int] | None = None
    disclaimer: str = DISCLAIMER


# ---- 2. raster layer metadata -------------------------------------------------------------------


class LayerKind(StrEnum):
    INDEX = "exposure_index"
    CLASS = "exposure_class"
    COMPLETENESS = "completeness"
    WATER_DISTANCE = "water_distance"
    WATER_MASK = "permanent_water_mask"


class RasterLayerMetadata(_Model):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    layer_id: str
    region_id: str
    kind: LayerKind
    scenario: ScenarioRef | None = None
    file: str = Field(description="Path relative to the artifact root")
    crs: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    resolution_m: float = Field(gt=0)
    bounds_native: tuple[float, float, float, float]
    bounds_wgs84: Bbox
    dtype: str
    nodata: float | None
    value_min: float | None
    value_max: float | None
    units: str = Field(description="e.g. 'unitless 0..1', 'class 1..5', 'm'")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    derived_from: list[str] = Field(min_length=1)
    description: str


# ---- GeoJSON pieces -----------------------------------------------------------------------------


class Geometry(_Model):
    type: Literal["Point", "LineString", "MultiLineString", "Polygon", "MultiPolygon"]
    coordinates: list


class PolygonProperties(_Model):
    exposure_class: int = Field(ge=1, le=10)
    label: str
    area_km2: float = Field(ge=0)


class RoadProperties(_Model):
    osm_id: int
    name: str | None
    osm_class: str = Field(description="OSM highway class")
    exp_mean: float = Field(ge=0, le=1)
    exp_max: float = Field(ge=0, le=1)
    exp_class_max: int = Field(ge=1, le=10)
    top_class_share: float = Field(ge=0, le=1)
    length_m: float = Field(ge=0)


class FacilityProperties(_Model):
    osm_id: int
    name: str | None
    osm_class: str
    facility_type: Literal["hospital_or_clinic", "school"]
    exp_index: float = Field(ge=0, le=1)
    exp_class: int = Field(ge=1, le=10)
    hand_m: float | None = Field(description="Height above nearest drainage; null if unavailable")
    water_dist_m: float = Field(ge=0)


class _Feature(_Model):
    type: Literal["Feature"] = "Feature"
    geometry: Geometry


class PolygonFeature(_Feature):
    properties: PolygonProperties


class RoadFeature(_Feature):
    properties: RoadProperties


class FacilityFeature(_Feature):
    properties: FacilityProperties


class _Collection(_Model):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    region_id: str
    scenario: ScenarioRef
    disclaimer: str = DISCLAIMER
    # Present only on a bbox-filtered or zoom-reduced response.
    bbox: Bbox | None = None
    detail: Literal["full", "overview"] | None = None
    truncated: bool = False
    matched: int | None = Field(default=None, ge=0, description="Features matched before any limit")


# ---- 3. exposure polygons / 4. road features / 5. facility proximity ------------------------------


class ExposurePolygonCollection(_Collection):
    features: list[PolygonFeature]


class RoadExposureCollection(_Collection):
    selection: Literal["ways_touching_top_class"] = "ways_touching_top_class"
    features: list[RoadFeature]


class FacilityProximityCollection(_Collection):
    features: list[FacilityFeature]


# ---- 6. scenario comparison ---------------------------------------------------------------------


class ScenarioStats(_Model):
    scenario: ScenarioRef
    share_by_class: dict[str, float]
    share_top_class: float = Field(ge=0, le=1)
    mean_index: float = Field(ge=0, le=1)
    road_length_km_touching_top_class: float = Field(ge=0)
    facilities_in_top_class: dict[str, int]
    delta_vs_baseline_share_top_class: float | None = Field(
        description="Top-class share minus baseline; null for the baseline itself")


class ScenarioComparison(_Model):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    region_id: str
    boundary_id: str
    baseline_scenario_id: str
    class_thresholds_baseline_index: list[float]
    scenarios: list[ScenarioStats] = Field(min_length=1)
    observed_rainfall_status: Literal["available", "pending"] = Field(
        description="'pending' when no real rainfall product has been processed; no observed "
                    "scenario is then listed and none is synthesised.")
    notes: list[str]
    disclaimer: str = DISCLAIMER


# ---- 7. dataset provenance ----------------------------------------------------------------------


class DatasetProvenance(_Model):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    dataset_id: str
    dataset_name: str
    kind: Literal["source", "derived", "fixture"]
    provider: str
    source_url: str
    license: str
    attribution: str
    acquisition_timestamp: str | None
    observation_period: str | None
    spatial_resolution: str
    coordinate_reference_system: str
    processing_version: str
    geographical_coverage: str
    processing_status: Literal["planned", "downloaded", "processed", "failed"]
    limitations: list[str] = Field(min_length=1)
    derived_from: list[str]
    checksum_sha256: str | None


class ProvenanceList(_Model):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    datasets: list[DatasetProvenance]


# ---- 8. data confidence and completeness --------------------------------------------------------


class InputLayerStatus(_Model):
    input: str
    weight: float = Field(ge=0)
    status: Literal["present", "pending", "absent"]
    dataset_ids: list[str]
    note: str | None = None


class DataConfidence(_Model):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    region_id: str
    boundary_id: str
    mean_completeness: float = Field(ge=0, le=1)
    scored_fraction_of_region: float = Field(ge=0, le=1)
    min_completeness_to_score: float = Field(ge=0, le=1)
    inputs: list[InputLayerStatus]
    validation_status: Literal["not_validated"] = "not_validated"
    validation_evidence: list[str] = Field(
        description="Documents describing what validation was attempted; empty list means none")
    confidence_label: Literal["screening_only"] = "screening_only"
    known_limitations: list[str] = Field(min_length=1)
    disclaimer: str = DISCLAIMER


# ---- API envelope -------------------------------------------------------------------------------


class ErrorBody(_Model):
    code: str = Field(examples=["not_found", "invalid_bbox"])
    message: str
    details: dict | None = None
    request_id: str | None = None


class ErrorResponse(_Model):
    error: ErrorBody


class ScenarioList(_Model):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    region_id: str
    scenarios: list[ScenarioRef]
    observed_rainfall_status: Literal["available", "pending"]


class RegionInfo(_Model):
    region_id: str
    name: str
    bbox: Bbox
    boundaries: list[str]
    scenarios: list[str]


class RegionList(_Model):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    regions: list[RegionInfo]


# Registry used by the exporter and tests: name -> model.
SCHEMAS: dict[str, type[BaseModel]] = {
    "regional_exposure_summary": RegionExposureSummary,
    "raster_layer_metadata": RasterLayerMetadata,
    "exposure_polygons": ExposurePolygonCollection,
    "road_exposure": RoadExposureCollection,
    "facility_proximity": FacilityProximityCollection,
    "scenario_comparison": ScenarioComparison,
    "dataset_provenance": ProvenanceList,
    "data_confidence": DataConfidence,
}
