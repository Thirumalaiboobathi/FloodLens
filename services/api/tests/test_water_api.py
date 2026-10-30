"""Water bodies and waterways API (schema 1.1.0) against the real built artifacts, plus edge cases.

Needs data/api/chennai built with scripts/build_api_artifacts.py (skipped otherwise).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from floodlens_api import main
from floodlens_api import water_schemas as WS
from floodlens_api.store import Store

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / "data" / "api" / "chennai"
pytestmark = pytest.mark.skipif(not (ART / "water" / "features.json").exists(),
                                reason="run scripts/build_api_artifacts.py first")
client = TestClient(main.app, raise_server_exceptions=False)
BASE = "/v1/regions/chennai"
AOI = "80.05,12.85,80.35,13.25"
CITY = "80.20,13.00,80.30,13.10"


def get(path="/water", **params):
    return client.get(BASE + path, params=params)


def test_valid_bbox_returns_validated_features():
    r = get(bbox=CITY)
    assert r.status_code == 200
    body = WS.WaterFeatures.model_validate(r.json())
    assert body.matched > 0 and body.returned == len(body.features) > 0
    assert body.schema_version == "1.1.0"
    b = body.bbox
    for f in body.features:  # every returned feature's own extent touches the query box
        xs, ys = [], []
        stack = [f.geometry.coordinates]
        while stack:
            c = stack.pop()
            if isinstance(c[0], (int, float)):
                xs.append(c[0]); ys.append(c[1])
            else:
                stack.extend(c)
        assert not (max(xs) < b.west or min(xs) > b.east or max(ys) < b.south or min(ys) > b.north)


def test_whole_aoi_matches_the_manifest_count_and_both_types_exist():
    r = get(bbox=AOI, limit=5000).json()
    manifest = json.loads((ROOT / "data/manifests/osm-chennai-water.json").read_text(encoding="utf-8"))
    assert r["matched"] == manifest["feature_count"] == 1650
    assert r["truncated"] is False and r["next_offset"] is None
    types = {f["properties"]["feature_type"] for f in r["features"]}
    assert types == {"water_body", "waterway"}


def test_empty_bbox_is_a_valid_empty_result_not_an_error():
    r = get(bbox="10,10,10.1,10.1")  # open ocean far from the region: nothing mapped
    assert r.status_code == 200
    b = r.json()
    assert b["matched"] == 0 and b["features"] == [] and b["truncated"] is False
    assert b["source"]["license"] == "ODbL 1.0"  # provenance still present


@pytest.mark.parametrize("bad", [
    "abc", "1,2,3", "80.3,13,80.2,13.1", "80,13.1,80.1,13", "181,0,182,1", "80,-91,81,-90.5",
    "nan,0,1,1", "inf,0,1,1", "", "80,13,80,13.1"])
def test_invalid_coordinates_are_structured_422(bad):
    r = get(bbox=bad)
    assert r.status_code == 422
    err = r.json()
    assert err["error"]["code"] in {"invalid_bbox", "validation_error"}
    assert err["error"]["message"]


def test_bbox_is_required():
    r = get()
    assert r.status_code == 422 and "bbox" in json.dumps(r.json())


@pytest.mark.parametrize("ftype,expected", [("water_body", "Polygon"), ("waterway", "LineString")])
def test_feature_type_filter(ftype, expected):
    r = get(bbox=AOI, feature_type=ftype, limit=5000).json()
    assert r["feature_type"] == ftype and r["matched"] > 0
    assert {f["geometry"]["type"] for f in r["features"]} == {expected}
    assert {f["properties"]["feature_type"] for f in r["features"]} == {ftype}


def test_water_class_filter_and_unknown_values_rejected():
    r = get(bbox=AOI, water_class="canal", limit=5000).json()
    assert r["matched"] == 79
    assert {f["properties"]["water_class"] for f in r["features"]} == {"canal"}
    assert get(bbox=AOI, feature_type="lake").status_code == 422
    assert get(bbox=AOI, water_class="sea").status_code == 422


def test_class_counts_are_consistent_with_provenance():
    prov = get("/water/provenance").json()
    by_class = prov["counts_by_class"]
    for cls, n in by_class.items():
        assert get(bbox=AOI, water_class=cls, limit=1).json()["matched"] == n
    assert sum(by_class.values()) == prov["feature_count"] == sum(prov["counts_by_type"].values())


def test_pagination_is_stable_complete_and_without_duplicates():
    seen: list[tuple[str, int]] = []
    offset = 0
    pages = 0
    while offset is not None:
        b = get(bbox=AOI, limit=400, offset=offset).json()
        seen += [(f["properties"]["osm_type"], f["properties"]["osm_id"]) for f in b["features"]]
        assert b["offset"] == offset and b["returned"] <= 400
        offset = b["next_offset"]
        pages += 1
        assert pages < 20
    assert len(seen) == len(set(seen)) == 1650
    assert pages == 5


def test_truncated_flag_and_limits():
    b = get(bbox=AOI, limit=10).json()
    assert b["returned"] == 10 and b["truncated"] is True and b["next_offset"] == 10
    assert get(bbox=AOI, limit=0).status_code == 422
    assert get(bbox=AOI, limit=5001).status_code == 422
    assert get(bbox=AOI, offset=-1).status_code == 422
    past_end = get(bbox=AOI, offset=100000).json()
    assert past_end["returned"] == 0 and past_end["truncated"] is False


def test_large_viewport_is_rejected_not_silently_clipped():
    r = get(bbox="70,5,90,25")
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "bbox_too_large" and err["details"]["max_span_degrees"] == 2.0
    assert get(bbox="79,12,81,14").status_code == 200  # exactly 2 degrees is allowed


def test_every_response_carries_source_and_licence():
    b = get(bbox=CITY, limit=1).json()
    s = b["source"]
    assert s["license"] == "ODbL 1.0" and "OpenStreetMap contributors" in s["attribution"]
    assert s["dataset_id"] == "osm-chennai-water" and s["checksum_sha256"]
    assert "alike" in s["license_note"]
    assert "not a flood extent" in b["disclaimer"].lower() or "not a flood extent" in b["disclaimer"]


def test_provenance_matches_the_manifest_and_names_limitations():
    p = WS.WaterProvenance.model_validate(get("/water/provenance").json())
    m = json.loads((ROOT / "data/manifests/osm-chennai-water.json").read_text(encoding="utf-8"))
    assert p.source.checksum_sha256 == m["checksum_sha256"]
    assert p.source.license == m["license"] and p.source.attribution == m["attribution"]
    assert p.feature_count == m["feature_count"]
    assert p.limitations == m["limitations"] and p.limitations


def test_features_carry_no_raw_osm_tags_and_no_invented_names():
    feats = get(bbox=AOI, limit=5000).json()["features"]
    assert all(set(f["properties"]) == {"osm_type", "osm_id", "name", "feature_type", "water_class"}
               for f in feats)
    named = sum(1 for f in feats if f["properties"]["name"])
    assert 0 < named < len(feats)  # unnamed features stay null


def test_unknown_region_is_404_and_path_traversal_is_not_served():
    assert client.get("/v1/regions/nowhere/water", params={"bbox": CITY}).status_code == 404
    assert client.get("/v1/regions/..%2F..%2Fetc/water", params={"bbox": CITY}).status_code in (404, 422)


def test_openapi_documents_the_routes_and_never_mentions_the_error_field():
    spec = client.get("/openapi.json").json()
    assert "/v1/regions/{region}/water" in spec["paths"]
    assert "/v1/regions/{region}/water/provenance" in spec["paths"]
    assert "randomerror" not in json.dumps(spec).lower()


def test_missing_water_data_is_a_structured_404(tmp_path, monkeypatch):
    root = tmp_path / "api"
    shutil.copytree(ART, root / "chennai", ignore=shutil.ignore_patterns(
        "water", "roads", "polygons", "facilities", "rainfall"))
    monkeypatch.setattr(main, "store", Store(root))
    c = TestClient(main.app, raise_server_exceptions=False)
    for path, params in (("/water", {"bbox": CITY}), ("/water/provenance", {})):
        r = c.get(BASE + path, params=params)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "water_not_published"
    # an existing artifact tree elsewhere keeps working
    assert c.get("/v1/health").status_code == 200


def test_corrupt_artifact_does_not_leak_internals(tmp_path, monkeypatch):
    root = tmp_path / "api"
    shutil.copytree(ART, root / "chennai", ignore=shutil.ignore_patterns(
        "roads", "polygons", "facilities", "rainfall"))
    (root / "chennai" / "water" / "features.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(main, "store", Store(root))
    c = TestClient(main.app, raise_server_exceptions=False)
    r = c.get(BASE + "/water", params={"bbox": CITY})
    assert r.status_code == 500
    assert "Traceback" not in r.text and str(tmp_path) not in r.text
