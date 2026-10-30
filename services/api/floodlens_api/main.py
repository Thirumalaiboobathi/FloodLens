"""FloodLens read-only REST API (v1). Serves prebuilt artifacts; computes nothing."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from floodlens_api import API_VERSION
from floodlens_api import rainfall as R
from floodlens_api import rainfall_schemas as RS
from floodlens_api import schemas as S
from floodlens_api import water_schemas as WS
from floodlens_api.store import ArtifactError, Store, artifact_root, intersects, parse_bbox

DESCRIPTION = (
    "Relative flood-**exposure screening** artifacts for pilot regions. Not a forecast, not a "
    "flood-depth or extent model, not an official warning, and not validated against observed "
    "floods. Rainfall values are *rainfall accumulation* (hypothetical or observed), never flood "
    "depth. Classes are relative ranks of a terrain-only baseline, not risk levels."
)
ERRORS = {404: {"model": S.ErrorResponse}, 422: {"model": S.ErrorResponse}}
MAX_FEATURES = 20000
OVERVIEW_BELOW_ZOOM = 13

store = Store(artifact_root())
app = FastAPI(title="FloodLens API", version=API_VERSION, description=DESCRIPTION,
              docs_url="/docs", openapi_url="/openapi.json")
v1 = APIRouter(prefix="/v1", responses=ERRORS)

BboxQ = Annotated[str | None, Query(description="west,south,east,north (WGS84 degrees)",
                                    examples=["80.20,12.96,80.24,13.00"])]
BoundaryQ = Annotated[str | None, Query(description="boundary id; defaults to the first listed")]


def _error(status: int, code: str, message: str, request: Request, details: dict | None = None):
    body = S.ErrorResponse(error=S.ErrorBody(
        code=code, message=message, details=details,
        request_id=getattr(request.state, "request_id", None)))
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


@app.middleware("http")
async def request_id(request: Request, call_next):
    request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    resp = await call_next(request)
    resp.headers["x-request-id"] = request.state.request_id
    resp.headers["x-floodlens-schema-version"] = S.SCHEMA_VERSION
    return resp


@app.exception_handler(ArtifactError)
async def _artifact_error(request: Request, exc: ArtifactError):
    return _error(exc.status, exc.code, exc.message, request, exc.details)


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    errs = [{"loc": [str(x) for x in e["loc"]], "msg": e["msg"]} for e in exc.errors()]
    return _error(422, "invalid_request", "request parameters failed validation", request,
                  {"errors": errs})


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
    return _error(exc.status_code, code, str(exc.detail), request)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):  # never leak internals
    return _error(500, "internal_error", "unexpected server error", request)


@v1.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "api_version": API_VERSION, "schema_version": S.SCHEMA_VERSION,
            "regions": store.regions()}


@v1.get("/regions", response_model=S.RegionList, tags=["regions"])
def regions() -> S.RegionList:
    items = []
    for r in store.regions():
        i = store.index(r)
        items.append(S.RegionInfo(region_id=r, name=r.title(), bbox=S.Bbox(**i["bbox"]),
                                  boundaries=i["boundaries"], scenarios=i["scenarios"]))
    return S.RegionList(regions=items)


@v1.get("/regions/{region}/scenarios", response_model=S.ScenarioList, tags=["scenarios"])
def scenarios(region: str) -> dict:
    return store.doc(region, "scenarios.json")


@v1.get("/regions/{region}/summary", response_model=list[S.RegionExposureSummary],
        tags=["summary"])
def summary(region: str, scenario: str | None = None, boundary: BoundaryQ = None) -> list[dict]:
    b = store.require_boundary(region, boundary) if boundary else None
    if scenario:
        store.require_scenario(region, scenario)
    return [s for s in store.doc(region, "summaries.json")
            if (scenario is None or s["scenario"]["scenario_id"] == scenario)
            and (b is None or s["boundary_id"] == b)]


@v1.get("/regions/{region}/comparison", response_model=S.ScenarioComparison, tags=["scenarios"])
def comparison(region: str, boundary: BoundaryQ = None) -> dict:
    return store.doc(region, "comparison.json")[store.require_boundary(region, boundary)]


@v1.get("/regions/{region}/layers", response_model=list[S.RasterLayerMetadata],
        tags=["rasters"])
def layers(region: str, scenario: str | None = None,
           kind: S.LayerKind | None = None) -> list[dict]:
    if scenario:
        store.require_scenario(region, scenario)
    return [x for x in store.doc(region, "layers.json")
            if (scenario is None or (x["scenario"] or {}).get("scenario_id") == scenario)
            and (kind is None or x["kind"] == kind.value)]


@v1.get("/regions/{region}/layers/{layer_id}", response_model=S.RasterLayerMetadata,
        tags=["rasters"])
def layer(region: str, layer_id: str) -> dict:
    for x in store.doc(region, "layers.json"):
        if x["layer_id"] == layer_id:
            return x
    raise ArtifactError(404, "layer_not_found", f"unknown layer '{layer_id}'")


@v1.get("/regions/{region}/provenance", response_model=S.ProvenanceList, tags=["metadata"])
def provenance(region: str) -> dict:
    return store.doc(region, "provenance.json")


@v1.get("/regions/{region}/confidence", response_model=S.DataConfidence, tags=["metadata"])
def confidence(region: str, boundary: BoundaryQ = None) -> dict:
    return store.doc(region, "confidence.json")[store.require_boundary(region, boundary)]


def _filtered(collection: dict, bbox: str | None, limit: int, keep=None) -> dict:
    feats = collection["features"]
    q = parse_bbox(bbox) if bbox else None
    if q:
        feats = [f for f in feats if intersects(f, q)]
    if keep:
        feats = [f for f in feats if keep(f)]
    matched = len(feats)
    out = {k: v for k, v in collection.items() if k != "features"}
    out.update(features=feats[:limit], matched=matched, truncated=matched > limit,
               bbox=q.model_dump() if q else None)
    return out


@v1.get("/regions/{region}/scenarios/{scenario}/polygons",
        response_model=S.ExposurePolygonCollection,
        tags=["vectors"])
def polygons(region: str, scenario: str, bbox: BboxQ = None,
             min_class: Annotated[int, Query(ge=1, le=10)] = 1,
             limit: Annotated[int, Query(ge=1, le=MAX_FEATURES)] = 5000) -> dict:
    store.require_scenario(region, scenario)
    col = store.doc(region, f"polygons/{scenario}.json")
    return _filtered(col, bbox, limit, lambda f: f["properties"]["exposure_class"] >= min_class)


@v1.get("/regions/{region}/scenarios/{scenario}/facilities",
        response_model=S.FacilityProximityCollection,
        tags=["vectors"])
def facilities(region: str, scenario: str, bbox: BboxQ = None,
               facility_type: Literal["hospital_or_clinic", "school"] | None = None,
               min_class: Annotated[int, Query(ge=1, le=10)] = 1,
               limit: Annotated[int, Query(ge=1, le=MAX_FEATURES)] = 5000) -> dict:
    store.require_scenario(region, scenario)
    col = store.doc(region, f"facilities/{scenario}.json")
    return _filtered(col, bbox, limit, lambda f: (
        (facility_type is None or f["properties"]["facility_type"] == facility_type)
        and f["properties"]["exp_class"] >= min_class))


@v1.get("/regions/{region}/scenarios/{scenario}/roads", response_model=S.RoadExposureCollection,
        tags=["vectors"],
        description="Road ways touching the highest relative class. `bbox` is required so a "
                    "response is always viewport-sized. `zoom` below 13 returns the simplified "
                    "overview (strongly top-class ways only); otherwise full detail.")
def roads(region: str, scenario: str, bbox: Annotated[str, Query(
              description="west,south,east,north (WGS84 degrees)")],
          zoom: Annotated[float, Query(ge=0, le=24)] = 14,
          limit: Annotated[int, Query(ge=1, le=MAX_FEATURES)] = 5000) -> dict:
    store.require_scenario(region, scenario)
    q = parse_bbox(bbox)
    detail = "overview" if zoom < OVERVIEW_BELOW_ZOOM else "full"
    feats = store.road_features(region, scenario, q, detail)
    scn = next(s for s in store.doc(region, "scenarios.json")["scenarios"]
               if s["scenario_id"] == scenario)
    return {"schema_version": S.SCHEMA_VERSION, "region_id": region, "scenario": scn,
            "selection": "ways_touching_top_class", "bbox": q.model_dump(), "detail": detail,
            "matched": len(feats), "truncated": len(feats) > limit, "features": feats[:limit]}


# ---- water bodies and waterways (schema 1.1.0, additive) ----------------------------------------
# Mapped OSM water (ODbL). The bbox is required so a response is always viewport-sized, results are
# bounded by `limit` and paged with `offset`, and every response carries its source and licence.

MAX_WATER_LIMIT = 5000
MAX_WATER_SPAN_DEG = 2.0  # larger boxes are rejected, not silently clipped
WATER_TAG = ["water"]


@v1.get("/regions/{region}/water", response_model=WS.WaterFeatures, tags=WATER_TAG,
        description="Mapped water bodies (polygons) and waterways (lines) from OpenStreetMap. `bbox` "
                    "is required; features are ordered by OSM id and paged with `limit`/`offset`. "
                    "Reference geometry, not a flood extent.")
def water_features(region: str, bbox: Annotated[str, Query(
                       description="west,south,east,north (WGS84 degrees); at most 2 degrees on a side")],
                   feature_type: WS.WaterFeatureType | None = None,
                   water_class: WS.WaterClass | None = None,
                   limit: Annotated[int, Query(ge=1, le=MAX_WATER_LIMIT)] = 1000,
                   offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0) -> dict:
    q = parse_bbox(bbox)
    if q.east - q.west > MAX_WATER_SPAN_DEG or q.north - q.south > MAX_WATER_SPAN_DEG:
        raise ArtifactError(422, "bbox_too_large",
                            f"bbox may span at most {MAX_WATER_SPAN_DEG} degrees on a side; zoom in",
                            {"bbox": bbox, "max_span_degrees": MAX_WATER_SPAN_DEG})
    doc = store.water_doc(region, "features.json")
    feats = [f for f in doc["features"]
             if (feature_type is None or f["properties"]["feature_type"] == feature_type.value)
             and (water_class is None or f["properties"]["water_class"] == water_class.value)
             and intersects(f, q)]
    page = feats[offset:offset + limit]
    end = offset + len(page)
    return {"region_id": region, "bbox": q.model_dump(),
            "feature_type": feature_type, "water_class": water_class,
            "limit": limit, "offset": offset, "matched": len(feats), "returned": len(page),
            "truncated": end < len(feats), "next_offset": end if end < len(feats) else None,
            "source": doc["source"], "features": page}


@v1.get("/regions/{region}/water/provenance", response_model=WS.WaterProvenance, tags=WATER_TAG)
def water_provenance(region: str) -> dict:
    return store.water_doc(region, "provenance.json")


# ---- observed rainfall: additive context layers (schema 1.1.0) ---------------------------------
# Satellite-estimated rainfall accumulation per stream (historical_final / recent_late). Never part
# of the exposure index, never flood depth. Dataset ids resolve only through the published catalog.

RAIN_TAG = ["observed-rainfall"]
RainId = Annotated[str, Path(pattern=r"^obs_(final|late)_\d{8}_\d{8}$",
                             description="Published observed-rainfall scenario id")]


def _now():
    return R.now_utc()


def _metas(region: str, now) -> list[dict]:
    return [R.with_freshness(store.rainfall_doc(region, i, "meta"), now)
            for i in store.rainfall_catalog(region)["scenario_ids"]]


@v1.get("/regions/{region}/scenario-catalog", response_model=RS.ScenarioCatalog, tags=["scenarios"],
        description="Every scenario family: terrain baseline, hypothetical accumulations, and "
                    "historical/recent observed rainfall. Observed rainfall has no exposure outputs.")
def scenario_catalog(region: str) -> dict:
    scn = store.doc(region, "scenarios.json")["scenarios"]
    ids = store.rainfall_catalog(region)["scenario_ids"]
    metas = [store.rainfall_doc(region, i, "meta") for i in ids]
    return {"schema_version": RS.RAINFALL_SCHEMA_VERSION, **R.scenario_catalog(region, scn, metas)}


@v1.get("/regions/{region}/rainfall", response_model=RS.RainfallCatalog, tags=RAIN_TAG)
def rainfall_catalog(region: str, stream: RS.RainfallStream | None = None,
                     start: date | None = None, end: date | None = None) -> dict:
    if start and end and start > end:
        raise ArtifactError(422, "invalid_date_range", "start must not be after end",
                            {"start": start.isoformat(), "end": end.isoformat()})
    cat = store.rainfall_catalog(region)
    metas = [m for m in _metas(region, _now())
             if (stream is None or m["rainfall_stream"] == stream.value) and R.overlaps(m, start, end)]
    streams = [s for s in R.stream_availability(cat) if stream is None or s["stream"] == stream.value]
    return {"region_id": region, "streams": streams, "datasets": metas,
            "filters": {"stream": stream.value if stream else None,
                        "start": start.isoformat() if start else None,
                        "end": end.isoformat() if end else None}}


@v1.get("/regions/{region}/rainfall/coverage", response_model=RS.RainfallCoverage, tags=RAIN_TAG)
def rainfall_coverage(region: str) -> dict:
    cat = store.rainfall_catalog(region)
    metas = [store.rainfall_doc(region, i, "meta") for i in cat["scenario_ids"]]
    return {"region_id": region, "streams": R.coverage(cat, metas)}


@v1.get("/regions/{region}/rainfall/freshness", response_model=RS.RainfallFreshness, tags=RAIN_TAG)
def rainfall_freshness(region: str) -> dict:
    now = _now()
    return {"region_id": region, "as_of_utc": now, "datasets": [
        {"scenario_id": m["scenario_id"], "rainfall_stream": m["rainfall_stream"],
         "retrieval_timestamp": m["retrieval_timestamp"], "data_freshness": m["data_freshness"]}
        for m in _metas(region, now)]}


@v1.get("/regions/{region}/rainfall/comparison", response_model=RS.RainfallComparison,
        tags=RAIN_TAG,
        description="Side-by-side observed accumulations with the hypothetical scenario values for "
                    "reference. `ids` is a comma-separated list of published ids (exactly 2 adds a "
                    "per-cell difference). Streams are compared, never merged.")
def rainfall_comparison(region: str, ids: Annotated[str | None, Query(
        description="comma-separated observed-rainfall ids; default all published")] = None) -> dict:
    avail = store.rainfall_catalog(region)["scenario_ids"]
    chosen = avail if ids is None else [x.strip() for x in ids.split(",") if x.strip()]
    if len(chosen) != len(set(chosen)) or len(chosen) > 4:
        raise ArtifactError(422, "invalid_ids", "give at most 4 distinct rainfall ids")
    for i in chosen:
        store.require_rainfall(region, i)
    metas = [store.rainfall_doc(region, i, "meta") for i in chosen]
    summ = {i: store.rainfall_doc(region, i, "summary") for i in chosen}
    cells = {i: store.rainfall_doc(region, i, "cells") for i in chosen} if len(chosen) == 2 else {}
    return R.comparison(region, metas, summ, cells, store.doc(region, "scenarios.json")["scenarios"])


@v1.get("/regions/{region}/rainfall/{scenario_id}", response_model=RS.ObservedRainfallMetadata,
        tags=RAIN_TAG)
def rainfall_dataset(region: str, scenario_id: RainId) -> dict:
    return R.with_freshness(store.rainfall_doc(region, scenario_id, "meta"), _now())


@v1.get("/regions/{region}/rainfall/{scenario_id}/summary", response_model=RS.RainfallSummary,
        tags=RAIN_TAG)
def rainfall_summary(region: str, scenario_id: RainId) -> dict:
    return store.rainfall_doc(region, scenario_id, "summary")


@v1.get("/regions/{region}/rainfall/{scenario_id}/provenance",
        response_model=RS.RainfallProvenance, tags=RAIN_TAG)
def rainfall_provenance(region: str, scenario_id: RainId) -> dict:
    return store.rainfall_doc(region, scenario_id, "provenance")


@v1.get("/regions/{region}/rainfall/{scenario_id}/cells", response_model=RS.RainfallCells,
        tags=RAIN_TAG,
        description="The native 0.1 degree grid cells as GeoJSON polygons (not resampled). `state` "
                    "separates valid zero rainfall from missing data.")
def rainfall_cells(region: str, scenario_id: RainId) -> dict:
    return store.rainfall_doc(region, scenario_id, "cells")


@v1.get("/regions/{region}/rainfall/{scenario_id}/raster", tags=RAIN_TAG,
        response_class=FileResponse,
        responses={200: {"content": {"image/tiff": {}}, "description": "Float32 GeoTIFF, mm"}})
def rainfall_raster(region: str, scenario_id: RainId):
    p = store.rainfall_raster(region, scenario_id)
    meta = store.rainfall_doc(region, scenario_id, "meta")
    return FileResponse(p, media_type="image/tiff", filename=f"{scenario_id}.tif",
                        headers={"x-content-sha256": meta["raster"]["sha256"]})


app.include_router(v1)

try:  # AWS Lambda entry point when mangum is packaged; absent in local development
    from mangum import Mangum

    handler = Mangum(app)
except ImportError:
    handler = None
