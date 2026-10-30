"""Guards that keep deployment configuration in step with the API contract."""

from __future__ import annotations

import re
from pathlib import Path

from floodlens_api import main

ROOT = Path(__file__).resolve().parents[3]


def test_cloudfront_query_allowlist_matches_every_openapi_query_parameter():
    spec = main.app.openapi()
    api = {p["name"] for item in spec["paths"].values() for op in item.values()
           for p in op.get("parameters", []) if p["in"] == "query"}
    src = (ROOT / "infrastructure" / "lib" / "floodlens-stack.ts").read_text(encoding="utf-8")
    block = re.search(r"API_QUERY_PARAMS = \[(.*?)\];", src, re.DOTALL)
    assert block, "API_QUERY_PARAMS not found in the CDK stack"
    listed = set(re.findall(r'"([a-z_]+)"', block.group(1)))
    assert listed == api, f"CloudFront would drop or forward the wrong parameters: {listed ^ api}"
