"""Water bodies and waterways API contract, additive schema 1.1.0.

Mapped OpenStreetMap water features (ODbL 1.0) clipped to the pilot region's analysis box. They are
reference geometry for orientation and for the terrain screening's water-distance input. They are not
a flood extent, not a depth, and not a statement about what is currently under water. Completeness
and tagging are crowd-sourced and unvalidated; a feature that is absent here may still exist.
The frozen v1.0.0 models in ``schemas.py`` are untouched.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from floodlens_api.schemas import Bbox

WATER_SCHEMA_VERSION = "1.1.0"
WATER_DISCLAIMER = (
    "Mapped water features from OpenStreetMap contributors (ODbL 1.0). Crowd-sourced and not "
    "validated for completeness. Not a flood extent or depth; a mapped water body is not a statement "
    "about current water levels."
)
ODBL_NOTE = "ODbL: derived databases must be shared alike with attribution to OpenStreetMap contributors."


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WaterFeatureType(StrEnum):
    WATER_BODY = "water_body"  # polygon: lake, pond, reservoir, wide river area
    WATERWAY = "waterway"  # line: river, canal, stream, drain


class WaterClass(StrEnum):
    WATER = "water"
    RIVER = "river"
    CANAL = "canal"
    STREAM = "stream"
    DRAIN = "drain"


class WaterProperties(_Model):
    osm_type: Literal["way", "relation", "node"]
    osm_id: int
    name: str | None
    feature_type: WaterFeatureType
    water_class: WaterClass


class WaterGeometry(_Model):
    type: Literal["Polygon", "LineString"]
    coordinates: list


class WaterFeature(_Model):
    type: Literal["Feature"] = "Feature"
    geometry: WaterGeometry
    properties: WaterProperties


class WaterSource(_Model):
    dataset_id: str
    provider: str
    source_url: str
    license: str
    attribution: str
    acquisition_timestamp: datetime
    snapshot: str = Field(description="Stated OSM snapshot period")
    checksum_sha256: str = Field(description="SHA-256 of the processed OSM file this was built from")
    license_note: str = ODBL_NOTE


class WaterFeatures(_Model):
    schema_version: Literal["1.1.0"] = WATER_SCHEMA_VERSION
    region_id: str
    bbox: Bbox
    feature_type: WaterFeatureType | None
    water_class: WaterClass | None
    limit: int
    offset: int
    matched: int = Field(description="Features intersecting the bbox that match the filters")
    returned: int
    truncated: bool
    next_offset: int | None = Field(description="Offset of the next page, or null when complete")
    source: WaterSource
    disclaimer: str = WATER_DISCLAIMER
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[WaterFeature]


class WaterProvenance(_Model):
    schema_version: Literal["1.1.0"] = WATER_SCHEMA_VERSION
    region_id: str
    source: WaterSource
    geographical_coverage: str
    coordinate_reference_system: str
    processing_version: str
    feature_count: int
    counts_by_type: dict[str, int]
    counts_by_class: dict[str, int]
    limitations: list[str]
    disclaimer: str = WATER_DISCLAIMER


WATER_SCHEMAS: dict[str, type[BaseModel]] = {
    "water_features": WaterFeatures,
    "water_provenance": WaterProvenance,
}
