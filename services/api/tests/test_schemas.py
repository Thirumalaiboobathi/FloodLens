"""Frozen v1 schema contract: committed JSON Schemas match the models; fixtures satisfy both."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from floodlens_api import schemas as S

ROOT = Path(__file__).resolve().parents[3]
V1 = ROOT / "schemas" / "v1"


@pytest.mark.parametrize("name", sorted(S.SCHEMAS))
def test_committed_schema_matches_model(name):
    committed = json.loads((V1 / f"{name}.schema.json").read_text(encoding="utf-8"))
    assert committed == S.SCHEMAS[name].model_json_schema(), "schema drift: re-export deliberately"


@pytest.mark.parametrize("name", sorted(S.SCHEMAS))
def test_fixture_validates_against_json_schema_and_model(name):
    schema = json.loads((V1 / f"{name}.schema.json").read_text(encoding="utf-8"))
    doc = json.loads((V1 / "fixtures" / f"{name}.example.json").read_text(encoding="utf-8"))
    jsonschema.validate(doc, schema)
    S.SCHEMAS[name].model_validate(doc)


def test_schema_versions_are_one_point_zero():
    assert S.SCHEMA_VERSION == "1.0.0"
    for name in S.SCHEMAS:
        doc = json.loads((V1 / "fixtures" / f"{name}.example.json").read_text(encoding="utf-8"))
        assert doc["schema_version"] == "1.0.0"


def test_extra_fields_are_rejected():
    with pytest.raises(ValueError):
        S.ScenarioRef(scenario_id="baseline", scenario_type="terrain_baseline", flood_depth_m=1.0)


def test_no_flood_depth_vocabulary_in_schemas():
    for name, model in S.SCHEMAS.items():
        text = json.dumps(model.model_json_schema()).lower()
        assert "flood_depth" not in text and "inundation_depth" not in text, name


def test_scenario_types_are_distinct():
    assert {t.value for t in S.ScenarioType} == {
        "terrain_baseline", "hypothetical_rainfall_accumulation", "observed_rainfall"}


def test_fixture_scenarios_are_typed_and_rainfall_is_accumulation():
    comp = json.loads((V1 / "fixtures" / "scenario_comparison.example.json").read_text("utf-8"))
    by_id = {s["scenario"]["scenario_id"]: s["scenario"] for s in comp["scenarios"]}
    assert by_id["baseline"]["scenario_type"] == "terrain_baseline"
    assert by_id["baseline"]["rainfall_accumulation_mm"] is None
    assert by_id["rain_200mm"]["scenario_type"] == "hypothetical_rainfall_accumulation"
    assert by_id["rain_200mm"]["rainfall_accumulation_mm"] == 200.0
    # observed rainfall is pending, so no observed scenario may be listed or synthesised
    if comp["observed_rainfall_status"] == "pending":
        assert all(s["scenario"]["scenario_type"] != "observed_rainfall" for s in comp["scenarios"])


def test_confidence_fixture_states_not_validated():
    conf = json.loads((V1 / "fixtures" / "data_confidence.example.json").read_text("utf-8"))
    assert conf["validation_status"] == "not_validated"
    assert conf["confidence_label"] == "screening_only"
