"""AWS Lambda entry point for the read-only API, invoked by an Amazon API Gateway HTTP API (payload format 2.0, AWS_PROXY).

API Gateway routes GET and HEAD /v1/{proxy+} here and answers CORS preflight itself. Mangum 0.22 reads the version-2.0 fields
(rawPath/http.path, rawQueryString, headers, cookies, body, isBase64Encoded) and returns statusCode/headers/body/isBase64Encoded,
which an HTTP API accepts. The query string is read from rawQueryString, so every parameter reaches FastAPI unchanged.
HEAD and binary bodies are covered by services/api/tests/test_head_and_function_url.py and test_http_api_gateway.py.

The package ships prebuilt artifacts under /var/task/data/api and needs no geospatial libraries.
Importing this module needs `mangum` (the `lambda` extra); the rest of the API does not.
"""

from __future__ import annotations

import os
from pathlib import Path

# set before main is imported: the store reads the artifact root at import time
os.environ.setdefault("FLOODLENS_ARTIFACT_DIR", str(Path(__file__).resolve().parents[1] / "data" / "api"))

from mangum import Mangum

from floodlens_api.main import app

handler = Mangum(app, lifespan="off")
