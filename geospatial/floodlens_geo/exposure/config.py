"""Configuration for the Flood Exposure Screening Index (ESI).

Every number here is a documented *heuristic*, not a calibrated parameter. The index ranks relative
terrain/hydrology exposure; it is not a flood probability, depth or extent (docs/METHODOLOGY.md).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

INPUTS = ("hand", "twi", "slope", "flow_accumulation", "elevation", "water_distance", "rainfall")


class Weights(BaseModel):
    """Relative weights; renormalised over the inputs actually available for each cell."""

    hand: float = 0.30
    twi: float = 0.15
    slope: float = 0.10
    flow_accumulation: float = 0.10
    elevation: float = 0.15
    water_distance: float = 0.10
    rainfall: float = 0.10

    @field_validator("*")
    @classmethod
    def _non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("weights must be >= 0")
        return v

    @model_validator(mode="after")
    def _some_positive(self) -> Weights:
        if sum(self.as_dict().values()) <= 0:
            raise ValueError("at least one weight must be > 0")
        return self

    def as_dict(self) -> dict[str, float]:
        return {k: getattr(self, k) for k in INPUTS}


class Transforms(BaseModel):
    """Scales of the monotone transforms that map each indicator to a 0..1 exposure score."""

    hand_scale_m: float = Field(5.0, gt=0)  # score = exp(-HAND / scale)
    elevation_scale_m: float = Field(10.0, gt=0)  # score = exp(-elevation / scale)
    water_distance_scale_m: float = Field(500.0, gt=0)  # score = exp(-d / scale)
    slope_max_deg: float = Field(5.0, gt=0)  # score = 1 - min(slope, max) / max
    robust_percentiles: tuple[float, float] = (2.0, 98.0)  # clip for TWI, log10(SCA)
    rainfall_scale_mm: float = Field(200.0, gt=0)  # score = 1 - exp(-R / scale)
    reference_rainfall_mm: float = Field(100.0, gt=0)  # R at which reach scaling k(R) = 1
    reach_exponent: float = Field(0.5, ge=0)  # k(R) = (R / ref) ** exponent on HAND/water scales

    @field_validator("robust_percentiles")
    @classmethod
    def _ordered(cls, v: tuple[float, float]) -> tuple[float, float]:
        if not 0 <= v[0] < v[1] <= 100:
            raise ValueError("robust_percentiles must satisfy 0 <= lo < hi <= 100")
        return v


class ExposureConfig(BaseModel):
    weights: Weights = Weights()
    transforms: Transforms = Transforms()
    min_completeness: float = Field(0.5, ge=0, le=1)  # weight share needed to score a cell
    n_classes: int = Field(5, ge=2, le=10)  # relative (quantile) classes of the baseline index
    scenario_rainfall_mm: list[float] = [50.0, 100.0, 200.0, 300.0]  # hypothetical, 24 h
    simplify_tolerance_m: float = Field(45.0, ge=0)
    polygon_smooth_cells: int = Field(7, ge=1)  # median filter width for polygon export only
    sieve_min_cells: int = Field(40, ge=1)  # ~3.6 ha at 30 m; removes speckle
