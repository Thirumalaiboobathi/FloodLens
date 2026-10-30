"""API behaviour against the real built artifacts (data/api/chennai); skipped if not built."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from floodlens_api import schemas as S
from floodlens_api.main import app

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(not (ROOT / "data" / "api" / "chennai" / "index.json").exists(),
                                reason="run scripts/build_api_artifacts.py first")
client = TestClient(app, raise_server_exceptions=False)
VEL = "80.205,12.965,80.240,13.000"


def test_health_and_headers():
    r = client.get("/v1/health")
    assert r.status_code == 200 and r.json()["regions"] == ["chennai"]
    assert r.headers["x-floodlens-schema-version"] == "1.0.0" and r.headers["x-request-id"]


def test_openapi_documents_v1_routes():
    spec = client.get("/openapi.json").json()
    assert "/v1/regions/{region}/scenarios/{scenario}/roads" in spec["paths"]
    assert "not validated" in spec["info"]["description"]


def test_scenarios_distinguish_types_and_pending_observed():
    r = client.get("/v1/regions/chennai/scenarios").json()
    types = {s["scenario_id"]: s["scenario_type"] for s in r["scenarios"]}
    assert types["baseline"] == "terrain_baseline"
    assert types["rain_100mm"] == "hypothetical_rainfall_accumulation"
    assert r["observed_rainfall_status"] == "pending"
    assert "observed_rainfall" not in types.values()


def test_summary_filters_and_validates():
    r = client.get("/v1/regions/chennai/summary",
                   params={"scenario": "baseline", "boundary": "chennai_core_geoboundaries"})
    body = r.json()
    assert r.status_code == 200 and len(body) == 1
    S.RegionExposureSummary.model_validate(body[0])
    assert abs(sum(body[0]["share_by_class"].values()) - 1) < 0.01


def test_comparison_baseline_delta_null_and_others_present():
    c = client.get("/v1/regions/chennai/comparison").json()
    S.ScenarioComparison.model_validate(c)
    deltas = {s["scenario"]["scenario_id"]: s["delta_vs_baseline_share_top_class"]
              for s in c["scenarios"]}
    assert deltas["baseline"] is None and deltas["rain_300mm"] is not None


def test_layers_and_single_layer():
    ls = client.get("/v1/regions/chennai/layers", params={"scenario": "baseline"}).json()
    assert {x["kind"] for x in ls} == {"exposure_index", "exposure_class", "completeness"}
    one = client.get("/v1/regions/chennai/layers/baseline_class").json()
    S.RasterLayerMetadata.model_validate(one)


def test_polygons_bbox_and_limit():
    r = client.get("/v1/regions/chennai/scenarios/baseline/polygons",
                   params={"bbox": VEL, "limit": 3}).json()
    S.ExposurePolygonCollection.model_validate(r)
    assert len(r["features"]) <= 3 and r["matched"] >= len(r["features"])
    whole = client.get("/v1/regions/chennai/scenarios/baseline/polygons",
                       params={"limit": 1}).json()
    assert whole["matched"] > r["matched"]


def test_roads_require_bbox_and_zoom_changes_detail():
    assert client.get("/v1/regions/chennai/scenarios/rain_200mm/roads").status_code == 422
    full = client.get("/v1/regions/chennai/scenarios/rain_200mm/roads",
                      params={"bbox": VEL, "zoom": 15}).json()
    over = client.get("/v1/regions/chennai/scenarios/rain_200mm/roads",
                      params={"bbox": VEL, "zoom": 10}).json()
    S.RoadExposureCollection.model_validate(full)
    assert full["detail"] == "full" and over["detail"] == "overview"
    assert 0 < over["matched"] <= full["matched"]
    ids = [f["properties"]["osm_id"] for f in full["features"]]
    assert len(ids) == len(set(ids)), "tile duplicates must be removed"


def test_facilities_filter_by_type():
    r = client.get("/v1/regions/chennai/scenarios/baseline/facilities",
                   params={"facility_type": "school", "bbox": VEL}).json()
    S.FacilityProximityCollection.model_validate(r)
    assert all(f["properties"]["facility_type"] == "school" for f in r["features"])


@pytest.mark.parametrize("path,params,status,code", [
    ("/v1/regions/nowhere/summary", {}, 404, "region_not_found"),
    ("/v1/regions/chennai/scenarios/rain_9999mm/polygons", {}, 404, "scenario_not_found"),
    ("/v1/regions/chennai/scenarios/baseline/polygons", {"bbox": "1,2,3"}, 422, "invalid_bbox"),
    ("/v1/regions/chennai/scenarios/baseline/polygons", {"bbox": "80.3,13,80.1,13.1"}, 422,
     "invalid_bbox"),
    ("/v1/regions/chennai/scenarios/baseline/polygons", {"limit": 0}, 422, "invalid_request"),
    ("/v1/regions/chennai/summary", {"boundary": "atlantis"}, 404, "boundary_not_found"),
    ("/v1/regions/chennai/layers/nope", {}, 404, "layer_not_found"),
    ("/v1/regions/..%2f..%2fetc/summary", {}, 404, None),
])
def test_structured_errors(path, params, status, code):
    r = client.get(path, params=params)
    assert r.status_code == status
    err = r.json()["error"]
    S.ErrorResponse.model_validate(r.json())
    if code:
        assert err["code"] == code
    assert err["request_id"]


def test_api_package_does_not_import_geospatial_libraries():
    code = ("import sys; import floodlens_api.main; "
            "bad=[m for m in ('rasterio','geopandas','shapely','fiona') if m in sys.modules]; "
            "print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT / "services" / "api",
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
