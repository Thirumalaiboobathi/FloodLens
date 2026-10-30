"""AWS Lambda entry point (API Gateway HTTP API, payload format 2.0) for the read-only API.

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
