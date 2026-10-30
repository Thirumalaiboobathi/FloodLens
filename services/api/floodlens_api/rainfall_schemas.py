"""Observed-rainfall API contract, additive schema 1.1.0.

The frozen v1.0.0 models in ``schemas.py`` are untouched; these models only add. Observed rainfall
is *satellite-estimated rainfall accumulation* (mm over a stated UTC period), a context layer that
is not part of the exposure index. It is never flood depth, a forecast or an official warning.
The delivered daily error-estimate field has no representation here (``extra="forbid"``).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from floodlens_api.schemas import Bbox, ScenarioType

RAINFALL_SCHEMA_VERSION = "1.1.0"
RAINFALL_DISCLAIMER = (
    "Satellite-estimated rainfall accumulation (NASA GPM IMERG) on a ~10 km grid over a stated UTC "
    "period. Not flood depth, not a forecast, not an official warning, not a rain-gauge measurement, "
    "and not part of the exposure index."
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RainfallStream(StrEnum):
    HISTORICAL_FINAL = "historical_final"  # IMERG Final Run, GPM_3IMERGDF
    RECENT_LATE = "recent_late"  # IMERG Late Run, GPM_3IMERGDL


class CellState(StrEnum):
    RAIN = "rain"  # valid, > 0 mm
    ZERO = "zero"  # valid observation of 0 mm (not missing)
    NODATA = "nodata"  # missing; never rendered or treated as 0


class Freshness(_Model):
    status: Literal["historical_archive", "recent_provisional", "aged_provisional"]
    as_of_utc: datetime
    observation_end: date
    age_days: int = Field(description="Whole UTC days between observation end and as_of_utc")
    is_live: Literal[False] = False
    note: str


class SourceInfo(_Model):
    provider: str | None
    source_url: str | None
    attribution: str | None
    license: str | None


class RasterRef(_Model):
    kind: Literal["geotiff"] = "geotiff"
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    cell_size_deg: float = Field(gt=0)
    nodata: float | None
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    href: str = Field(description="API path of the raster download")


class SpatialCoverage(_Model):
    bbox: Bbox
    description: str
    cells_x: int = Field(gt=0)
    cells_y: int = Field(gt=0)


class ProvenanceRef(_Model):
    derived_dataset_id: str
    raw_dataset_id: str
    raw_verification: Literal["verified", "partial", "not_present"]


class ObservedRainfallMetadata(_Model):
    schema_version: Literal["1.1.0"] = RAINFALL_SCHEMA_VERSION
    scenario_id: str = Field(pattern=r"^obs_(final|late)_\d{8}_\d{8}$",
                             examples=["obs_late_20261022_20261028"])
    scenario_type: Literal[ScenarioType.OBSERVED_RAINFALL] = ScenarioType.OBSERVED_RAINFALL
    region_id: str
    label: str
    rainfall_stream: RainfallStream
    run_type: Literal["final", "late"]
    product_identifier: str = Field(examples=["GPM_3IMERGDL"])
    product_version: str = Field(description="Single version, or 'mixed: V07B, V07C'")
    product_versions: list[str]
    observation_start: date
    observation_end: date
    observation_start_utc: datetime
    observation_end_utc: datetime
    n_days: int = Field(ge=1)
    retrieval_timestamp: datetime | None
    native_resolution: str
    native_resolution_deg: float
    units: str
    spatial_coverage: SpatialCoverage
    valid_cell_count: int = Field(ge=0)
    missing_cell_count: int = Field(ge=0)
    missing_percentage: float = Field(ge=0, le=100)
    source: SourceInfo
    provenance_reference: ProvenanceRef
    processing_status: Literal["published"] = "published"
    data_freshness: Freshness
    raster: RasterRef
    in_exposure_index: Literal[False] = False
    is_forecast: Literal[False] = False
    limitations: list[str] = Field(min_length=1)
    disclaimer: str = RAINFALL_DISCLAIMER


class StreamAvailability(_Model):
    stream: RainfallStream
    label: str
    product_identifier: str
    status: Literal["available", "pending", "unavailable"]
    scenario_ids: list[str]
    note: str


class RainfallCatalog(_Model):
    schema_version: Literal["1.1.0"] = RAINFALL_SCHEMA_VERSION
    region_id: str
    streams: list[StreamAvailability]
    datasets: list[ObservedRainfallMetadata]
    filters: dict[str, str | None]
    disclaimer: str = RAINFALL_DISCLAIMER


class CoveragePeriod(_Model):
    scenario_id: str
    observation_start: date
    observation_end: date
    n_days: int
    product_version: str


class StreamCoverage(_Model):
    stream: RainfallStream
    status: Literal["available", "pending", "unavailable"]
    periods: list[CoveragePeriod]
    earliest: date | None
    latest: date | None


class RainfallCoverage(_Model):
    schema_version: Literal["1.1.0"] = RAINFALL_SCHEMA_VERSION
    region_id: str
    streams: list[StreamCoverage]
    note: str = ("Streams are never merged. The same calendar dates may appear in both, as "
                 "different estimates; Late Run is provisional and not interchangeable with Final.")


class FreshnessEntry(_Model):
    scenario_id: str
    rainfall_stream: RainfallStream
    retrieval_timestamp: datetime | None
    data_freshness: Freshness


class RainfallFreshness(_Model):
    schema_version: Literal["1.1.0"] = RAINFALL_SCHEMA_VERSION
    region_id: str
    as_of_utc: datetime
    datasets: list[FreshnessEntry]


class AccumulationStats(_Model):
    min_mm: float | None
    mean_mm: float | None
    median_mm: float | None
    max_mm: float | None
    valid_cell_count: int
    missing_cell_count: int
    missing_percentage: float


class DailyRainfall(_Model):
    date: date
    product_version: str
    min_mm: float | None
    mean_mm: float | None
    median_mm: float | None
    max_mm: float | None
    aoi_missing_cells: int | None
    global_missing_percent: float | None


class RainfallSummary(_Model):
    schema_version: Literal["1.1.0"] = RAINFALL_SCHEMA_VERSION
    scenario_id: str
    region_id: str
    rainfall_stream: RainfallStream
    observation_start: date
    observation_end: date
    n_days: int
    accumulation_mm: AccumulationStats
    mean_daily_rate_mm_per_day: float | None
    daily: list[DailyRainfall]
    statistic_scope: str
    coverage_caveat: str
    disclaimer: str = RAINFALL_DISCLAIMER


class GranuleRef(_Model):
    file: str
    date: date
    product_version: str


class RainfallProvenance(_Model):
    schema_version: Literal["1.1.0"] = RAINFALL_SCHEMA_VERSION
    scenario_id: str
    region_id: str
    rainfall_stream: RainfallStream
    product_identifier: str
    product_versions: list[str]
    derived_dataset_id: str
    raw_dataset_id: str
    raw_verification: Literal["verified", "partial", "not_present"]
    processed_sha256: str
    retrieval_timestamp: datetime | None
    source: SourceInfo
    granules: list[GranuleRef]
    processing: list[str]
    limitations: list[str]
    warnings: list[str]
    disclaimer: str = RAINFALL_DISCLAIMER


class CellProperties(_Model):
    row: int
    col: int
    state: CellState
    accumulation_mm: float | None = Field(description="null only when state is 'nodata'")

    @model_validator(mode="after")
    def _state_matches_value(self):
        v = self.accumulation_mm
        ok = ((self.state == CellState.NODATA and v is None)
              or (self.state == CellState.ZERO and v == 0)
              or (self.state == CellState.RAIN and v is not None and v > 0))
        if not ok:
            raise ValueError("cell state must agree with its value: nodata=null, zero=0, rain>0")
        return self


class CellGeometry(_Model):
    type: Literal["Polygon"] = "Polygon"
    coordinates: list


class CellFeature(_Model):
    type: Literal["Feature"] = "Feature"
    geometry: CellGeometry
    properties: CellProperties


class RainfallCells(_Model):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    schema_version: Literal["1.1.0"] = RAINFALL_SCHEMA_VERSION
    scenario_id: str
    region_id: str
    rainfall_stream: RainfallStream
    native_resolution_deg: float
    units: str
    resampled: Literal[False] = False
    features: list[CellFeature]
    disclaimer: str = RAINFALL_DISCLAIMER


class ComparedDataset(_Model):
    scenario_id: str
    rainfall_stream: RainfallStream
    label: str
    observation_start: date
    observation_end: date
    n_days: int
    product_version: str
    mean_accumulation_mm: float | None
    max_accumulation_mm: float | None
    mean_daily_rate_mm_per_day: float | None


class HypotheticalReference(_Model):
    scenario_id: str
    scenario_type: Literal[ScenarioType.HYPOTHETICAL_RAINFALL]
    rainfall_accumulation_mm: float
    accumulation_period: str | None


class CellDifference(_Model):
    row: int
    col: int
    a_mm: float | None
    b_mm: float | None
    b_minus_a_mm: float | None


class PairwiseDifference(_Model):
    a: str
    b: str
    same_grid: bool
    same_duration: bool
    mean_difference_mm: float | None
    mean_daily_rate_difference_mm_per_day: float | None
    cells: list[CellDifference]
    caution: str


class RainfallComparison(_Model):
    schema_version: Literal["1.1.0"] = RAINFALL_SCHEMA_VERSION
    region_id: str
    observed: list[ComparedDataset]
    hypothetical_reference: list[HypotheticalReference]
    pairwise: PairwiseDifference | None
    exposure_note: str = ("Observed rainfall is not an input of the exposure index. Exposure results "
                          "exist only for the terrain baseline and hypothetical scenarios.")
    caution: str = ("Comparing accumulations shows how the satellite estimates differ; it does not "
                    "validate the exposure screening or predict flooding.")
    disclaimer: str = RAINFALL_DISCLAIMER


class ScenarioCatalogEntry(_Model):
    scenario_id: str
    scenario_type: ScenarioType
    group: Literal["terrain_baseline", "hypothetical_rainfall", "historical_observed",
                   "recent_observed"]
    label: str
    has_exposure_outputs: bool
    rainfall_stream: RainfallStream | None = None
    observation_start: date | None = None
    observation_end: date | None = None
    rainfall_accumulation_mm: float | None = None


class ScenarioCatalog(_Model):
    schema_version: Literal["1.1.0"] = RAINFALL_SCHEMA_VERSION
    region_id: str
    scenarios: list[ScenarioCatalogEntry]
    note: str = ("Observed rainfall scenarios are context layers: they have no exposure outputs and "
                 "do not enter the exposure index.")


RAINFALL_SCHEMAS: dict[str, type[BaseModel]] = {
    "rainfall_catalog": RainfallCatalog,
    "observed_rainfall_metadata": ObservedRainfallMetadata,
    "rainfall_summary": RainfallSummary,
    "rainfall_provenance": RainfallProvenance,
    "rainfall_cells": RainfallCells,
    "rainfall_coverage": RainfallCoverage,
    "rainfall_freshness": RainfallFreshness,
    "rainfall_comparison": RainfallComparison,
    "scenario_catalog": ScenarioCatalog,
}
