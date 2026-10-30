# FloodLens — Architecture (Phase 0)

FloodLens is a **flood exposure screening and scenario analysis** platform for India.
It is not a flood forecast, not a depth model, and not an emergency system.

## 1. Guiding strategy: precompute offline, serve static-ish artifacts

Heavy raster work never runs in an API request. The system has two halves:

```
 OFFLINE PIPELINE (laptop / container job)            ONLINE SERVING (AWS)
 ─────────────────────────────────────────            ─────────────────────────────
 Copernicus DEM (public S3) ─┐                         Browser (React + MapLibre)
 NASA GPM IMERG (Earthdata) ─┤                              │  map tiles/style (API key)
 OpenStreetMap (Geofabrik)  ─┼─► floodlens_geo ─► S3        ▼
 Admin boundaries           ─┘   (validate,       processed  Amazon Location Service
                                  terrain,        + manifests
                                  hydrology,           ▲
                                  rainfall,            │ read-only
                                  exposure, export)    │
                                                  Lambda (FastAPI+Mangum)
 Amplify Hosting (static React app)                     ▲
 API Gateway HTTP API (GET/HEAD /v1/*, CORS, throttle) ──┘  (Phase 13; the Phase 7 CloudFront design was superseded)
```

## 2. Hierarchical data strategy

| Level | Content | Source of truth |
|---|---|---|
| L1 India | country + state boundaries, coarse rainfall summary, coverage status | processed boundaries, IMERG aggregate |
| L2 State / district | admin boundaries, regional rainfall + infra statistics | boundaries, OSM extracts |
| L3 Pilot region | DEM-derived terrain, hydrology, road/facility exposure, scenarios | per-region processed bundle |

Each region has a coverage status (`available | partial | processing | unavailable`) stored in
`regions.json`; the API refuses to return L3 analytics for anything not `available`/`partial`.

## 3. Components

- **`geospatial/` (`floodlens_geo`)** – pure-Python processing engine. Deterministic functions,
  unit-tested on synthetic rasters (fixtures never served as evidence).
- **`services/api`** – FastAPI app, exposed on Lambda via Mangum. Reads only prebuilt artifacts
  from S3 (local filesystem in dev) through a `repositories/` abstraction.
- **`apps/web`** – React 19 + Vite + MapLibre GL. Map style/tiles from Amazon Location (API key
  restricted by referrer + action); data from our API.
- **`infrastructure/`** – CDK (TypeScript), stack `FloodLensAmplify`: API Gateway HTTP API, Lambda,
  Location API key, log retention, alarms. The frontend is built and hosted by AWS Amplify from GitHub (`amplify.yml`).

## 4. Artifact formats

- Vector overlays: simplified GeoJSON per region/layer (size-budgeted), PMTiles/vector tiles only if
  a layer exceeds the budget.
- Rasters: COGs in S3; for the browser, pre-rendered PNG overlays with bounds metadata (avoids a
  tile server).
- Everything exchanged in EPSG:4326; analysis in a local UTM CRS (EPSG:32643/32644/32645 by region).

## 5. AWS resource plan (minimal)

API Gateway HTTP API (Phase 13), Lambda ×1 (data artifacts packaged inside), Amplify Hosting for the web app (no S3 or CloudFront of our own),
Amazon Location API key, CloudWatch logs (30-day retention), AWS Budget. No VPC, no NAT, no
always-on compute. Estimated steady-state cost: a few USD/month at demo traffic.

## 6. Security boundaries

Browser → Amplify Hosting / API Gateway (TLS). The API Lambda role has logging permissions only (it reads files in its own package). The Location
key is browser-exposed by design, scoped to `geo-maps:*` read actions and allowed referrers.
CI uses GitHub OIDC; no long-lived keys. See `docs/security/` (Phase 5).

## 7. Known risks (see ADRs)

1. IMERG requires a free NASA Earthdata login — credentials needed from the developer.
2. Administrative boundary licence/legality for India — must pick a source carefully (ADR-0003).
3. Hydrology library choice — WhiteboxTools to be verified on Windows before committing (ADR-0004).
4. Schedule: ~3 days. Scope is one fully processed pilot, then widen.


## Addendum 2026-10-30: local toolchain and terrain stage
- Offline stage runs natively (venv + wheels, WhiteboxTools exe); no containers (ADR-0006).
- Terrain stage: windowed DEM merge -> UTM reproject -> sea mask -> Horn slope / local relief (NumPy) and fill, D-infinity SCA, TWI, streams, HAND (WhiteboxTools). Peak memory ~200 MB for the Chennai AOI.
- Because the API must stay lightweight (Lambda size limit), it must not import rasterio/geopandas; it serves prebuilt artifacts only.

## Addendum 2026-10-30: schema freeze and API (Phase 3)
- Flow: `run_exposure.py` (raw analytical outputs, unchanged) -> `build_api_artifacts.py` -> `data/api/<region>/`
  (JSON validated against `services/api/floodlens_api/schemas.py`) -> FastAPI `/v1` (read-only).
- Contract: Pydantic models are the source of truth; `scripts/export_schemas.py` writes `schemas/v1/*.schema.json`
  and real-data fixtures; `--check` and tests fail on drift. Any change needs a new schema version.
- Viewport serving: roads are stored as 0.05-degree tiles at `full` and `overview` detail; the API requires a
  `bbox`, reads only intersecting tiles, de-duplicates ways spanning tiles, and picks detail by `zoom` (<13 overview).
- The API package imports no rasterio/geopandas/shapely (tested) to stay inside Lambda size limits.
  Artifacts will be shipped to S3/Lambda layer at deploy time (not done; deployment needs approval).


## Observed rainfall and the web client (Phase 3 addendum)
- **Discovery and publishing (offline):** `floodlens_geo.rainfall.catalog` validates manifests, checksums
  and GeoTIFFs and returns records plus explicit rejections; `rainfall.publish` turns records into API
  documents; `scripts/build_api_artifacts.py` writes them to `data/api/<region>/rainfall/` with a copy of
  the GeoTIFF. Only validated datasets are published. A bad candidate makes its stream `pending`.
- **API (online):** `floodlens_api/rainfall_schemas.py` (schema 1.1.0, additive) and `rainfall.py`
  (freshness, comparison). Routes are documented in `docs/API.md`. The API stays stdlib plus pydantic.
- **Separation:** Final and Late are distinct streams, scenario ids and map layers. Observed rainfall has
  no exposure outputs and is never read by the exposure engine.
- **Web client (`apps/web`):** React 19, TypeScript, Vite, Tailwind, MapLibre, TanStack Query, Zustand,
  Recharts. It calls only the same-origin `/v1` API (Vite proxy in development, CloudFront in production),
  holds no credentials, and caches responses. Rainfall cells (20 polygons) load only when their layer is
  switched on; roads and facilities load per debounced viewport snapped to the 0.05 degree tile grid, only
  at zoom 11 or more. No basemap tiles are bundled: set `VITE_MAP_STYLE_URL` (for example an Amazon
  Location style) at deploy time. Water bodies are not yet published by the API and are shown as such.

## Phase 4 additions
- **Water:** `data/api/<region>/water/` is published from the validated OSM water file; `/water` is bbox-bounded, paged and carries ODbL provenance.
- **Basemap:** the browser loads `/config.json` (written at deploy time) and fetches an Amazon Location Maps v2 style; any failure falls back to a plain background with a banner.
- **Deployment target (prepared, not deployed):** CloudFront (OAC to a private S3 bucket, `/v1/*` to a Lambda Function URL protected by OAC) and a Python 3.13 arm64 Lambda that bundles the artifact tree.
  See `docs/deployment/DEPLOYMENT.md` for the inventory, cost and security review.

## Request path (Phase 7)

```
Browser ──HTTPS GET/HEAD /v1/...──> CloudFront ──SigV4 (OAC), HTTPS──> Lambda Function URL (AWS_IAM) ──> FastAPI (Mangum) ──> artifacts in the package
```

* **CloudFront is the only public entry point.** The browser calls `/v1/...` on the site's own origin and never signs anything; it holds no AWS
  credentials. CloudFront signs each origin request with SigV4 using a Lambda-type origin access control (signing behaviour `always`).
* **Trust model.** The Function URL has `AuthType AWS_IAM`. The Lambda resource policy has exactly two statements, both with principal
  `cloudfront.amazonaws.com` and `SourceArn` = this distribution: `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction` (the latter with
  `InvokedViaFunctionUrl`). Nothing grants `*`. An unauthenticated request to the Function URL host must fail (HTTP 403 from the Lambda
  service, before the function runs); this is asserted on the synthesized template and must be confirmed after deployment.
* **No API Gateway.** The HTTP API, its stage throttle and access logs are gone. A Function URL has no stage throttle and cannot take a WAF; the
  remaining controls are reserved concurrency (optional, hard cap), the `LambdaTrafficSpike` alarm (warning only) and CloudFront caching.
* The event format is Lambda payload format 2.0, which Mangum 0.22 reads (`rawPath`, `rawQueryString`, `headers`, `cookies`, `body`,
  `isBase64Encoded`). Rasters are returned base64-encoded; buffered responses are limited to 6 MB.
* Verified locally only (synthesized template and synthetic events). CloudFront origin authentication, the real Function URL rejection,
  production CSP and the first ARM64 invocation need a deployment to verify.

## Request path (Phase 13, current design)

```
Browser ──HTTPS GET/HEAD /v1/...──> API Gateway HTTP API ($default stage, throttled) ──AWS_PROXY 2.0──> Lambda (FastAPI + Mangum) ──> artifacts in the package
Browser ──HTTPS──> Amplify Hosting (static app) ;  Browser ──HTTPS──> maps.geo.ap-south-1.amazonaws.com (restricted key)
```

* The frontend calls the API cross-origin at `VITE_API_BASE_URL`; API Gateway CORS allows only the Amplify origin(s) (and localhost only if enabled).
* Only `GET` and `HEAD` on `/v1/{proxy+}` are routed; `/docs`, `/openapi.json` and other methods never reach Lambda. The public read-only API is intentional.
* No CloudFront, no cache policy, no Function URL, no S3 bucket of our own. Responses are not cached or compressed by the gateway.
* The Lambda resource policy allows only `apigateway.amazonaws.com` for this API id and route. The sections above that mention CloudFront, OAC or the Function URL
  describe the Phase 7 design that was never deployed. See `docs/deployment/AMPLIFY_MIGRATION.md`.
