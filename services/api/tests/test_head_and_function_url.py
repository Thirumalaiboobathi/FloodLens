"""HTTP HEAD support and Lambda Function URL (payload format 2.0, fronted by CloudFront) compatibility.

HEAD must behave as GET without a body. The Function URL tests drive the real Lambda entry point with events shaped
like the ones the Lambda service sends for a Function URL (these are synthetic: nothing here ran on AWS).
Needs data/api/chennai (skipped otherwise); the Lambda tests also need mangum.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from floodlens_api import main

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / "data" / "api" / "chennai"
pytestmark = pytest.mark.skipif(not (ART / "rainfall" / "catalog.json").exists(),
                                reason="run scripts/build_api_artifacts.py first")
client = TestClient(main.app, raise_server_exceptions=False)
B = "/v1/regions/chennai"
FINAL = "obs_final_20231203_20231205"
POLY = f"{B}/scenarios/rain_200mm/polygons?bbox=80.05,12.85,80.35,13.25&limit=5000"

# header names whose values legitimately differ per request
PER_REQUEST = {"x-request-id", "date"}


def stable(h) -> dict:
    return {k.lower(): v for k, v in h.items() if k.lower() not in PER_REQUEST}


# ---- HEAD ------------------------------------------------------------------------------------------

PARITY = [
    "/v1/health", "/v1/regions", f"{B}/scenarios", f"{B}/summary", f"{B}/comparison", f"{B}/layers",
    f"{B}/provenance", f"{B}/confidence", f"{B}/scenario-catalog", f"{B}/rainfall", f"{B}/rainfall/coverage",
    f"{B}/rainfall/comparison", f"{B}/rainfall/{FINAL}", f"{B}/rainfall/{FINAL}/summary",
    f"{B}/rainfall/{FINAL}/provenance", f"{B}/rainfall/{FINAL}/cells", f"{B}/rainfall/{FINAL}/raster",
    f"{B}/water?bbox=80.20,13.00,80.25,13.05", f"{B}/water/provenance",
    f"{B}/scenarios/rain_200mm/polygons?bbox=80.20,12.96,80.24,13.00&limit=50",
    # errors must match too
    f"{B}/water?bbox=70,5,90,25", f"{B}/water", "/v1/regions/atlantis/summary", f"{B}/rainfall/not_a_real_id",
    f"{B}/summary?scenario=nope", f"{B}/scenarios/rain_200mm/polygons?limit=0", "/v1/no/such/route", "/v1/",
]


@pytest.mark.parametrize("url", PARITY)
def test_head_returns_the_get_status_and_headers_with_no_body(url):
    g, h = client.get(url), client.head(url)
    assert h.status_code == g.status_code
    assert h.content == b""
    assert stable(h.headers) == stable(g.headers)
    assert h.headers["content-type"] == g.headers["content-type"]
    # the length is that of the GET representation, so the body that was dropped was not shorter or longer
    assert h.headers.get("content-length") == str(len(g.content))


def test_head_json_error_keeps_error_content_type_and_schema_version_header():
    h = client.head(f"{B}/water?bbox=70,5,90,25")
    assert h.status_code == 422 and h.headers["content-type"] == "application/json"
    assert h.headers["x-floodlens-schema-version"]
    assert int(h.headers["content-length"]) > 0


def test_head_raster_is_headers_only_and_never_reads_the_file(monkeypatch):
    import anyio

    async def boom(*a, **k):
        raise AssertionError("HEAD must not open the GeoTIFF")

    monkeypatch.setattr(anyio, "open_file", boom)
    h = client.head(f"{B}/rainfall/{FINAL}/raster")
    assert h.status_code == 200 and h.content == b""
    assert h.headers["content-type"] == "image/tiff"
    assert int(h.headers["content-length"]) == (ART / "rainfall" / FINAL / "raster.tif").stat().st_size
    assert h.headers["x-content-sha256"]


def test_head_unknown_dataset_and_region_are_404_like_get():
    assert client.head(f"{B}/rainfall/obs_final_19990101_19990102").status_code == 404
    url = "/v1/regions/atlantis/water?bbox=80.20,13.00,80.25,13.05"  # a valid query for a region that does not exist
    assert client.head(url).status_code == client.get(url).status_code == 404


def test_head_validation_is_identical_to_get():
    bad = [f"{B}/water?bbox=1,2,3", f"{B}/water?bbox=a,b,c,d", f"{B}/rainfall?stream=zzz",
           f"{B}/scenarios/rain_200mm/polygons?limit=0"]
    for url in bad:
        g, h = client.get(url), client.head(url)
        assert g.status_code == 422, url  # these really are rejected by GET
        assert h.status_code == g.status_code, url
    ok = client.get(f"{B}/rainfall?limit=-1")  # an unknown parameter is ignored by GET, so HEAD ignores it too
    assert client.head(f"{B}/rainfall?limit=-1").status_code == ok.status_code


def test_other_methods_are_still_rejected_and_options_has_no_cors_grant():
    for method in ("post", "put", "delete", "patch", "options"):
        r = getattr(client, method)("/v1/health")
        assert r.status_code == 405, method
        assert "access-control-allow-origin" not in r.headers  # same-origin through CloudFront: no CORS is granted
    assert client.get("/v1/health").status_code == 200


def test_get_behaviour_is_unchanged_by_head_support():
    r = client.get("/v1/health")
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.content


def test_openapi_documents_head_for_each_get_without_response_content():
    spec = client.get("/openapi.json").json()
    ids = set()
    for item in spec["paths"].values():
        if "get" not in item:
            continue
        head = item["head"]
        assert head.get("parameters") == item["get"].get("parameters")
        assert all("content" not in r for r in head["responses"].values())
        assert set(head["responses"]) == set(item["get"]["responses"])
        assert head["operationId"] != item["get"]["operationId"]
        ids.add(head["operationId"])
        ids.add(item["get"]["operationId"])
    assert len(ids) == 2 * sum("get" in i for i in spec["paths"].values())  # every operationId unique
    assert {"get", "head"} <= set(spec["paths"]["/v1/health"])


# ---- Lambda Function URL (payload format 2.0) ---------------------------------------------------------

def function_url_event(path: str, query: str = "", method: str = "GET", headers: dict | None = None) -> dict:
    """The shape of a Function URL invocation (fields per the Lambda Function URL payload format 2.0)."""
    ev = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": query,
        "headers": {"host": "abcdefghij1234567890.lambda-url.ap-south-1.on.aws", "accept-encoding": "br,gzip",
                    "x-amz-cf-id": "abc", "user-agent": "Amazon CloudFront", "x-forwarded-proto": "https",
                    "x-forwarded-for": "203.0.113.9", **(headers or {})},
        "requestContext": {
            "accountId": "anonymous", "apiId": "abcdefghij1234567890",
            "domainName": "abcdefghij1234567890.lambda-url.ap-south-1.on.aws", "domainPrefix": "abcdefghij1234567890",
            "http": {"method": method, "path": path, "protocol": "HTTP/1.1", "sourceIp": "203.0.113.9",
                     "userAgent": "Amazon CloudFront"},
            "requestId": "11111111-2222-3333-4444-555555555555", "routeKey": "$default", "stage": "$default",
            "time": "30/Oct/2026:10:00:00 +0000", "timeEpoch": 1793354400000,
        },
        "isBase64Encoded": False,
    }
    return ev


@pytest.fixture(scope="module")
def handler():
    pytest.importorskip("mangum")
    from floodlens_api import lambda_handler
    return lambda_handler.handler


def invoke(handler, **kw) -> dict:
    out = handler(function_url_event(**kw), None)
    json.dumps(out)  # the Lambda runtime must be able to serialize it
    return out


def test_function_url_get_json(handler):
    out = invoke(handler, path="/v1/health")
    assert out["statusCode"] == 200 and out["isBase64Encoded"] is False
    assert out["headers"]["content-type"] == "application/json"
    assert json.loads(out["body"])["status"] == "ok"


def test_function_url_event_without_optional_fields(handler):
    ev = function_url_event("/v1/health")
    for k in ("headers", "rawQueryString"):
        ev.pop(k)  # the service omits these when empty
    out = handler(ev, None)
    assert out["statusCode"] == 200


def test_function_url_query_string_is_passed_and_percent_decoded(handler):
    out = invoke(handler, path=f"{B}/water", query="bbox=80.20%2C13.00%2C80.25%2C13.05")
    assert out["statusCode"] == 200
    direct = client.get(f"{B}/water?bbox=80.20,13.00,80.25,13.05")
    assert json.loads(out["body"]) == direct.json()


def test_function_url_path_is_not_double_decoded_or_traversable(handler):
    out = invoke(handler, path=f"{B}/rainfall/..%2F..%2Findex")
    assert out["statusCode"] == 404


def test_function_url_errors_keep_the_error_envelope(handler):
    for path, query, status, code in [
        (f"{B}/water", "bbox=70,5,90,25", 422, "bbox_too_large"),
        ("/v1/regions/atlantis/summary", "", 404, None),
        ("/v1/unknown", "", 404, "not_found"),
    ]:
        out = invoke(handler, path=path, query=query)
        assert out["statusCode"] == status
        body = json.loads(out["body"])
        assert "error" in body and (code is None or body["error"]["code"] == code)
        assert out["headers"]["content-type"] == "application/json"


def test_function_url_raster_is_base64_and_byte_identical(handler):
    out = invoke(handler, path=f"{B}/rainfall/{FINAL}/raster")
    assert out["statusCode"] == 200 and out["isBase64Encoded"] is True
    assert out["headers"]["content-type"] == "image/tiff"
    raw = base64.b64decode(out["body"])
    assert raw == (ART / "rainfall" / FINAL / "raster.tif").read_bytes()
    assert raw[:4] == b"II*\x00"


def test_function_url_large_response_is_within_the_6mb_limit_and_matches_direct(handler):
    out = invoke(handler, path=f"{B}/scenarios/rain_200mm/polygons",
                 query="bbox=80.05,12.85,80.35,13.25&limit=5000")
    assert out["statusCode"] == 200
    size = len(out["body"].encode())
    assert 1_000_000 < size < 6_000_000, size  # buffered Function URL responses are capped at 6 MB
    assert json.loads(out["body"]) == client.get(POLY).json()


def test_function_url_head_has_status_headers_and_empty_body(handler):
    g = invoke(handler, path=f"{B}/rainfall")
    h = invoke(handler, path=f"{B}/rainfall", method="HEAD")
    assert h["statusCode"] == g["statusCode"] == 200
    assert h["body"] == "" and h["headers"]["content-type"] == g["headers"]["content-type"]
    assert h["headers"]["content-length"] == str(len(g["body"].encode()))


def test_function_url_head_raster_has_no_body(handler):
    h = invoke(handler, path=f"{B}/rainfall/{FINAL}/raster", method="HEAD")
    assert h["statusCode"] == 200 and h["body"] == ""
    assert h["headers"]["content-length"] == str((ART / "rainfall" / FINAL / "raster.tif").stat().st_size)


def test_function_url_other_methods_are_405(handler):
    for m in ("POST", "OPTIONS", "DELETE"):
        assert invoke(handler, path="/v1/health", method=m)["statusCode"] == 405


def test_function_url_no_cors_grant_even_for_cross_origin_requests(handler):
    out = invoke(handler, path="/v1/health", headers={"origin": "https://evil.example"})
    assert out["statusCode"] == 200
    assert not any(k.lower().startswith("access-control-") for k in out["headers"])


def test_function_url_event_without_version_is_not_misread(handler):
    # an event of an unknown shape must fail, not be served as something else
    with pytest.raises(Exception):  # noqa: B017  (mangum raises its own error for unsupported events)
        handler({"hello": "world"}, None)
