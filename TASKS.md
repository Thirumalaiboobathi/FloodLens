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

## Phase 6 addendum (2026-10-30) - release baseline and readiness
- [x] Git baseline recorded (first verified baseline `d2c67a2`; no earlier history exists); data hash inventory; runbook; security/cost operations plan
- [ ] Owner: acknowledge no historical baseline (P6-01/02); store the data inventory and a copy of `data/api` durably
- [ ] Owner decision P6-05: Function URL + OAC (preferred) or throttle 20/40 and accept the public execute-api URL
- [ ] Owner decision P6-08: HEAD support (A explicit / B remove from API route / C document GET-only)
- [ ] Owner: always pass `locationKeyExpireTime` (<= 90 days) and `alarmEmail`; decide whether the stack should require expiry
- [ ] Owner: create Budgets B1/B2 and Cost Anomaly Detection (SECURITY_OPERATIONS.md 1.2-1.3)
- [ ] Verify the arm64 package on first invoke (runbook 14); test key delete/rollback in a throwaway stack (P6-09)
- [ ] Separate approvals: GATE 2 bootstrap, GATE 3 deploy (DEPLOYMENT_RUNBOOK.md)

## Phase 7 addendum (2026-10-30) - Function URL + OAC, HEAD, mandatory settings, release bundle
- [x] Lambda Function URL (AWS_IAM) + CloudFront OAC replaces API Gateway (synth + 32 infrastructure tests); HEAD on all GET routes; mandatory key expiry and alarm e-mail
- [x] `scripts/create_release_bundle.py`, bundle `2026-10-30-rc1`, `docs/deployment/RELEASE_ARTIFACTS.md`, `PHASE7_IMPLEMENTATION_REPORT.md`
- [ ] Owner: commit Phases 6 and 7, then regenerate the bundle from a clean tree; store `reports/releases/<id>/` durably
- [ ] Owner decision P7-02: reservedConcurrency (after a quota check) / accept / WAF on CloudFront
- [ ] Owner: supply alarm e-mail and key expiry (<= 90 days); confirm the SNS subscription; calendar the key rotation
- [ ] Owner: create budgets and Cost Anomaly Detection; separate approvals for bootstrap and deploy
- [ ] After an approved deploy: POST_DEPLOYMENT_CHECKLIST.md gate H (direct Function URL must be 403; CloudFront must reach it; first ARM64 invocation)

## Phase 8 addendum (2026-10-30) - release audit
- [x] Audit, approval packages, post-deployment plan; read-only AWS readiness (bootstrap present v32; Lambda quota 10)
- [ ] Owner: decide whether to remove the I-1 template object `9f43afec...json` from the CDK staging bucket (optional)
- [ ] Owner: request Lambda concurrency quota increase (Service Quotas), or accept the shared quota of 10; then use `-c reservedConcurrency=10`
- [ ] Owner: commit Phases 6-8; `git status` empty; create a clean bundle (new id, no `--allow-dirty`); verify; store off this machine
- [ ] Owner: create budgets ($25 account, $15 Location) and confirm anomaly-detection subscription; choose alarm e-mail and key expiry (<= 90 days)
- [ ] Owner: written approval for deployment only after DEPLOYMENT_APPROVAL_PACKAGE.md section 2 is met; no bootstrap needed

## Phase 9 addendum (2026-10-30)

- [x] Review I-1 staging object (read-only) and correct `cdk diff` guidance
- [x] Lambda quota plan, cost-protection setup, IAM requirements, commit checklist, Phase 9 readiness
- [x] Local regression and secret scan
- [ ] Owner: commit Phases 6-9
- [ ] Owner: request Lambda concurrency quota 1000; then deploy with `reservedConcurrency=10`
- [ ] Owner: create $25 account and $15 Amazon Location budgets; confirm anomaly subscription
- [ ] Owner: choose alarm e-mail and key expiry (at most 90 days)
- [ ] Owner: decide deployer identity (avoid long-term admin)
- [ ] Clean release bundle (after commit), copy off-machine
- [ ] Written deployment approval; then post-deployment verification
