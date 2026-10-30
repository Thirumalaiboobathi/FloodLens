"""Guards that keep deployment configuration in step with the API contract."""

from __future__ import annotations

import re
from pathlib import Path

from floodlens_api import main

ROOT = Path(__file__).resolve().parents[3]
STACK = ROOT / "infrastructure" / "lib" / "floodlens-stack.ts"


def test_the_api_still_has_exactly_the_fifteen_known_query_parameters():
    """The HTTP API forwards every query parameter, so there is no allow-list to keep in step; this pins the contract itself."""
    spec = main.app.openapi()
    api = {p["name"] for item in spec["paths"].values() for op in item.values()
           for p in op.get("parameters", []) if p["in"] == "query"}
    assert api == {"bbox", "boundary", "end", "facility_type", "feature_type", "ids", "kind", "limit", "min_class",
                   "offset", "scenario", "start", "stream", "water_class", "zoom"}


def test_the_stack_has_no_query_string_allow_list_or_cache_policy():
    src = STACK.read_text(encoding="utf-8")
    assert "API_QUERY_PARAMS" not in src
    assert not re.search(r"CachePolicy|CacheQueryStringBehavior|allowList|FunctionUrl|cloudfront", src)


def test_the_stack_routes_only_get_and_head_under_v1_with_payload_2_0():
    src = STACK.read_text(encoding="utf-8")
    assert 'path: "/v1/{proxy+}"' in src
    assert re.search(r"methods: \[apigwv2\.HttpMethod\.GET, apigwv2\.HttpMethod\.HEAD\]", src)
    assert "PayloadFormatVersion.VERSION_2_0" in src
    assert not re.search(r"HttpMethod\.(ANY|POST|PUT|DELETE|PATCH)", src)


def test_the_stack_does_not_set_reserved_concurrency():
    assert "reservedConcurrentExecutions:" not in STACK.read_text(encoding="utf-8").replace("// No reservedConcurrentExecutions", "")
