# FloodLens release readiness - Phase 6

> **Phase 7 update (2026-10-30).** Status of the Phase 6 findings after implementation (details: [PHASE7_IMPLEMENTATION_REPORT.md](PHASE7_IMPLEMENTATION_REPORT.md)).
> Nothing is deployed; "implemented" means synthesized and tested locally only.
>
> | Finding | Status |
> |---|---|
> | P6-05 public `execute-api` URL (HIGH) | **Implemented** as Function URL + OAC; **to be verified in AWS** (direct refusal, CloudFront acceptance). Residual: no throttle or WAF (new MEDIUM, P7-02). |
> | P6-06 key without expiry (MEDIUM) | **Resolved in code** (mandatory, at most 90 days). |
> | P6-08 HEAD 405 (MEDIUM) | **Resolved** (HEAD added; GET unchanged). |
> | P6-11 alarm recipient | **Resolved in code**; the owner must confirm the subscription. |
> | P6-02 / P6-01 data inventory, no history | **Mitigated**: local release bundle with checksums and a data archive; there is still no historical baseline. |
> | P6-04 tile spend (HIGH), P6-07 ARM64 runtime, P6-09 key deletion, P6-10 CloudFront/CSP unverified | **Unchanged**; need the owner or a deployment. |
>
> The verdict below was written for Phase 6. The current verdict is in the Phase 7 report: **READY FOR OWNER REVIEW, not production-ready**.

Date 2026-10-30. Branch `main`, HEAD `d2c67a2eb08818bcfcbdf7a81990d08cc4a8ba66` ("first commit", the only commit, equal to `origin/main`).
Nothing was deployed, uploaded, committed, pushed or created in AWS. Machine-readable Git baseline: [phase6-git-baseline.json](../../reports/pre-deployment/phase6-git-baseline.json).

Labels: **[V]** verified by something executed or read this phase · **[A]** assumption · **[U]** unknown.

## Verdict

**READY FOR OWNER REVIEW. NOT READY to deploy** until the owner makes the decisions in "Owner decisions" and completes the prerequisites (budgets, alarm email, key expiry). No BLOCKER remains in the code; the former Git BLOCKER (F-01) is downgraded to MEDIUM P6-01 because the cause was a missing commit, which now exists, and what remains is a limitation that cannot be repaired, only acknowledged.

## 1. Git baseline (task 1)

| Question | Answer [V] |
|---|---|
| Working tree clean at start? | Yes (`git status` printed nothing). Phase 6 adds `scripts/data_baseline.py`, the `scripts/release_integrity.py` fix, `reports/pre-deployment/phase6-git-baseline.json`, and the docs below, all uncommitted. |
| Branch / commit | `main` / `d2c67a2` |
| Remote | `origin https://github.com/Thirumalaiboobathi/FloodLens.git`; `git ls-remote` shows `HEAD` and `refs/heads/main` both `d2c67a2`. No earlier history on the remote. |
| Genuine Phase 4 or earlier baseline? | **No.** `git rev-list --count --all` = 1; the commit has no parent. It contains the Phase 5 tree (199 files, 31,156 lines). |
| Schemas and fixtures in the commit? | Yes: 38 files under `schemas/` (19 schemas, 19 fixtures) plus 15 manifests. All equal the working tree after LF normalisation. |
| Untracked or staged files at start? | None. |

**What this does and does not prove.** `d2c67a2` is the *first verified baseline*. It cannot show that the eight frozen v1 schemas or fixtures were unchanged from Phase 1/3/4; no earlier revision exists in this repository or its remote. This report makes no claim of historical preservation. What *is* verified, independent of Git history:

* all 19 schema files equal the Pydantic models' own export (`export_schemas.py --check`: "schemas match models") [V];
* all 19 fixtures validate against their schemas [V];
* none of the 8 frozen v1 schemas references a 1.1.0 model, so rainfall (9 schemas) and water (2 schemas) are additive [V];
* the 24 route operation hashes and the schema hashes are identical to the ones recorded in Phase 5 [V]; 13 of 16 response hashes matched byte-for-byte, and the other 3 differ only because Phase 5 hashed a per-request timestamp (P6-03).

`scripts/release_integrity.py --baseline` was read before running: it, for each frozen v1 schema and fixture, runs `git show <rev>:<path>` and compares LF-normalised bytes with the working file; a difference is a BLOCKER (exit 1); it also records route and response hashes. With `--baseline HEAD` (or the default, which is the root commit = HEAD here) it compares the working tree to the commit, which is meaningful only as "unchanged since the commit". It writes `reports/pre-deployment/release-integrity.json` by default, which would overwrite the Phase 5 hashes, so Phase 6 ran it with `--out` pointing elsewhere; the Phase 5 file is untouched.

**Forward-looking process.** (1) After each commit run `release_integrity.py --baseline d2c67a2 --out <new file>`; (2) before each release run `data_baseline.py --verify`; (3) any change to a frozen v1 file needs a new schema directory and a migration plan, never an in-place edit.

**Git does not cover the data (P6-02).** `.gitignore` excludes `data/raw/` (798 MB), `data/processed/` (255 MB) and `data/api/` (91 MB). Only the 15 manifests are tracked. `scripts/data_baseline.py` was added (read-only on data, streams files, no heavy memory) and recorded SHA-256 for 135 raw/processed files and a combined hash of the 630 `data/api` files into the baseline report; `--verify` printed `UNCHANGED`. This proves "unchanged since recorded today", not "unchanged since Phase 1". The deployable Lambda package is built from the untracked `data/api`, so **the deployed artifact cannot be reproduced from Git alone**.

## 2. Findings register (Phase 6)

Severities: BLOCKER, HIGH, MEDIUM, LOW, INFORMATIONAL. "Owner approval" = the owner must decide or authorise before the item can close. Phase 5 IDs (F-xx) are in [PRE_DEPLOYMENT_REVIEW.md](PRE_DEPLOYMENT_REVIEW.md).

| ID | Sev | Evidence | Impact | Recommended action | Owner approval | Resolved |
|---|---|---|---|---|---|---|
| P6-01 (was F-01) | MEDIUM | Single root commit, remote identical; `rev-list --count --all` = 1 [V] | Historical preservation of frozen v1 cannot be proven | Acknowledge; use forward process above | Yes (accept the limitation) | Partly: baseline recorded, history cannot be recovered |
| P6-02 | MEDIUM | `data/raw`, `data/processed`, `data/api` ignored; only manifests tracked [V] | Deployed artifacts are not reproducible from Git; protected-data checks rely on manifests and a local hash inventory | Keep `data_baseline.py` records; store the inventory and a copy of `data/api` somewhere durable (owner's choice); consider committing `data/api` hashes only | Yes (storage decision) | Mitigated (inventory) |
| P6-03 | LOW | `data_freshness.as_of_utc` changes per request; two calls 1 s apart differ only there [V] | Phase 5 snapshot included 3 unreproducible hashes (my defect) | `release_integrity.py` now excludes that field; repeat run gave identical output | No | **Yes** |
| P6-04 (F-03) | HIGH | Tile spend has no cap; $5,184/month at 50 tiles/s [V arithmetic, COST_MODEL.md] | Financial exposure from a forged-Referer script | Budgets B1/B2, per-key alarm, kill-switch, expiry (SECURITY_OPERATIONS.md 1-2) | Yes (create budgets/alarms) | No |
| P6-05 (F-02) | HIGH | Default execute-api endpoint is public; HTTP API has no resource policy/WAF [V]; throttle shared with CloudFront traffic | Up to about $272/month at 20 rps ($680 at 50 rps); flooding the direct URL can 429 legitimate cache misses | Preferred: Function URL (`AWS_IAM`) + CloudFront OAC; otherwise throttle 20/40 plus alarms and accept (SECURITY_OPERATIONS.md 3) | **Yes** (architecture change, not a contract change) | No |
| P6-06 | MEDIUM | Synthesized key has `NoExpiry: true` unless `locationKeyExpireTime` is passed [V] | A deployment without the flag creates a non-expiring public key, against the 90-day target | Always pass the flag (runbook section 6); optionally make the stack refuse to synth without expiry (needs approval) | Yes | No |
| P6-07 (F-06) | MEDIUM | arm64 native module is statically correct (ELF `e_machine` 183, tag `cp313-aarch64`) [V]; host is x86-64; a WSL attempt to look for an ARM64 emulator was blocked by the permission system and **not retried** | The Lambda has never executed on arm64; an import error would show only after deploy | Check `Init Duration`/import errors at first invoke (runbook section 14); rollback ready | No (verification outstanding) | **No** |
| P6-08 (F-09) | MEDIUM | `HEAD` returns 405 (no `Allow` header) on every real route; API Gateway route and CloudFront advertise GET and HEAD [V] | Uptime monitors, link checkers and some proxies using HEAD will see errors; CloudFront cannot advertise fewer than GET and HEAD | See section 4 | **Yes** (contract) | No |
| P6-09 | MEDIUM | Key resource has no DeletionPolicy; CLI model says `DeleteKey` needs the key deactivated >90 days [V]; whether CloudFormation force-deletes **[U]** | Rollback of a failed first deploy, `cdk destroy`, or key replacement may leave the stack in a failed cleanup state | Test in a throwaway stack with owner approval; never use delete as emergency revocation; rotate by expiring | Yes | No |
| P6-10 (F-07) | MEDIUM | CloudFront, CSP, OAC, `config.json` path verified only by template assertions and a local simulation of the SPA function [V] | Production behaviour unproven | Post-deployment checklist | No | No |
| P6-11 (F-04) | MEDIUM | No alarm recipient unless `alarmEmail` [V] | Alarms fire into nothing | Pass `alarmEmail`; confirm the SNS subscription | Yes (address) | No |
| P6-12 (F-05) | MEDIUM | New accounts can have low Lambda concurrency/memory quotas [V docs] | `reservedConcurrency` can fail the deploy; 1,024 MB memory might be refused | Read Service Quotas first (runbook 7) | No | No |
| P6-13 | LOW | `requirements-lambda.txt` pins versions but not hashes [V] | A compromised or re-uploaded wheel on PyPI would be accepted | Add `--require-hashes` pins before a production launch; pilot risk accepted [A] | No | No |
| P6-14 | LOW | The SPA function rewrites any extension-less path to `/index.html` [V: executed in Node on 9 paths]; the only routes are `/` and `*` | A future client route containing a `.` would 404 | Documented; revisit if routes change | No | n/a |
| P6-15 | INFO | `img-src` has no maps host; MapLibre loads images via `fetch` and blob [A] | Possible CSP console error if a fallback path uses `<img src>` | Check in a real browser; widen only on evidence | No | Open |
| P6-16 | INFO | Cost Anomaly Detection pricing confirmed only by secondary sources; AWS Budgets monitoring free and first two action-enabled budgets free [V: AWS pricing page] | Budget plan cost is about $0 either way | Confirm in console | No | n/a |
| P6-17 | INFO | `age_days` in rainfall freshness is computed from the clock at request time [V: `rainfall.py`] | Responses legitimately change daily | none | No | n/a |
| P6-18 (F-16..F-18) | LOW | Unchanged from Phase 5: default IAM breadth of CDK helpers, S3 403 for missing assets, TLS 1.0/1.1 accepted on the default CloudFront certificate | See Phase 5 | Custom domain and ACM to enforce TLS 1.2 | Yes (domain) | No |

## 3. Verification status by layer

| Item | Locally verified | Synthesized and inspected | Needs real AWS |
|---|---|---|---|
| `/v1/*` routing | Handler invoked with synthetic HTTP-API v2 events: health, rainfall, water, polygons (1.74 MB), raster (base64 image/tiff), 404, HEAD=405 [V] | Distribution has a `/v1/*` behaviour to the API origin; routes `GET`/`HEAD /v1/{proxy+}` | Actual CloudFront to API round trip |
| Cache policy / query forwarding | Test keeps the allow-list equal to the OpenAPI parameters [V] | Policy has the 15-parameter allow list, no headers/cookies, TTL 0/5 min/1 h | Hit/miss behaviour (`x-cache`) |
| `/config.json` | Loader tested with success, non-JSON and failure cases [V] | `no-store` behaviour, same bucket origin | Content type, key value, never cached |
| CSP | None possible without a browser/CloudFront | Policy text present; no `unsafe-eval`; no inline script in `dist/index.html` [V] | Real console |
| CORS | Not applicable: same-origin API [V by design] | No CORS config in stack | Confirm no cross-origin calls in DevTools |
| MIME types | Vite output has `.js`, `.css`, `.html` only [V] | BucketDeployment sets types by extension [A] | `content-type` of each |
| SPA fallback | Function code executed in Node: `/`, `/compare`, `/some/deep/link` -> `/index.html`; assets, `/config.json` untouched [V] | Attached to the default behaviour only | Real viewer requests |
| Compression | `compress: true` on all behaviours [V] | Same | `content-encoding` with `accept-encoding: br` |
| S3 OAC | None | Bucket policy allows the distribution only; public access blocked [V: Phase 5] | Direct S3 URL returns 403 |
| Error responses | App error handlers tested [V]; missing asset behaviour reasoned (S3 403) | No distribution-wide custom error pages | Observed status |
| Map style / key loading | Basemap code tested with mocked `config.json` [V] | Key restrictions in template | Real tiles with a real key |

Nothing in the right-hand column was verified, and nothing here claims CloudFront or CSP production verification.

## 4. HEAD (task 6)

* **Why 405:** FastAPI's `@app.get` routes accept GET only; the custom handler returns the 405 JSON error without an `Allow` header [V]. API Gateway has an explicit `HEAD /v1/{proxy+}` route and the CloudFront behaviours use `ALLOW_GET_HEAD`, so a HEAD from outside reaches the Lambda and is rejected there. The deployed behaviour is therefore consistent but unhelpful.
* **CloudFront constraint [V from the CDK API]:** the smallest method set CloudFront offers is GET+HEAD; HEAD cannot be removed from the distribution.
* **Option A (recommended): support HEAD explicitly** by registering the routes for GET and HEAD (FastAPI and Starlette drop the body for HEAD). Additive, no existing client changes behaviour; an API contract addition. Cost: a HEAD still runs the handler (same Lambda cost as GET) [A].
* **Option B: remove HEAD from the API Gateway route.** Then HEAD returns the HTTP API's 404 (or similar), which is no better for clients and changes the documented routes.
* **Option C: leave it and document it** as GET-only. Lowest effort; uptime checkers must use GET.
* Not changed. Needs the owner's decision because it alters the published contract (OpenAPI would add HEAD operations).

## 5. Lambda ARM64 (task 5)

Executed or read this phase [V]: package rebuilt (98.6 MiB unzipped); `pydantic_core/_pydantic_core.cpython-313-aarch64-linux-gnu.so` has a valid ELF header with 64-bit little-endian class and `e_machine` 183 (AArch64); no `.exe`, no geopandas/rasterio/shapely/numpy/pandas/gdal/fiona/pyproj; the packaged `floodlens_api` source equals the repository source; the bundled `data/api` tree hash equals the source tree; no tests or `__pycache__` in the package; the packaged handler is `floodlens_api.lambda_handler.handler` (Mangum, `lifespan="off"`) and the stack sets runtime python3.13, arm64, 1,024 MB, 15 s; the handler (with the x86 Windows dependencies, not the packaged arm64 ones) answered seven synthetic events including a base64-encoded TIFF and a 1.74 MB JSON body (limit 6 MB); local import took about 1.3 s on x86 (not representative of Lambda cold start).

**Not verified: execution of the packaged arm64 code.** This host is AMD64. I attempted to look for an ARM64 emulator in the existing WSL installation; the permission system denied that command and I did not retry or work around it. Docker is installed but was not used and is not required. Static inspection is not equivalent to running; P6-07 stays open until the first real invocation.

## 6. Regression and scientific integrity (task 9) - all executed 2026-10-30

| Check | Result |
|---|---|
| Geospatial tests | 174 passed |
| API tests | 132 passed (2 existing warnings) |
| Frontend tests (vitest) | 83 passed, 8 files |
| Infrastructure tests | 20 passed |
| Ruff (`geospatial scripts services infrastructure`) | All checks passed |
| TypeScript (`tsc --noEmit`) | clean |
| Production build (`vite build`) | succeeded |
| Schema drift (`export_schemas.py --check`) | "schemas match models" |
| Manifest validation (`validate_data.py`) | 15 ok, 0 failures |
| Offline `cdk synth` | succeeded, 40 resources, throttle 50/100 by default |
| Secret scan (AWS key ids, private keys, JWT-like strings, token assignments) | clean; the only match is a test asserting those strings are absent |
| Git-tracked protected paths vs HEAD (`geospatial`, `schemas`, `data/manifests`, `floodlens_api`) | unchanged; `git status` shows only this phase's own files |
| Ignored data hash inventory | UNCHANGED (135 files + 630 api files) |
| randomError in API / frontend / schemas | no occurrences |
| Exposure code referencing observed rainfall (IMERG, `obs_*`) | none (one docstring uses "observed flood extent", unrelated) |
| Final and Late streams | separate datasets (`obs_final_...`, `obs_late_...`) with separate freshness; not merged [V from API output] |
| ODbL attribution for water | present in `LayerPanel.tsx`, `MapView.tsx`, `main.py`, `water_schemas.py` [V by grep] |

The exposure engine, scoring and outputs were not touched. The `rainfall` input in the exposure configuration is the hypothetical scenario depth used by the pre-existing model, not observed rainfall.

## 7. Owner decisions needed

1. Accept P6-01/P6-02 (no historical baseline; data outside Git) and choose where the data inventory and `data/api` copy are kept.
2. P6-05: option 5 (Function URL + OAC) or option 0 with throttle 20/40.
3. P6-08: HEAD option A, B or C.
4. P6-06: confirm the key expiry date (<= 90 days) and whether to make the stack require it.
5. Create Budgets B1/B2 and Cost Anomaly Detection; provide two alert addresses.
6. Whether to test key deletion/rollback behaviour in a throwaway stack (P6-09).

## 8. Remaining blockers

None in the code. The **conditions** for a deployment approval are the items in section 7 and the prerequisites in the runbook (sections 1-9). Any AWS-changing step (bootstrap, deploy) needs a separate explicit approval.
