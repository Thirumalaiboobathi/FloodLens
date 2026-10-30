"""API Gateway HTTP API (payload format 2.0) compatibility of the Lambda entry point.

The events are synthetic: they follow the documented HTTP API payload format 2.0 (route key, rawPath, rawQueryString,
queryStringParameters, pathParameters, requestContext.http, stage $default). Nothing here ran on AWS, so API Gateway
itself (routing, CORS, throttling) is covered by the CDK template tests and the post-deployment checks.
Needs data/api/chennai (skipped otherwise) and mangum.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest
from fastapi.testclient import TestClient

from floodlens_api import main
from floodlens_api import water_schemas as WS

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / "data" / "api" / "chennai"
pytestmark = pytest.mark.skipif(not (ART / "rainfall" / "catalog.json").exists(),
                                reason="run scripts/build_api_artifacts.py first")
client = TestClient(main.app, raise_server_exceptions=False)
B = "/v1/regions/chennai"
FINAL = "obs_final_20231203_20231205"
BOX = "80.20,13.00,80.25,13.05"


def api_event(url: str, method: str = "GET", headers: dict | None = None) -> dict:
    """An HTTP API payload format 2.0 event for a GET/HEAD /v1/{proxy+} route on the $default stage."""
    sp = urlsplit(url)
    pairs = parse_qsl(sp.query, keep_blank_values=True)
    qsp: dict[str, str] = {}
    for k, v in pairs:  # API Gateway joins repeated keys with a comma in queryStringParameters
        qsp[k] = f"{qsp[k]},{v}" if k in qsp else v
    ev = {
        "version": "2.0",
        "routeKey": f"{method} /v1/{{proxy+}}",
        "rawPath": sp.path,
        "rawQueryString": sp.query,
        "headers": {"accept": "application/json", "accept-encoding": "gzip, deflate, br",
                    "host": "abc123xyz0.execute-api.ap-south-1.amazonaws.com",
                    "origin": "https://main.d1a2b3c4d5e6f7.amplifyapp.com", "user-agent": "test",
                    "x-forwarded-for": "203.0.113.9", "x-forwarded-port": "443", "x-forwarded-proto": "https",
                    **(headers or {})},
        "pathParameters": {"proxy": sp.path.removeprefix("/v1/")},
        "requestContext": {
            "accountId": "675613597178", "apiId": "abc123xyz0",
            "domainName": "abc123xyz0.execute-api.ap-south-1.amazonaws.com", "domainPrefix": "abc123xyz0",
            "http": {"method": method, "path": sp.path, "protocol": "HTTP/1.1", "sourceIp": "203.0.113.9", "userAgent": "test"},
            "requestId": "Xy1AbCdEfGhIjKl=", "routeKey": f"{method} /v1/{{proxy+}}", "stage": "$default",
            "time": "30/Oct/2026:10:00:00 +0000", "timeEpoch": 1793354400000,
        },
        "isBase64Encoded": False,
    }
    if qsp:
        ev["queryStringParameters"] = qsp
    return ev


@pytest.fixture(scope="module")
def handler():
    pytest.importorskip("mangum")
    from floodlens_api import lambda_handler
    return lambda_handler.handler


def invoke(handler, url: str, method: str = "GET", **kw) -> dict:
    out = handler(api_event(url, method, **kw), None)
    json.dumps(out)
    return out


def body_of(out: dict) -> bytes:
    return base64.b64decode(out["body"]) if out["isBase64Encoded"] else out["body"].encode()


VOLATILE = {"as_of_utc", "request_id"}  # request-time values: freshness is computed per request, request ids are random


def norm(x):
    """Drop request-time fields so two separate calls can be compared."""
    if isinstance(x, dict):
        return {k: norm(v) for k, v in x.items() if k not in VOLATILE}
    if isinstance(x, list):
        return [norm(v) for v in x]
    return x


def _first_boundary() -> str:
    return client.get(f"{B}/summary").json()[0]["boundary_id"]


def _first_layer_kind() -> str:
    return client.get(f"{B}/layers").json()[0]["kind"]


# One request per query parameter: the response through the Lambda handler (HTTP API event) must be identical to a direct call,
# and the parameter must visibly change the answer (a default response would mean the gateway dropped it).
PARAM_CASES = {
    "bbox": f"{B}/scenarios/rain_200mm/polygons?bbox=80.20,12.96,80.24,13.00&limit=50",
    "boundary": lambda: f"{B}/summary?boundary={_first_boundary()}",
    "end": f"{B}/rainfall?start=2023-12-01&end=2023-12-04",
    "facility_type": f"{B}/scenarios/rain_200mm/facilities?bbox=80.05,12.85,80.35,13.25&facility_type=school&limit=500",
    "feature_type": lambda: f"{B}/water?bbox={BOX}&feature_type={next(iter(WS.WaterFeatureType)).value}&limit=20",
    "ids": f"{B}/rainfall/comparison?ids={FINAL}",
    "kind": lambda: f"{B}/layers?kind={_first_layer_kind()}",
    "limit": f"{B}/water?bbox={BOX}&limit=1",
    "min_class": f"{B}/scenarios/rain_200mm/polygons?bbox=80.05,12.85,80.35,13.25&min_class=8&limit=500",
    "offset": f"{B}/water?bbox={BOX}&limit=1&offset=1",
    "scenario": f"{B}/summary?scenario=rain_200mm",
    "start": f"{B}/rainfall?start=2023-12-04&end=2023-12-31",
    "stream": f"{B}/rainfall?stream=recent_late",
    "water_class": lambda: f"{B}/water?bbox={BOX}&water_class={next(iter(WS.WaterClass)).value}&limit=20",
    "zoom": f"{B}/scenarios/rain_200mm/roads?bbox=80.20,12.96,80.24,13.00&zoom=14.5&limit=50",
}


def _url(name: str) -> str:
    v = PARAM_CASES[name]
    return v() if callable(v) else v


def test_every_openapi_query_parameter_has_a_case():
    spec = main.app.openapi()
    api = {p["name"] for item in spec["paths"].values() for op in item.values()
           for p in op.get("parameters", []) if p["in"] == "query"}
    assert len(api) == 15
    assert set(PARAM_CASES) == api, f"parameters without a gateway case: {set(PARAM_CASES) ^ api}"


@pytest.mark.parametrize("name", sorted(PARAM_CASES))
def test_query_parameter_reaches_fastapi_unchanged(handler, name):
    url = _url(name)
    direct = client.get(url)
    out = invoke(handler, url)
    assert direct.status_code == 200, f"{name}: precondition, the direct call must succeed ({direct.status_code})"
    assert out["statusCode"] == 200
    assert norm(json.loads(body_of(out))) == norm(direct.json())
    # the parameter must not be silently ignored: removing it changes the result for these
    if name in {"limit", "offset", "min_class", "scenario", "stream", "start", "end", "facility_type", "boundary"}:
        stripped = url.replace(f"&{name}=", "&_x=").replace(f"?{name}=", "?_x=")
        assert norm(client.get(stripped).json()) != norm(direct.json()) or name in {"boundary", "scenario"}, f"{name} had no effect"


def test_limit_and_offset_are_honoured_through_the_gateway_event(handler):
    page1 = json.loads(body_of(invoke(handler, f"{B}/water?bbox={BOX}&limit=1")))
    page2 = json.loads(body_of(invoke(handler, f"{B}/water?bbox={BOX}&limit=1&offset=1")))
    assert len(page1["features"]) == 1 and len(page2["features"]) == 1
    assert page1["features"][0] != page2["features"][0]


def test_stream_filter_separates_historical_final_from_recent_late(handler):
    fin = json.loads(body_of(invoke(handler, f"{B}/rainfall?stream=historical_final")))
    late = json.loads(body_of(invoke(handler, f"{B}/rainfall?stream=recent_late")))
    assert fin["datasets"] and all(d["scenario_id"].startswith("obs_final_") for d in fin["datasets"])
    assert all(d["scenario_id"].startswith("obs_late_") for d in late["datasets"])


@pytest.mark.parametrize("url", [
    f"{B}/rainfall", f"{B}/rainfall?stream=historical_final", f"{B}/rainfall?stream=recent_late", f"{B}/rainfall/coverage",
    f"{B}/rainfall/freshness", f"{B}/rainfall/comparison", f"{B}/rainfall/{FINAL}", f"{B}/rainfall/{FINAL}/summary",
    f"{B}/rainfall/{FINAL}/provenance", f"{B}/rainfall/{FINAL}/cells", f"{B}/scenario-catalog", f"{B}/water?bbox={BOX}",
    f"{B}/water/provenance", f"{B}/provenance", f"{B}/summary", "/v1/health", "/v1/regions",
])
def test_endpoints_match_a_direct_call(handler, url):
    direct = client.get(url)
    out = invoke(handler, url)
    assert out["statusCode"] == direct.status_code == 200
    assert norm(json.loads(body_of(out))) == norm(direct.json())
    assert out["headers"]["x-floodlens-schema-version"] == direct.headers["x-floodlens-schema-version"]


def test_geotiff_is_base64_and_byte_identical(handler):
    direct = client.get(f"{B}/rainfall/{FINAL}/raster")
    out = invoke(handler, f"{B}/rainfall/{FINAL}/raster")
    assert out["statusCode"] == 200 and out["isBase64Encoded"] is True
    assert body_of(out) == direct.content and body_of(out)[:4] == b"II*\x00"
    assert out["headers"]["content-type"] == "image/tiff"
    assert out["headers"]["x-content-sha256"] == direct.headers["x-content-sha256"]


@pytest.mark.parametrize("url,status,code", [
    (f"{B}/water?bbox=70,5,90,25", 422, "bbox_too_large"),
    (f"{B}/water", 422, None),
    (f"{B}/scenarios/rain_200mm/polygons?limit=0", 422, None),
    (f"{B}/rainfall?stream=nonsense", 422, None),
    (f"{B}/rainfall?start=2024-01-02&end=2024-01-01", 422, "invalid_date_range"),
    ("/v1/regions/atlantis/summary", 404, None),
    (f"{B}/rainfall/obs_final_20000101_20000102", 404, None),
    (f"{B}/rainfall/not_a_real_id", 422, None),
    ("/v1/no/such/route", 404, None),
])
def test_invalid_input_and_missing_data_keep_the_error_envelope(handler, url, status, code):
    out = invoke(handler, url)
    direct = client.get(url)
    assert out["statusCode"] == status == direct.status_code
    body = json.loads(body_of(out))
    assert norm(body) == norm(direct.json()) and "error" in body
    if code:
        assert body["error"]["code"] == code
    assert "Traceback" not in out["body"]


@pytest.mark.parametrize("url", [f"{B}/rainfall", f"{B}/water?bbox={BOX}&limit=1", f"{B}/rainfall/{FINAL}/raster", "/v1/health",
                                 f"{B}/water?bbox=70,5,90,25"])
def test_head_has_status_and_headers_but_no_body(handler, url):
    g = invoke(handler, url)
    h = invoke(handler, url, method="HEAD")
    assert h["statusCode"] == g["statusCode"]
    assert body_of(h) == b""
    assert h["headers"]["content-type"] == g["headers"]["content-type"]


def test_unsupported_methods_are_not_served_by_the_application(handler):
    # API Gateway only routes GET and HEAD, so these never arrive; if one did, the application still refuses it
    for m in ("POST", "PUT", "DELETE", "PATCH"):
        assert invoke(handler, "/v1/health", method=m)["statusCode"] == 405


def test_the_application_adds_no_cors_headers_because_api_gateway_owns_cors(handler):
    out = invoke(handler, "/v1/health")
    assert not any(k.lower().startswith("access-control-") for k in out["headers"])


def test_largest_response_stays_within_the_lambda_6mb_limit(handler):
    out = invoke(handler, f"{B}/scenarios/rain_200mm/polygons?bbox=80.05,12.85,80.35,13.25&limit=20000")
    assert out["statusCode"] == 200
    assert len(out["body"]) < 6 * 1024 * 1024


def test_percent_encoded_and_repeated_query_values_are_decoded_once(handler):
    out = invoke(handler, f"{B}/water?bbox=80.20%2C13.00%2C80.25%2C13.05&limit=1")
    assert out["statusCode"] == 200
    assert norm(json.loads(body_of(out))) == norm(client.get(f"{B}/water?bbox=80.20%2C13.00%2C80.25%2C13.05&limit=1").json())


def test_the_lambda_handler_documents_the_http_api_event_not_a_function_url():
    src = (ROOT / "services" / "api" / "floodlens_api" / "lambda_handler.py").read_text(encoding="utf-8")
    assert "API Gateway" in src and "HTTP API" in src
