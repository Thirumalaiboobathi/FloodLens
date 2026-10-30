"""AWS Lambda entry point for the read-only API, invoked through a Lambda Function URL (payload format 2.0).

CloudFront signs the request (origin access control) and the Function URL requires AWS_IAM, so only the distribution can call it.
Mangum 0.22 reads the same version-2.0 fields a Function URL sends (rawPath/http.path, rawQueryString, headers, cookies, body,
isBase64Encoded) and returns statusCode/headers/body/isBase64Encoded, which a Function URL accepts. HEAD and binary bodies are covered by
services/api/tests/test_head_and_function_url.py.

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
