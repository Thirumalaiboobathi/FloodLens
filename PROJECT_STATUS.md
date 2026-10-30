# FloodLens — Project Status

_Last updated: 2026-10-30_

## Phase 0 — Repository and architecture: DONE (this commit)

Implemented
- Monorepo skeleton (`apps/web`, `services/api`, `geospatial`, `infrastructure`, `data`, `docs`, …)
- Architecture doc, ADR 0001–0005
- `DatasetManifest` Pydantic model + 7 unit tests (`geospatial/floodlens_geo/metadata/manifest.py`)
- Python venv (`.venv/`), git repository initialised

Verified environment: see ADR-0002. AWS CLI authenticated (account 675613597178, ap-south-1).
Amazon Location responds in ap-south-1. Copernicus DEM public buckets are anonymously listable.

Tests executed: `pytest` in `geospatial/` — 7 passed. `ruff check` — run after fixes (see log).

Not implemented yet: everything in Phases 1–8. No AWS resources have been created.

## Known risks
See `docs/architecture/ARCHITECTURE.md` §7 and ADR-0003/4/5.

## Next: Phase 1 — data pipeline

## Phase 1 — data pipeline: IN PROGRESS (2026-10-30)
Done: raster validation (`raster/validate.py`), DEM discovery/download with GLO-30→GLO-90 fallback
(`ingestion/dem.py`), `scripts/download_data.py dem`. Real Chennai GLO-30 tiles N12/N13 E080
downloaded (3600x3600, 100% valid, checksums in `data/manifests/dem-chennai.json`).
Tests: 13 passed; ruff clean.
Remaining: boundaries (ADR-0003), OSM Tamil Nadu extract, IMERG (needs Earthdata token).

### IMERG rainfall ingestion (2026-10-30)
Implemented `ingestion/imerg.py` + `scripts/ingest_imerg.py` (`probe`, `run`) + `docs/DATASETS.md`.
Auth verified against NASA docs: Earthdata user token as `Authorization: Bearer`; product
`GPM_3IMERGDF` v07 found via public CMR (granule names `3B-DAY.MS.MRG.3IMERG.YYYYMMDD-S000000-E235959.V07B.nc4`).
Unauthenticated request to the data host returns 302 to urs.earthdata.nasa.gov (observed).
Tests: synthetic fixtures only; full suite and ruff run (see log below).
**Not done:** live authentication/download. `EARTHDATA_TOKEN` is not set in the agent's environment,
so no real rainfall has been fetched and no IMERG manifest exists yet. No rainfall data is faked.

### Update 2026-10-30 (session 2)
- **IMERG live verification: NOT done.** `EARTHDATA_TOKEN` was again absent from the agent process, so no
  live probe or download was attempted. No IMERG manifest/GeoTIFF exists. See docs/DEVELOPMENT.md for how
  to set it (session-only, launch VS Code from that shell).
- Docker: none used or required (ADR-0006, docs/DEVELOPMENT.md). WhiteboxTools 2.4.0 verified native.
- Acquired + validated: geoBoundaries ADM1/ADM2 and Chennai polygon, OSM Chennai (roads/hospitals/
  schools/water), terrain derivatives. All have manifests; `scripts/validate_data.py` passes.
- Phase 2 started: terrain module (`terrain/derivatives.py`), `scripts/process_terrain.py`.
- Data on disk (git-ignored): raw 162 MB, processed 151 MB, manifests 44 KB.
- Terrain run: 8.2 s, ~200 MB peak RSS in the Python process (Whitebox runs as a separate process).
- Deviation: breach-depressions was too slow on the flat real DEM; Wang & Liu fill used (ADR-0004).
- Open: IMERG token; India-wide boundary depiction legal review; Chennai polygon area (175 vs ~426 km2);
  negative-elevation cells; remaining Phase 2 (rainfall aggregation, exposure index, scenarios, exports).

### Update 2026-10-30 (session 3) - Phase 2A/2C/2D/2E/2F
- **IMERG (2B): still NOT run.** `EARTHDATA_TOKEN` absent from the agent process again; no live request,
  no rainfall data, no fabricated values. `observed_sample` scenario is produced automatically once an
  `imerg-v07-total-chennai-*.tif` exists.
- **2A terrain quality:** 597 cells < 0 m (the earlier "247" was the < -5 m subset). 597/597 lie on
  Copernicus water-body-mask pixels, so they are water-surface DSM artefacts, kept unchanged and
  excluded from scoring (docs/analysis/TERRAIN_QUALITY.md). Chennai boundary: geoBoundaries polygon (175 km2)
  is 98.6 % inside the OSM district polygon (441 km2): an older city-core extent, not an extraction error.
  Both are kept; no boundary was silently replaced. Official LGD geometry still needed.
- **Bug found and fixed:** warping the Copernicus water mask straight from a dataset band marked all
  land as water on the real tiles (caught only when exposure came out 100 % excluded); fixed with a
  regression test. Rainfall-as-average design also fixed after a test exposed it (ADR-0007).
- **2C/2D:** `floodlens_geo/exposure/` (config, engine, inputs, outputs), `scripts/run_exposure.py`.
  Chennai run: 33 products, 104 MiB, 75-130 s, ~450 MB peak RSS (Python process). Outputs: index/class/
  completeness per scenario, polygons, road ways, facility proximity, regional summaries, manifest.
- **Tests:** 67 passed (was 40); Ruff clean; `scripts/validate_data.py` clean (11 manifests, checksums verified).
- **Known limitations:** uncalibrated heuristic weights; DSM at 30 m; Velachery (known flood-prone) is mid-ranked
  (corrected: an earlier "class 1" note was a single-point impression; see docs/VALIDATION.md); road GeoJSON 9-15 MiB per scenario is too large to serve directly;
  scenarios below 100 mm can score below baseline (reach narrows); no validation against observed floods.
- **Next (not started):** schema-freeze of outputs, then Phase 3 FastAPI. FastAPI deliberately not started.

### Update 2026-10-30 (session 4) - Phase 2.5 validation, schema freeze, Phase 3 API
- **IMERG (C): still NOT run.** `EARTHDATA_TOKEN` is set in the user's shell, but the live probe command
  was declined twice at the permission prompt, so no NASA request was made: authentication, the real V07
  file layout and the observed-rainfall product remain **unverified/pending**. No rainfall was fabricated;
  the API reports `observed_rainfall_status: "pending"` and lists no observed scenario.
- **A (Velachery):** correction - Velachery is mid-ranked (13 % class 1 ... 21 % class 5), not class 1.
  Sensitivity analysis (`scripts/run_sensitivity.py`, 22 one-at-a-time experiments) keeps its mean class
  in 2.94-3.32 whatever the weights/scales, so the limit is in the inputs (DSM, no drainage, 30 m), not
  the weights. Weights were not tuned. See docs/VALIDATION.md.
- **B (historical floods):** candidates identified (UNOSAT 2015, Sentinel Asia ALOS-2 2015, IWMI, NRSC
  2023, OpenCity hotspots); licences/resolutions could not be verified (HDX 403, others 404/gated). No
  validation performed; none fabricated.
- **D (schema freeze):** 8 versioned JSON Schemas in `schemas/v1/` generated from Pydantic models
  (`services/api/floodlens_api/schemas.py`), fixtures built from real Chennai outputs, `--check` drift
  guard. Scenario types: `terrain_baseline`, `hypothetical_rainfall_accumulation`, `observed_rainfall`.
- **E (performance):** roads re-encoded as 0.05-degree tiles in `data/api/` (raw outputs unchanged):
  full detail tiles median 139 KiB / max 843 KiB, overview (simplified) median 58 KiB / max 394 KiB vs
  9-15 MiB monoliths. Vector tiles (PMTiles/tippecanoe) not attempted - not needed yet.
- **F (API):** FastAPI `/v1` (regions, scenarios, summary, comparison, layers, provenance, confidence,
  polygons, facilities, roads), bbox filtering, structured errors, OpenAPI at `/docs`. No rasterio /
  geopandas / shapely import (tested). Not deployed.
- **Tests (executed):** geospatial 72 passed, API 40 passed (112 total); Ruff clean; schemas match models;
  `validate_data.py` 11 manifests ok.
- **Remaining:** IMERG live verification; official boundary; flood-extent validation; frontend (Phase 4).


### Update 2026-10-30 (session 5) - Phase 3: observed rainfall, API integration, map
Done (not committed, not deployed, no data downloaded, exposure engine and outputs untouched):
- Deterministic rainfall discovery and validation (`floodlens_geo/rainfall/`), Final and Late kept separate,
  version read from granule names; 29 + 3 new geospatial tests.
- Additive API schema 1.1.0 and nine new routes (`docs/API.md`); frozen v1 schemas and fixtures unchanged;
  22 new API tests. Observed rainfall is not an exposure scenario and not in any score.
- `build_api_artifacts.py` publishes only validated datasets to `data/api/<region>/rainfall/`; existing
  artifacts byte-identical, `index.json` gains `rainfall_scenarios`.
- First web client in `apps/web`: separate Historical Final and Recent Late layers, native-resolution cells,
  zero-versus-nodata legend, info/provenance/compare panels, viewport-bounded roads and facilities;
  61 frontend tests, typecheck and production build pass.
Not done / limitations: no screenshots (browser extension unavailable; stack checked over HTTP only); no
visual review of the map in a real browser; no basemap (needs `VITE_MAP_STYLE_URL`); water layer not
published by the API; rainfall not in the exposure index; NASA randomError inquiry still unsent; no
Early Run; no scheduled ingestion; AWS infrastructure and deployment await approval.

### Update 2026-10-30 (session 6) - Phase 4: visual QA, water API, basemap, CDK preparation
Done (not committed, not deployed, no AWS resource created, no data downloaded, exposure engine and outputs untouched):
- Real-browser QA (Chrome, desktop): rainfall layers, legend, provenance, comparison, viewport loading, error state. Fixed: opaque rainfall
  fill hid the exposure layer; cramped comparison table; no key for roads/facilities; horizontal overflow and legend/attribution overlap on
  narrow screens. Mobile was checked by a 390 px iframe emulation (the window could not be resized), not a device.
- Water bodies and waterways API (`/water`, `/water/provenance`, schema 1.1.0 additive, 28 tests) and a UI layer.
- Amazon Location basemap support (runtime `config.json` or env; fallback and banner on failure). Verified: Maps v2 serves ap-south-1.
- CDK app (`infrastructure/`, 17 tests, `cdk synth` succeeds), `scripts/package_lambda.py`, `docs/deployment/DEPLOYMENT.md`.
Not done: deployment; any check of the deployed CloudFront/CSP/config.json path; real basemap rendering with a valid key (none exists);
verified Amazon Location tile price; TLS 1.2 minimum, WAF, access logs, cost alarms, custom domain.


### Update 2026-10-30 (session 7) - Phase 5: pre-deployment security, cost and release-integrity review
Done (not committed, not deployed, no AWS resource created or changed, no upload, protected data and frozen schemas untouched):
- Findings register with severities, verified-versus-assumed labels: `docs/deployment/PRE_DEPLOYMENT_REVIEW.md`; cost model from the
  AWS Price List: `docs/deployment/COST_MODEL.md`; untested checklist: `docs/deployment/POST_DEPLOYMENT_CHECKLIST.md`;
  machine-readable integrity report: `reports/pre-deployment/release-integrity.json` (`scripts/release_integrity.py`).
- **BLOCKER:** the repository has no commits, so frozen-v1 byte-identity could not be verified against Git history (the brief says
  Phase 4 was committed; this tree shows otherwise). Schemas equal the model export and fixtures validate; hashes are recorded as a new baseline.
- **Corrections to earlier claims:** the Amazon Location tile price is $0.04/1,000 in Mumbai (not "$16-$160"); `cdk synth` was not credential-free
  on this machine (a default profile was used for an identity lookup); offline synth now verified.
- **HIGH, owner decisions:** map-tile spend is uncapped (leaked key + forged Referer); the public execute-api URL cannot be closed on an HTTP API.
- Local changes: CloudFront API cache keyed on the 15 real query parameters only; optional `apiRateLimit`/`apiBurstLimit`/`reservedConcurrency`
  context; explicit 30-day logs for deploy-time helpers; pinned Lambda dependencies and `bin/` removed from the package; `.gitattributes` for
  `schemas/`. Tests: geospatial 174, API 132, frontend 83, infrastructure 20; Ruff, tsc, build, schema drift, manifests, secret scan all pass.
Not done: deployment; any AWS-side verification; budgets/alerts (owner to create); HEAD support decision; custom domain/WAF.
