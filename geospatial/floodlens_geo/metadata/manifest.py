"""Dataset manifest model.

Every dataset FloodLens ingests or derives must be described by a manifest
carrying provenance, licence, attribution and limitations (spec section 4.5).
Manifests are validated here and served verbatim by the API.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator


class ProcessingStatus(StrEnum):
    PLANNED = "planned"
    DOWNLOADED = "downloaded"
    PROCESSED = "processed"
    FAILED = "failed"


class DatasetKind(StrEnum):
    """Distinguishes real data from derived products and demo fixtures."""

    SOURCE = "source"
    DERIVED = "derived"
    FIXTURE = "fixture"  # synthetic; must never be served as evidence


class DatasetManifest(BaseModel):
    dataset_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_\-\.]*$")
    dataset_name: str = Field(min_length=3)
    kind: DatasetKind = DatasetKind.SOURCE
    source_url: str = Field(pattern=r"^(https?|s3)://")
    provider: str = Field(min_length=2)
    license: str = Field(min_length=2)
    attribution: str = Field(min_length=2)
    acquisition_timestamp: datetime | None = None
    observation_period: str | None = None
    spatial_resolution: str = Field(min_length=1)
    coordinate_reference_system: str = Field(min_length=3)
    processing_version: str = Field(min_length=1)
    geographical_coverage: str = Field(min_length=2)
    processing_status: ProcessingStatus = ProcessingStatus.PLANNED
    limitations: list[str] = Field(min_length=1)
    derived_from: list[str] = Field(default_factory=list)
    checksum_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("acquisition_timestamp")
    @classmethod
    def _tz_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("acquisition_timestamp must be timezone-aware (UTC)")
        return v

    @model_validator(mode="after")
    def _status_requires_provenance(self) -> DatasetManifest:
        needs_ts = self.processing_status in {ProcessingStatus.DOWNLOADED, ProcessingStatus.PROCESSED}
        if needs_ts and self.acquisition_timestamp is None:
            raise ValueError("downloaded/processed datasets need acquisition_timestamp")
        if self.kind is DatasetKind.DERIVED and not self.derived_from:
            raise ValueError("derived datasets must list derived_from dataset ids")
        return self


def load_manifest(path: Path) -> DatasetManifest:
    return DatasetManifest.model_validate_json(path.read_text(encoding="utf-8"))


def load_all(directory: Path) -> list[DatasetManifest]:
    """Load every *.json manifest in a directory; ids must be unique."""
    manifests = [load_manifest(p) for p in sorted(directory.glob("*.json"))]
    ids = [m.dataset_id for m in manifests]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate dataset_id(s): {sorted(dupes)}")
    return manifests
