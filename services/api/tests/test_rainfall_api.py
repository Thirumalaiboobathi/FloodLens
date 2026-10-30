"""Observed-rainfall API (schema 1.1.0) against the real built artifacts, plus edge cases on tiny trees.

Needs data/api/chennai built with scripts/build_api_artifacts.py (skipped otherwise).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from floodlens_api import main
from floodlens_api import rainfall_schemas as RS
from floodlens_api import schemas as S
from floodlens_api.store import Store

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / "data" / "api" / "chennai"
pytestmark = pytest.mark.skipif(not (ART / "rainfall" / "catalog.json").exists(),
                                reason="run scripts/build_api_artifacts.py first")
client = TestClient(main.app, raise_server_exceptions=False)
BASE = "/v1/regions/chennai"
FINAL = "obs_final_20231203_20231205"
LATE = "obs_late_20261022_20261028"
SPEC_FIELDS = {
    "scenario_id", "scenario_type", "rainfall_stream", "run_type", "product_identifier",
    "product_version", "observation_start", "observation_end", "retrieval_timestamp",
    "native_resolution", "units", "spatial_coverage", "valid_cell_count", "missing_cell_count",
    "missing_percentage", "source", "provenance_reference", "processing_status", "data_freshness"}


@pytest.fixture
def clock(monkeypatch):
    def set_now(y, m, d):
        monkeypatch.setattr(main, "_now", lambda: datetime(y, m, d, 12, 0, tzinfo=UTC))
    set_now(2026, 10, 30)
    return set_now


def get(path, **params):
    return client.get(BASE + path, params=params)


def test_catalog_lists_both_streams_separately(clock):
    r = get("/rainfall")
    body = r.json()
    RS.RainfallCatalog.model_validate(body)
    assert r.status_code == 200
    st = {s["stream"]: s for s in body["streams"]}
    assert st["historical_final"]["scenario_ids"] == [FINAL]
    assert st["recent_late"]["scenario_ids"] == [LATE]
    assert {d["scenario_id"] for d in body["datasets"]} == {FINAL, LATE}
    assert all(s["status"] == "available" for s in st.values())


def test_every_required_metadata_field_is_present(clock):
    for sid in (FINAL, LATE):
        body = get(f"/rainfall/{sid}").json()
        assert SPEC_FIELDS <= set(body)
        assert body["scenario_type"] == "observed_rainfall"
        assert body["in_exposure_index"] is False and body["is_forecast"] is False


def test_historical_final_response(clock):
    b = get(f"/rainfall/{FINAL}").json()
    assert (b["rainfall_stream"], b["run_type"], b["product_identifier"]) == (
        "historical_final", "final", "GPM_3IMERGDF")
    assert b["product_version"] == "V07B" and b["product_versions"] == ["V07B"]
    assert (b["observation_start"], b["observation_end"], b["n_days"]) == ("2023-12-03", "2023-12-05", 3)
    assert b["observation_start_utc"].startswith("2023-12-03T00:00:00")
    assert b["observation_end_utc"].startswith("2023-12-05T23:59:59")
    assert b["data_freshness"]["status"] == "historical_archive"


def test_recent_late_response(clock):
    b = get(f"/rainfall/{LATE}").json()
    assert (b["rainfall_stream"], b["run_type"], b["product_identifier"]) == (
        "recent_late", "late", "GPM_3IMERGDL")
    assert b["product_version"] == "V07C"
    assert (b["observation_start"], b["observation_end"], b["n_days"]) == ("2026-10-22", "2026-10-28", 7)
    assert b["native_resolution_deg"] == 0.1 and "not resampled" in b["native_resolution"]
    assert (b["valid_cell_count"], b["missing_cell_count"], b["missing_percentage"]) == (20, 0, 0)
    assert b["spatial_coverage"]["cells_x"] * b["spatial_coverage"]["cells_y"] == 20
    assert b["provenance_reference"]["raw_verification"] == "verified"
    assert "mm" in b["units"] and "depth" not in b["units"].lower()


def test_freshness_is_time_dependent_and_never_live(clock):
    assert get(f"/rainfall/{LATE}").json()["data_freshness"]["status"] == "recent_provisional"
    clock(2026, 12, 15)
    f = get(f"/rainfall/{LATE}").json()["data_freshness"]
    assert f["status"] == "aged_provisional" and f["age_days"] == 48 and f["is_live"] is False
    assert get(f"/rainfall/{FINAL}").json()["data_freshness"]["status"] == "historical_archive"
    fr = get("/rainfall/freshness").json()
    RS.RainfallFreshness.model_validate(fr)
    assert {d["scenario_id"] for d in fr["datasets"]} == {FINAL, LATE}
    assert all("live" not in d["data_freshness"]["note"].lower().replace("not live", "") for d in fr["datasets"])


def test_stream_and_date_filters(clock):
    ids = lambda **p: [d["scenario_id"] for d in get("/rainfall", **p).json()["datasets"]]
    assert ids(stream="historical_final") == [FINAL]
    assert ids(stream="recent_late") == [LATE]
    assert ids(start="2026-10-28", end="2026-11-30") == [LATE]  # overlap on the last day
    assert ids(start="2026-10-29") == []
    assert ids(start="2023-01-01", end="2023-12-03") == [FINAL]
    only = get("/rainfall", stream="recent_late").json()
    assert [s["stream"] for s in only["streams"]] == ["recent_late"]


@pytest.mark.parametrize("params", [{"stream": "early"}, {"start": "not-a-date"},
                                    {"start": "2026-11-01", "end": "2026-10-01"}])
def test_invalid_filters_give_structured_errors(params):
    r = get("/rainfall", **params)
    assert r.status_code == 422 and {"code", "message", "request_id"} <= set(r.json()["error"])


def test_coverage_lists_periods_per_stream():
    c = get("/rainfall/coverage").json()
    RS.RainfallCoverage.model_validate(c)
    by = {s["stream"]: s for s in c["streams"]}
    assert (by["historical_final"]["earliest"], by["historical_final"]["latest"]) == ("2023-12-03", "2023-12-05")
    assert by["recent_late"]["periods"][0]["product_version"] == "V07C"


def test_summary_states_scope_and_does_not_overclaim():
    s = get(f"/rainfall/{LATE}/summary").json()
    RS.RainfallSummary.model_validate(s)
    assert len(s["daily"]) == 7 and s["accumulation_mm"]["valid_cell_count"] == 20
    assert s["accumulation_mm"]["min_mm"] <= s["accumulation_mm"]["mean_mm"] <= s["accumulation_mm"]["max_mm"]
    assert s["mean_daily_rate_mm_per_day"] == pytest.approx(s["accumulation_mm"]["mean_mm"] / 7, abs=0.01)
    assert "not neighbourhood" in s["coverage_caveat"] or "not neighbourhood-level" in s["coverage_caveat"]
    assert "analysis box" in s["statistic_scope"]


def test_provenance_reports_granule_versions_without_server_paths():
    p = get(f"/rainfall/{LATE}/provenance").json()
    RS.RainfallProvenance.model_validate(p)
    assert len(p["granules"]) == 7 and {g["product_version"] for g in p["granules"]} == {"V07C"}
    assert p["raw_dataset_id"].startswith("nasa-gpm-imerg-v07-late-daily-raw")
    assert p["raw_verification"] == "verified"
    text = json.dumps(p)
    assert "data/raw" not in text and "\\\\" not in text and ":/" not in text.replace("https://", "")
    pf = get(f"/rainfall/{FINAL}/provenance").json()
    assert {g["product_version"] for g in pf["granules"]} == {"V07B"}


def test_cells_are_native_resolution_and_zero_differs_from_nodata():
    c = get(f"/rainfall/{LATE}/cells").json()
    RS.RainfallCells.model_validate(c)
    assert len(c["features"]) == 20 and c["resampled"] is False and c["native_resolution_deg"] == 0.1
    ring = c["features"][0]["geometry"]["coordinates"][0]
    xs = sorted({round(p[0], 6) for p in ring})
    assert round(xs[1] - xs[0], 6) == 0.1
    ok = {"row": 0, "col": 0}
    RS.CellProperties(**ok, state="zero", accumulation_mm=0.0)
    RS.CellProperties(**ok, state="nodata", accumulation_mm=None)
    for bad in (("zero", None), ("nodata", 0.0), ("rain", 0.0), ("rain", None), ("zero", 1.0)):
        with pytest.raises(ValueError):
            RS.CellProperties(**ok, state=bad[0], accumulation_mm=bad[1])


def test_raster_download_is_a_geotiff_with_matching_checksum():
    r = client.get(f"{BASE}/rainfall/{LATE}/raster")
    assert r.status_code == 200 and r.headers["content-type"] == "image/tiff"
    assert r.content[:4] in (b"II*\x00", b"MM\x00*")
    assert hashlib.sha256(r.content).hexdigest() == r.headers["x-content-sha256"]
    assert r.headers["x-content-sha256"] == get(f"/rainfall/{LATE}").json()["raster"]["sha256"]


@pytest.mark.parametrize("sid,status,code", [
    ("obs_late_20991231_20991231", 404, "rainfall_not_found"),  # well-formed but not published
    ("baseline", 422, "invalid_request"),  # not a rainfall id at all
    ("obs_late_2026102_20261028", 422, "invalid_request"),
    ("..%2F..%2Findex", 404, "not_found"),  # encoded slashes never reach a handler
    ("obs_late_20261022_20261028%2F..%2F..", 404, "not_found"),
])
def test_unknown_or_malicious_ids_are_rejected_with_structured_errors(sid, status, code):
    for suffix in ("", "/summary", "/provenance", "/cells", "/raster"):
        r = client.get(f"{BASE}/rainfall/{sid}{suffix}")
        body = r.json()
        if suffix == "":
            assert (r.status_code, body["error"]["code"]) == (status, code), sid
        assert r.status_code in (404, 422) and "error" in body, (sid, suffix)
    assert "available" in client.get(
        f"{BASE}/rainfall/obs_late_20991231_20991231").json()["error"]["details"]


def test_unknown_region_is_a_structured_404():
    r = client.get("/v1/regions/atlantis/rainfall")
    assert r.status_code == 404 and r.json()["error"]["code"] == "region_not_found"


def test_comparison_keeps_streams_apart_and_adds_references():
    c = get("/rainfall/comparison").json()
    RS.RainfallComparison.model_validate(c)
    assert {o["rainfall_stream"] for o in c["observed"]} == {"historical_final", "recent_late"}
    assert c["pairwise"] is not None and c["pairwise"]["same_grid"] is True
    assert c["pairwise"]["same_duration"] is False and "not like-for-like" in c["pairwise"]["caution"]
    assert len(c["pairwise"]["cells"]) == 20
    assert {h["rainfall_accumulation_mm"] for h in c["hypothetical_reference"]} == {50, 100, 200, 300}
    assert "not an input of the exposure index" in c["exposure_note"]
    assert "validate" in c["caution"] and "predict" in c["caution"]


def test_comparison_single_and_invalid():
    one = get("/rainfall/comparison", ids=LATE).json()
    assert [o["scenario_id"] for o in one["observed"]] == [LATE] and one["pairwise"] is None
    assert get("/rainfall/comparison", ids=f"{LATE},{LATE}").status_code == 422
    r = get("/rainfall/comparison", ids="obs_late_20991231_20991231")
    assert r.status_code == 404 and r.json()["error"]["code"] == "rainfall_not_found"


def test_scenario_catalog_distinguishes_all_four_families():
    c = get("/scenario-catalog").json()
    RS.ScenarioCatalog.model_validate(c)
    by = {s["scenario_id"]: s for s in c["scenarios"]}
    assert by["baseline"]["group"] == "terrain_baseline" and by["baseline"]["has_exposure_outputs"]
    assert by["rain_100mm"]["group"] == "hypothetical_rainfall"
    assert by[FINAL]["group"] == "historical_observed" and by[LATE]["group"] == "recent_observed"
    assert not by[FINAL]["has_exposure_outputs"] and not by[LATE]["has_exposure_outputs"]
    assert {s["scenario_type"] for s in c["scenarios"]} == {
        "terrain_baseline", "hypothetical_rainfall_accumulation", "observed_rainfall"}


# ---- backward compatibility and separation from exposure ------------------------------------


def test_existing_scenarios_endpoint_is_unchanged_and_excludes_observed():
    r = get("/scenarios").json()
    S.ScenarioList.model_validate(r)
    assert {s["scenario_id"] for s in r["scenarios"]} == {
        "baseline", "rain_50mm", "rain_100mm", "rain_200mm", "rain_300mm"}
    assert r["observed_rainfall_status"] == "pending"  # exposure has no observed scenario


@pytest.mark.parametrize("path", ["polygons", "facilities"])
def test_observed_ids_are_not_exposure_scenarios(path):
    r = client.get(f"{BASE}/scenarios/{LATE}/{path}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "scenario_not_found"
    r = client.get(f"{BASE}/scenarios/{FINAL}/roads", params={"bbox": "80.2,12.9,80.3,13.0"})
    assert r.status_code == 404


def test_confidence_still_reports_rainfall_input_as_pending():
    conf = get("/confidence").json()
    rain = next(i for i in conf["inputs"] if i["input"] == "rainfall")
    assert rain["status"] == "pending" and rain["dataset_ids"] == []


def test_frozen_v1_schema_set_is_unchanged():
    assert set(S.SCHEMAS) == {
        "regional_exposure_summary", "raster_layer_metadata", "exposure_polygons", "road_exposure",
        "facility_proximity", "scenario_comparison", "dataset_provenance", "data_confidence"}
    assert S.SCHEMA_VERSION == "1.0.0" and RS.RAINFALL_SCHEMA_VERSION == "1.1.0"


def test_exposure_artifacts_do_not_mention_observed_rainfall():
    idx = json.loads((ART / "index.json").read_text("utf-8"))
    assert not any(s.startswith("obs_") for s in idx["scenarios"])
    assert idx["rainfall_scenarios"] == [FINAL, LATE]
    summ = (ART / "summaries.json").read_text("utf-8")
    assert "obs_" not in summ and "observed_rainfall" not in summ


def test_randomerror_is_nowhere_in_api_artifacts_or_responses(clock):
    for p in list(ART.glob("*.json")) + list((ART / "rainfall").rglob("*.json")):
        assert "randomerror" not in p.read_text("utf-8").lower(), p.name
    paths = ["/rainfall", "/rainfall/coverage", "/rainfall/freshness", "/rainfall/comparison",
             "/scenario-catalog", "/provenance", f"/rainfall/{LATE}", f"/rainfall/{LATE}/summary",
             f"/rainfall/{LATE}/provenance", f"/rainfall/{LATE}/cells"]
    for p in paths:
        assert "randomerror" not in get(p).text.lower(), p
    spec = json.dumps(client.get("/openapi.json").json()).lower()
    assert "randomerror" not in spec and "flood_depth" not in spec


def test_responses_never_use_flood_depth_vocabulary(clock):
    for p in ("/rainfall", f"/rainfall/{LATE}/summary", "/rainfall/comparison"):
        assert "flood_depth" not in get(p).text.lower()
    for name, model in RS.RAINFALL_SCHEMAS.items():
        assert "flood_depth" not in json.dumps(model.model_json_schema()).lower(), name


# ---- regions without published rainfall --------------------------------------------------------


@pytest.fixture
def bare_store(tmp_path, monkeypatch):
    r = tmp_path / "chennai"
    r.mkdir()
    (r / "index.json").write_text(json.dumps({
        "region_id": "chennai", "bbox": {"west": 80, "south": 12, "east": 81, "north": 13},
        "boundaries": ["b"], "scenarios": ["baseline"], "tile_deg": 0.05,
        "observed_rainfall_status": "pending"}))
    (r / "scenarios.json").write_text(json.dumps({
        "region_id": "chennai", "observed_rainfall_status": "pending",
        "scenarios": [{"scenario_id": "baseline", "scenario_type": "terrain_baseline",
                       "rainfall_accumulation_mm": None, "accumulation_period": None,
                       "rainfall_source": None}]}))
    monkeypatch.setattr(main, "store", Store(tmp_path))
    return tmp_path


def test_region_built_before_rainfall_reports_unavailable_not_error(bare_store):
    b = get("/rainfall").json()
    RS.RainfallCatalog.model_validate(b)
    assert b["datasets"] == [] and {s["status"] for s in b["streams"]} == {"unavailable"}
    assert get(f"/rainfall/{LATE}").status_code == 404
    assert get("/rainfall/coverage").json()["streams"][0]["periods"] == []
    cmp_ = get("/rainfall/comparison").json()
    assert cmp_["observed"] == [] and cmp_["pairwise"] is None
    assert [s["scenario_id"] for s in get("/scenario-catalog").json()["scenarios"]] == ["baseline"]


def test_pending_status_when_only_rejected_candidates_exist(bare_store):
    d = bare_store / "chennai" / "rainfall"
    d.mkdir()
    (d / "catalog.json").write_text(json.dumps({
        "streams": [
            {"stream": "historical_final", "label": "Historical Final Run",
             "product_identifier": "GPM_3IMERGDF", "status": "unavailable", "scenario_ids": [],
             "rejected": []},
            {"stream": "recent_late", "label": "Recent Late Run", "product_identifier": "GPM_3IMERGDL",
             "status": "pending", "scenario_ids": [],
             "rejected": [{"dataset_id": "imerg-v07-late-total-chennai-2026-11-01_2026-11-07",
                           "code": "processed_checksum_mismatch"}]}],
        "scenario_ids": [], "rejected": []}))
    streams = {s["stream"]: s for s in get("/rainfall").json()["streams"]}
    assert streams["recent_late"]["status"] == "pending"
    assert "failed validation" in streams["recent_late"]["note"]
    assert streams["historical_final"]["status"] == "unavailable"
    assert "imerg-v07" not in json.dumps(streams)  # internal dataset ids of rejects are not exposed
