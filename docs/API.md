# FloodLens API (v1, with additive observed-rainfall schema 1.1.0)

Read-only. It serves prebuilt artifacts from `data/api/<region>/` and computes nothing per request.
It is exploratory screening: not a forecast, not a flood-depth or extent model, not an official
warning. Interactive docs: `/docs`; machine-readable: `/openapi.json`.

Contracts: the frozen exposure models (`schemas.py`, schema 1.0.0, `schemas/v1/*.schema.json`) are
unchanged. Observed rainfall adds `rainfall_schemas.py` (schema 1.1.0) with its own JSON Schemas and
fixtures in `schemas/v1/`. No v1 fixture was altered. Errors always use
`{"error": {"code", "message", "details", "request_id"}}`.

## Existing routes (unchanged)
`/v1/health`, `/v1/regions`, `/v1/regions/{region}/scenarios`, `/summary`, `/comparison`, `/layers`,
`/layers/{layer_id}`, `/provenance`, `/confidence`, and
`/scenarios/{scenario}/polygons|facilities|roads` (roads require `bbox`; `zoom` below 13 returns the
overview).

## Observed rainfall routes (additive)
| Route | Purpose |
|---|---|
| `GET /v1/regions/{region}/scenario-catalog` | All scenario families: terrain baseline, hypothetical, historical observed, recent observed. Observed ones have `has_exposure_outputs: false`. |
| `GET /v1/regions/{region}/rainfall?stream=&start=&end=` | Published datasets with full metadata plus per-stream availability (`available`, `pending`, `unavailable`). Filters: `stream` is `historical_final` or `recent_late`; `start`/`end` are ISO dates and select datasets overlapping the range. |
| `GET .../rainfall/coverage` | Time coverage (periods, earliest, latest) per stream. |
| `GET .../rainfall/freshness` | Per-dataset freshness, computed at request time. |
| `GET .../rainfall/comparison?ids=a,b` | Observed datasets side by side, hypothetical values for reference, and for exactly two ids a per-cell difference (b minus a). |
| `GET .../rainfall/{scenario_id}` | Metadata: scenario_id, scenario_type (`observed_rainfall`), rainfall_stream, run_type, product_identifier, product_version, observation_start/end (and UTC bounds), retrieval_timestamp, native_resolution, units, spatial_coverage, valid/missing cell counts and percentage, source, provenance_reference, processing_status, data_freshness. |
| `GET .../rainfall/{scenario_id}/summary` | Cell statistics (min, mean, median, max), mean daily rate, daily means, and the statistic scope and coverage caveat. |
| `GET .../rainfall/{scenario_id}/provenance` | Raw and derived dataset ids, raw verification state, GeoTIFF SHA-256, granules with versions, processing steps, limitations. |
| `GET .../rainfall/{scenario_id}/cells` | The native 0.1 degree cells as GeoJSON polygons. `state` is `rain`, `zero` or `nodata`; zero and missing are never conflated. |
| `GET .../rainfall/{scenario_id}/raster` | The processed GeoTIFF (`image/tiff`), with `x-content-sha256`. |

`scenario_id` has the form `obs_final_YYYYMMDD_YYYYMMDD` or `obs_late_YYYYMMDD_YYYYMMDD`. Anything
else is a 422. A well-formed id that is not published is a 404 `rainfall_not_found` listing the
published ids. Ids resolve only through the published catalog; no request value is ever used as a
file path.

## Examples
    GET /v1/regions/chennai/rainfall?stream=recent_late
    GET /v1/regions/chennai/rainfall/obs_late_20261022_20261028/summary
    GET /v1/regions/chennai/rainfall/comparison?ids=obs_final_20231203_20231205,obs_late_20261022_20261028
    GET /v1/regions/chennai/rainfall?start=2026-10-28   -> datasets overlapping that date or later

Abbreviated metadata for the recent Late Run dataset:

    {"scenario_id": "obs_late_20261022_20261028", "scenario_type": "observed_rainfall",
     "rainfall_stream": "recent_late", "run_type": "late", "product_identifier": "GPM_3IMERGDL",
     "product_version": "V07C", "observation_start": "2026-10-22", "observation_end": "2026-10-28",
     "native_resolution": "0.1 degree (~10 km) IMERG grid, not resampled",
     "valid_cell_count": 20, "missing_cell_count": 0, "missing_percentage": 0.0,
     "data_freshness": {"status": "recent_provisional", "is_live": false, "age_days": 2},
     "in_exposure_index": false, "is_forecast": false}

## Freshness
`historical_archive` (Final Run), `recent_provisional` (Late Run, observation ended 14 days ago or
less) and `aged_provisional` (older Late Run, possibly superseded by Final). `is_live` is always
false: the data are satellite estimates of past UTC days, not real-time.

## Rules the API enforces
- Final and Late are separate streams with separate ids; they are never merged.
- Observed rainfall is not an exposure scenario: `/scenarios`, `/polygons`, `/facilities` and
  `/roads` do not accept it, and `/confidence` still lists the rainfall input as pending.
- The delivered daily `randomError` is in no model, response or artifact (guard tests scan the
  schemas, artifacts and OpenAPI document). See `docs/analysis/IMERG_RANDOMERROR.md`.
- A region built before rainfall existed reports both streams as `unavailable`, not an error.

## Publishing
`python scripts/build_api_artifacts.py --region chennai` runs
`floodlens_geo.rainfall.catalog.discover`, which validates every candidate manifest (see
`docs/DATASETS.md`) and publishes only those that pass into `data/api/<region>/rainfall/`. Rejected
candidates are printed and make a stream `pending`; their ids are not exposed by the API.

## Water bodies and waterways (schema 1.1.0, additive)

Mapped OpenStreetMap water (ODbL 1.0), published only if the processed file matches its manifest (SHA-256 and
feature count). Reference geometry: not a flood extent, depth, or statement about current water levels. Crowd-sourced
and not validated for completeness; the API never adds or edits features.

| Route | Purpose |
|---|---|
| `GET /v1/regions/{region}/water?bbox=w,s,e,n` | Features intersecting the box. `bbox` is **required**, at most 2 degrees per side. |
| `GET /v1/regions/{region}/water/provenance` | Source, licence, checksum, counts by type and class, limitations. |

Query parameters of `/water`: `feature_type` = `water_body` (polygons) or `waterway` (lines); `water_class` = `water`, `river`,
`canal`, `stream`, `drain`; `limit` 1-5000 (default 1000); `offset` >= 0. Results are ordered by OSM type and id, so paging
is stable. Each feature has `osm_type`, `osm_id`, `name` (null when unnamed), `feature_type`, `water_class` (raw OSM tags are dropped).

Response envelope: `matched`, `returned`, `truncated`, `next_offset` (null when complete), `bbox`, `source` (dataset id, provider,
licence, attribution, acquisition time, snapshot, SHA-256, ODbL share-alike note) and `disclaimer`.

Errors (structured `{"error": {"code", "message", "details", "request_id"}}`): `422 invalid_bbox` (not numeric, wrong order,
out of range, NaN), `422 bbox_too_large` (over 2 degrees; never silently clipped), `422` for unknown `feature_type`/`water_class`
or bad `limit`/`offset`, `404 region_not_found`, `404 water_not_published` (no validated water dataset for the region).

Sample (Chennai, `bbox=80.2,13.0,80.3,13.1&limit=1`): `matched` 193, `returned` 1, `truncated` true, `next_offset` 1,
`source.license` `ODbL 1.0`, `source.dataset_id` `osm-chennai-water`. Whole analysis box: 1650 features
(1044 water bodies, 606 waterways: drain 443, canal 79, stream 57, river 27).
