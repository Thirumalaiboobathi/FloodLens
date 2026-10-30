from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from floodlens_geo.metadata.manifest import (
    DatasetKind,
    DatasetManifest,
    ProcessingStatus,
    load_all,
)


def make(**over):
    base = {
        "dataset_id": "copernicus-dem-glo30",
        "dataset_name": "Copernicus DEM GLO-30",
        "source_url": "s3://copernicus-dem-30m/",
        "provider": "ESA / Copernicus",
        "license": "Copernicus DEM licence",
        "attribution": "Contains Copernicus DEM data",
        "spatial_resolution": "30 m",
        "coordinate_reference_system": "EPSG:4326",
        "processing_version": "0.1.0",
        "geographical_coverage": "Global",
        "limitations": ["Digital Surface Model, not bare-earth terrain"],
    }
    base.update(over)
    return DatasetManifest(**base)


def test_valid_planned_manifest():
    m = make()
    assert m.processing_status is ProcessingStatus.PLANNED
    assert m.kind is DatasetKind.SOURCE


def test_naive_timestamp_rejected():
    with pytest.raises(ValidationError):
        make(acquisition_timestamp=datetime(2026, 10, 30))


def test_processed_requires_timestamp():
    with pytest.raises(ValidationError):
        make(processing_status="processed")
    ok = make(processing_status="processed", acquisition_timestamp=datetime.now(UTC))
    assert ok.processing_status is ProcessingStatus.PROCESSED


def test_derived_requires_lineage():
    with pytest.raises(ValidationError):
        make(kind="derived")
    assert make(kind="derived", derived_from=["copernicus-dem-glo30"]).derived_from


def test_limitations_required():
    with pytest.raises(ValidationError):
        make(limitations=[])


def test_bad_source_url_and_checksum():
    with pytest.raises(ValidationError):
        make(source_url="ftp://x")
    with pytest.raises(ValidationError):
        make(checksum_sha256="abc")


def test_duplicate_ids_rejected(tmp_path):
    for n in ("a", "b"):
        (tmp_path / f"{n}.json").write_text(make().model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_all(tmp_path)
