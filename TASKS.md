# Tasks

## Phase 0
- [x] Inspect environment
- [x] Skeleton, architecture doc, ADRs
- [x] Manifest schema + tests

## Phase 1 — data pipeline
- [x] Boundaries: geoBoundaries ADM1/ADM2 + Chennai polygon + manifests (legal review of India-wide depiction still open)
- [x] Discover Copernicus DEM tiles; fetch Chennai pilot tiles (GLO-30) + manifest
- [x] Rainfall: IMERG V07 ingestion code + 15 tests (token auth, 401/403/429/network, units, clip, manifest)
- [ ] Rainfall: run live probe + sample download (BLOCKED: EARTHDATA_TOKEN not visible to agent process)
- [x] OSM: Chennai AOI via Overpass (roads, hospitals, schools, water) + manifests
- [x] Raster validation utilities + tests
- [x] Manifests for all acquired datasets + `scripts/validate_data.py` (schema, parents, checksums)
- [ ] Re-run IMERG: probe, then 3-day sample (needs EARTHDATA_TOKEN in the agent's environment)

## Phase 2 — geoanalytics
- [x] Slope (Horn), local relief, WhiteboxTools verified, TWI/SCA/streams/HAND for Chennai (tests on analytic surfaces)
- [x] Negative-elevation cells investigated (597, all on water mask); Chennai boundary discrepancy investigated
- [ ] Aspect; coast handling when the sea is not cut by the raster edge
- [x] Rainfall aggregation (IMERG accumulate) and hypothetical depth scenarios (observed sample still pending)
- [x] Exposure index: configurable weights, per-cell renormalisation, completeness, rainfall amplification, relative classes
- [x] Scenario engine, road/facility exposure, polygons, regional summaries, manifests (Chennai)
- [ ] Decide the official Chennai pilot boundary (LGD geometry); calibrate/validate against 2015/2023 flood extents
- [x] Schema-freeze the exposure outputs (schemas/v1, fixtures from real data)
- [x] Sensitivity analysis + Velachery diagnosis (docs/VALIDATION.md)
- [ ] Verify licences + obtain a Chennai 2015/2023 flood-extent dataset, then validate (none confirmed)

- [x] ADR-0006 no-Docker local dev; docs/DEVELOPMENT.md

## Phase 3 — API
- [x] FastAPI /v1 read-only endpoints over data/api artifacts, structured errors, OpenAPI, 40 tests
- [x] Road tiling + zoom-dependent detail
- [ ] Lambda packaging (mangum) + CDK (needs approval before any deploy)

## Phase 4 — Frontend · Phase 5 — CDK · Phase 6 — Hardening
## Phase 7 — Deploy (requires explicit approval) · Phase 8 — Submission


## Phase 3 addendum - observed rainfall (2026-10-30)
- [x] Rainfall discovery/validation layer, Final and Late separate
- [x] Additive API schema 1.1.0 and rainfall routes
- [x] Artifact publishing of validated datasets only
- [x] Web client: layers, legend, info, provenance, compare, viewport loading
- [x] Docs: API.md, DATASETS, METHODOLOGY, ARCHITECTURE, DEVELOPMENT, PROJECT_STATUS
- [ ] Visual review in a real browser; screenshots
- [ ] Publish a water bodies/waterways endpoint; basemap style
- [ ] Decide whether observed rainfall may enter the exposure index (needs validation data)
- [ ] CDK infrastructure and deployment (needs explicit approval)

## Phase 4 addendum (2026-10-30)
- [x] Visual review in a real browser; screenshots; genuine usability fixes
- [x] Water bodies/waterways endpoint and UI layer
- [x] Basemap integration code (Amazon Location, configurable, graceful failure)
- [x] CDK stack, Lambda packaging, deployment documentation (synth only)
- [ ] Explicit approval, then `cdk bootstrap` / `cdk deploy` (account 675613597178, ap-south-1)
- [ ] Create a localhost-restricted Amazon Location key for local basemap checks
- [ ] Verify the Amazon Location tile price; set a cost/usage alarm
- [ ] Custom domain + ACM (enforces TLS 1.2+), optional WAF and CloudFront access logs

## Phase 5 addendum (2026-10-30) - pre-deployment review
- [x] Release-integrity report, security/cost review, post-deployment checklist, local hardening
- [ ] **BLOCKER:** commit the tree (or locate the Phase 4 commit) and re-run `scripts/release_integrity.py --baseline <rev>`
- [ ] Owner decision: public execute-api URL (accept with lower throttle / custom domain / Function URL + OAC)
- [ ] Owner decision: `HEAD` support (API returns 405 today)
- [ ] Owner: create AWS Budgets (account + Amazon Location) and Cost Anomaly Detection; confirm alarm e-mail
- [ ] Check Lambda Service Quotas (read-only) before using `reservedConcurrency`
- [ ] Explicit approval, then bootstrap and deploy; run POST_DEPLOYMENT_CHECKLIST.md; create the per-key usage alarm
