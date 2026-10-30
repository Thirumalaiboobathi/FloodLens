"""Additive rainfall contract (1.1.0): committed JSON Schemas match the models; fixtures satisfy both."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from floodlens_api import rainfall_schemas as RS
from floodlens_api import schemas as S

V1 = Path(__file__).resolve().parents[3] / "schemas" / "v1"


@pytest.mark.parametrize("name", sorted(RS.RAINFALL_SCHEMAS))
def test_committed_schema_matches_model(name):
    committed = json.loads((V1 / f"{name}.schema.json").read_text(encoding="utf-8"))
    assert committed == RS.RAINFALL_SCHEMAS[name].model_json_schema(), "schema drift: re-export deliberately"


@pytest.mark.parametrize("name", sorted(RS.RAINFALL_SCHEMAS))
def test_fixture_validates_against_json_schema_and_model(name):
    schema = json.loads((V1 / f"{name}.schema.json").read_text(encoding="utf-8"))
    doc = json.loads((V1 / "fixtures" / f"{name}.example.json").read_text(encoding="utf-8"))
    jsonschema.validate(doc, schema)
    RS.RAINFALL_SCHEMAS[name].model_validate(doc)
    assert doc["schema_version"] == "1.1.0"


def test_rainfall_registry_is_disjoint_from_the_frozen_v1_registry():
    assert not set(RS.RAINFALL_SCHEMAS) & set(S.SCHEMAS)


def test_observed_scenario_type_reuses_the_existing_enum_value():
    meta = json.loads((V1 / "fixtures" / "observed_rainfall_metadata.example.json").read_text("utf-8"))
    assert meta["scenario_type"] == S.ScenarioType.OBSERVED_RAINFALL.value == "observed_rainfall"
    assert meta["rainfall_stream"] in {"historical_final", "recent_late"}
    assert meta["in_exposure_index"] is False and meta["is_forecast"] is False
    assert meta["data_freshness"]["is_live"] is False


def test_extra_and_forbidden_fields_are_rejected():
    meta = json.loads((V1 / "fixtures" / "observed_rainfall_metadata.example.json").read_text("utf-8"))
    for extra in ({"flood_depth_m": 1.0}, {"uncertainty": 3.0}):
        with pytest.raises(ValueError):
            RS.ObservedRainfallMetadata.model_validate({**meta, **extra})
    with pytest.raises(ValueError):
        RS.ObservedRainfallMetadata.model_validate({**meta, "in_exposure_index": True})
    with pytest.raises(ValueError):
        RS.ObservedRainfallMetadata.model_validate({**meta, "scenario_id": "obs_early_20261022_20261028"})
    with pytest.raises(ValueError):
        RS.ObservedRainfallMetadata.model_validate({**meta, "rainfall_stream": "early"})


def test_schemas_have_no_error_estimate_or_depth_vocabulary():
    for name, model in RS.RAINFALL_SCHEMAS.items():
        text = json.dumps(model.model_json_schema()).lower()
        assert "randomerror" not in text and "flood_depth" not in text and "uncertainty" not in text, name
