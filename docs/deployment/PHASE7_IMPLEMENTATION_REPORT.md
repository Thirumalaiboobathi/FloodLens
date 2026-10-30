# FloodLens Phase 7 implementation report

> **Superseded in part by Phase 13 (2026-10-30).** The deployment target changed from CloudFront + S3 + Lambda Function URL to
> **AWS Amplify Hosting + API Gateway HTTP API + Lambda** (stack `FloodLensAmplify`), after the first CloudFront deployment failed on the
> account's cache-policy query-string quota (see `reports/releases/2026-10-30-rc2/ROLLBACK-INVESTIGATION.md`). Statements below about
> CloudFront, the Function URL, OAC, `config.json`, the 4-alarm set, reserved concurrency and the `FloodLens` stack describe the old design.
> Current procedure: [AMPLIFY_MIGRATION.md](AMPLIFY_MIGRATION.md). Kept for the audit trail; its commands were never run against AWS.


Written for: the project owner (review before any deployment decision). 2026-10-30.
**Nothing was deployed, bootstrapped, uploaded, committed or pushed, and no AWS resource was created or changed.** All verification is local (tests, synthesized template, synthetic events).
Labels: **[V]** verified by something executed this phase · **[A]** assumption · **[U]** unknown.

## 1. Starting state

| Item | Value |
|---|---|
| Branch / HEAD | `main` / `d2c67a2eb08818bcfcbdf7a81990d08cc4a8ba66` ("first commit"), equal to `origin/main`; the only commit |
| Expected Phase 6 commit | **Not present.** The prompt expected `HEAD` to be the owner's Phase 6 commit; it is still the first commit. The Phase 6 documents and scripts exist but were uncommitted (finding P7-11). Work proceeded on the working tree. |
| Working tree at start | Modified: `PROJECT_STATUS.md`, `TASKS.md`, `PRE_DEPLOYMENT_REVIEW.md`, `scripts/release_integrity.py`. Untracked: the three Phase 6 docs, `phase6-git-baseline.json`, `scripts/data_baseline.py`. Nothing unexpected. |

## 2. Architecture changes [V locally]

Browser → CloudFront → **Lambda Function URL (`AWS_IAM`) with CloudFront OAC** → FastAPI (Mangum) → artifacts in the package. API Gateway was removed.

* `infrastructure/lib/floodlens-stack.ts`: removed the HTTP API, stage, integration, routes, access-log group and API Gateway permission/alarm/dashboard widgets. Added `addFunctionUrl({authType: AWS_IAM, invokeMode: BUFFERED})` and
  `FunctionUrlOrigin.withOriginAccessControl` (exists in the installed aws-cdk-lib 2.275.0; read from its `.d.ts`/`.js`, not assumed). CDK creates a lambda-type OAC (`SigningBehavior always`, `sigv4`) and two Lambda permissions.
* The `/v1/*` behaviour, cache policy (15 query parameters, 0 s / 5 min / 1 h), response-headers policy, GET+HEAD methods, `/config.json` behaviour and the S3 OAC are unchanged. The public path is still `/v1/...`.
* Synthesized template: 37 resources (was 40). `ApiEndpoint` output replaced by `ApiFunctionUrl` (documented as *not* a public entry point).
* Origin: HTTPS only, TLS 1.2, origin read timeout 20 s (function timeout is 15 s). No origin request policy, so the viewer `Host` header is not forwarded (required for SigV4 to a Function URL) [A: not testable without AWS].
* `services/api/floodlens_api/lambda_handler.py`: docstring only.

## 3. Function URL and OAC security (what is and is not proven)

Proven on the synthesized template by 7 dedicated infrastructure tests plus the storage and secrets tests [V]:

| Requirement | Evidence |
|---|---|
| Function URL uses AWS_IAM, exactly one, no `Cors` | `AWS::Lambda::Url {AuthType: AWS_IAM, InvokeMode: BUFFERED}` |
| CloudFront uses OAC for the API | `OriginAccessControl {OriginAccessControlOriginType: lambda, SigningBehavior: always, SigningProtocol: sigv4}` referenced by the one custom origin; `/v1/*` targets that origin |
| Permissions scoped | exactly two `AWS::Lambda::Permission`: `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction` (`InvokedViaFunctionUrl: true`), principal `cloudfront.amazonaws.com`, `SourceArn` = `arn:…:cloudfront::<account>:distribution/<this distribution>` |
| No public invocation | no `*` principal, no `AuthType NONE`, no other Lambda permission |
| API Gateway not an origin | zero `AWS::ApiGateway*` resources, no `execute-api` string anywhere, no `ApiEndpoint` output, no throttle settings |
| Browser holds no signing material | frontend sources contain no SigV4/`x-amz-`/AWS SDK/access-key strings; `config.json` carries one marker, the public map key; `/v1` base path unchanged |
| S3 still private with OAC | existing storage tests |

**Not proven (needs a deployment):** that unsigned requests to the Function URL return 403; that CloudFront's signed request is accepted (including with query strings, HEAD and 1.7 MB responses); and that the InvokeFunctionUrl permission works without `FunctionUrlAuthType` (CDK omits it; AWS's CloudFront OAC guidance also omits it [A]). These are gate H in `POST_DEPLOYMENT_CHECKLIST.md`.

**Protections that differ from API Gateway:** a Function URL has no stage throttle, usage plan, resource policy on the URL itself, or WAF. Available: reserved concurrency (hard cap, `-c reservedConcurrency`, off by default, check quota first), the new `LambdaTrafficSpike` alarm (3000 invocations/5 min default, alarm only), Lambda error/throttle/p95 alarms, CloudFront caching (abuse must come through CloudFront), and optionally WAF on CloudFront (not added; billable). Documented in `SECURITY_OPERATIONS.md`.

## 4. HTTP HEAD [V]

`HEAD` is added to every `GET` route under `/v1` (24 routes) by registering HEAD routes from the GET routes, plus an ASGI middleware that drops the body of HEAD responses. Routes run exactly as for GET, so status, validation and error codes cannot diverge. JSON keeps the GET `Content-Length`; the GeoTIFF is headers-only and the file is never opened (asserted by making `anyio.open_file` fail). OpenAPI gains 24 `head` operations derived from the GET operations (no response content, unique `operationId`s).

* **GET unchanged [V]:** all 24 GET operations and the rest of the OpenAPI document hash identically to `HEAD`'s code (checked against `git archive HEAD`); the 16 normalized response snapshot hashes equal Phase 6's.
* Other methods and `OPTIONS` still return `405 method_not_allowed`; no CORS header is ever sent.
* 28 parity cases (GET vs HEAD: status, headers except per-request ones, content-type, `Content-Length` = length of GET body, empty body) over catalog, coverage, comparison, freshness, summary, provenance, cells, raster, water, polygons and error paths (422 `bbox_too_large`, missing/unknown region and dataset, unknown route, invalid parameters), plus the Function URL event path for HEAD.
* [U] Whether the Lambda service/CloudFront preserve a `Content-Length` that does not match an empty body on HEAD. Cosmetic; checked in the post-deployment list.

## 5. Key expiry and alarm e-mail [V]

* `locationKeyExpireTime` is required: ISO-8601 with a time zone, a real calendar date (a defect was found and fixed: JavaScript rolls `2026-02-30` over to March), in the future, at most 90 days ahead (exactly 90 days accepted, 90 days + 1 s rejected), normalized to UTC. Missing, malformed, impossible, expired and too-distant values abort the synth, both in the stack and in `bin/floodlens.ts`. The template always has `NoExpiry: false` and an `ExpireTime`; `NoExpiry: true` can no longer be produced.
* `alarmEmail` is required and format-checked; the stack always creates the SNS e-mail subscription. **The recipient must click the confirmation link; an unconfirmed subscription receives no alarms.** No topic exists in AWS and no e-mail was sent.
* `apiRateLimit` / `apiBurstLimit` now abort the synth with an explanation instead of silently doing nothing.
* The map key stays a public browser key, restricted by actions, resource and Referer (header-based, so it constrains browsers, not scripts); it is not described as secret.
* Local development is unaffected: no AWS key is needed to run the app or tests.

## 6. Release bundle [V]

`scripts/create_release_bundle.py` (create/verify) and `docs/deployment/RELEASE_ARTIFACTS.md`. Bundle `reports/releases/2026-10-30-rc1/` (manifest, checksums, validation, and a 13.5 MB deterministic `data-api.tar.gz`, git-ignored): 1,869 files inventoried, 15/15 checks PASS, `verify` prints OK, tamper test rejected. Hashes are in RELEASE_ARTIFACTS.md. It was made on a **dirty** tree (this phase is uncommitted), so the commit hash alone does not identify the source; the `source_tree` listing hash does. Recreate it from a clean tree after the owner commits. Not uploaded.
Two defects in the new script were found and fixed by running it: an over-strict duplicate-path check (a manifest file is in two inventories) and an aggregate comparison that sorted differently from the Phase 6 script (it reported a data difference that did not exist; `data_baseline.py --verify` independently said UNCHANGED).

## 7. Lambda ARM64 package [V static; runtime NOT verified]

Static: the one native module (`pydantic_core`) is ELF AArch64, tag `cpython-313-aarch64-linux-gnu`; no `.exe/.dll/.pyd`/PE headers; none of GeoPandas, Rasterio, Fiona, Shapely, PyProj, GDAL; handler `floodlens_api.lambda_handler.handler` resolves; package code equals source; 630 packaged artifacts equal `data/api`; 12 installed versions equal the pins; 98.6 MiB unzipped (limit 250), 16.1 MiB zipped.
Behaviour: the handler was run on this x86-64 host with Function URL v2-shaped synthetic events: GET JSON, missing optional fields, percent-encoded query, traversal attempt, error envelopes, base64 GeoTIFF byte-identical to the file, a 1.74 MB polygon response identical to the direct response and under 6 MB, HEAD (empty body, correct length), 405s, no CORS, unknown event shape rejected. The events follow the documented payload format; they are not captured real events [A].
**Outstanding:** no genuine Linux ARM64 environment is available, and no emulator was installed or enabled (that needs approval). The first ARM64 invocation is a post-deployment gate.

## 8. Frontend compatibility

Not changed. `/v1` base path, `/config.json` loading, basemap code, and the rainfall (Final vs Late separation, legend, nodata), water (ODbL attribution) and provenance components are covered by the unchanged 83 frontend tests, which pass; a test also checks the client still builds `${BASE}/v1…` and that no frontend source has signing code. **Browser tests were not run** in Phase 7 and nothing here validates real CloudFront, the production CSP, or Amazon Location tile rendering.

## 9. Test results [V, all executed this phase]

| Suite | Result |
|---|---|
| Geospatial | 174 passed (expected 174) |
| API | 189 passed, 1 skipped (a duplicate-path test that needs a case-sensitive file system), 0 failed. Was 132; +57 (HEAD/Function URL 47, release bundle 10) |
| Frontend | 83 passed |
| Infrastructure | 32 passed (was 20) |
| Ruff (geospatial, services, scripts) | clean |
| TypeScript (web, infrastructure) | clean |
| Web production build | succeeded |
| Schema drift (`export_schemas.py --check`) | "schemas match models" |
| Manifest validation (`validate_data.py`) | all ok |
| Release integrity (`release_integrity.py`, written to a scratch file) | schema files/versions/additive groups identical; 16 GET snapshots identical; routes differ only by the added HEAD operations (24 GET operation hashes proven identical separately) |
| Secret scan (bundle check) | none found in source, web build, Lambda package |
| Offline `cdk synth` | succeeded, 37 resources, credentials disabled |

A failing geospatial guard test (no IMERG random-error field in application code) flagged a comment I had written in the new script; the comment was reworded, the guard untouched.

## 10. Protected-data integrity [V]

`schemas/`, `data/fixtures/`, `data/manifests/`, `geospatial/` have no diff against `HEAD`; `scripts/data_baseline.py --verify`: UNCHANGED (135 raw/processed files + 630 `data/api` files); `phase6-git-baseline.json` and the Phase 5 `release-integrity.json` unmodified by this phase. Exposure code, scoring and published exposure outputs untouched; rainfall remains separate from exposure; Final and Late streams are distinct; the IMERG random-error field is absent from API code, frontend and artifacts; water responses keep ODbL attribution. No raw, processed or manifest file was modified. Files this phase changed: stack, CDK entrypoint and tests, `main.py`, `lambda_handler.py` (docstring), new API tests, new release script, Makefile, `.gitignore`, documentation.

## 11. Findings

| ID | Severity | Finding | Status |
|---|---|---|---|
| P7-01 | MEDIUM | Function URL + OAC works only on the synthesized template. Direct-URL refusal, CloudFront acceptance (query strings, HEAD, large response), the `Host`-header assumption and the permission without `FunctionUrlAuthType` are unverified. | Open: deployment gate H. Owner approval needed to deploy. |
| P7-02 | MEDIUM | No throttle, usage plan or WAF on the API path. Abuse limits are reserved concurrency (off), a warning-only alarm, caching and budgets. | Open. Owner decision: set `reservedConcurrency` after checking quota; accept; or approve WAF on CloudFront (billable). |
| P7-03 | HIGH | (P6-04, unchanged) Map-tile spend is uncapped; the key is public. Mitigated only by mandatory expiry, budgets (owner), Referer restriction. | Open, owner. |
| P7-04 | MEDIUM | (P6-07) ARM64 runtime unverified; no ARM64 environment, no emulator installed. | Open: post-deployment gate, or owner approves an emulator. |
| P7-05 | MEDIUM | (P6-09/10) Key deletion/rollback behaviour and CloudFront/CSP/tile rendering remain unverified. Rotating the key by changing `ExpireTime` is assumed to update in place [A]; if CloudFormation replaces the key, the old key and the `config.json` value both change. | Open. |
| P7-06 | LOW | Function URL tests use synthetic events built from the documented format, not captured real events. | Open, covered by gate H. |
| P7-07 | LOW | `HEAD` `Content-Length` handling through Function URL/CloudFront unknown [U]. | Open, cosmetic. |
| P7-08 | LOW | Browser/E2E tests not run this phase. | Open. |
| P7-09 | LOW | The 90-day key lifetime means a rotation (redeploy with a new date) is required at least every 90 days; an expired key blanks the basemap (the app falls back to a plain background with a banner). No reminder exists in AWS. | Open, owner process. |
| P7-10 | LOW | `COST_MODEL.md` and `cost_model.py` still contain the API Gateway lines (marked superseded; not rerun). | Open, documentation. |
| P7-11 | MEDIUM | The expected Phase 6 commit does not exist; `HEAD` is the first commit, and Phases 6 and 7 are uncommitted. The release bundle therefore records a dirty tree. | Open: owner to commit, then regenerate the bundle on a clean tree. |
| P7-12 | INFORMATIONAL | Pip wheels are still not hash-pinned (P6-13); the bundle records installed versions. | Open. |
| P7-13 | INFORMATIONAL | `ApiEndpoint` output was removed and replaced by `ApiFunctionUrl`; any operator note referencing the old output is obsolete (runbook and checklist updated). | Resolved. |

No BLOCKER. Resolved this phase: P6-05 (implemented, to verify), P6-06, P6-08, P6-11; mitigated: P6-02.

## 12. Remaining deployment risks

First deployment of a never-run CloudFront→Lambda IAM path; uncapped tile spend; no API throttle; no WAF; unverified ARM64 import; TLS 1.2 minimum not enforceable on the default CloudFront certificate; the key expiry rotation duty; the SNS subscription needing confirmation; the Lambda concurrency quota of a new account; and key deletion behaviour on rollback.

## 13. Owner decisions still required

1. Approve (or decline) the Function URL + OAC deployment path for real; decide on API protection: `reservedConcurrency` value after a quota check, accept as is, or WAF on CloudFront.
2. Supply the alarm e-mail address and the key expiry date (at most 90 days); commit to confirming the SNS subscription and to rotating the key before expiry.
3. Commit Phases 6 and 7 (the assistant does not commit), then regenerate the release bundle from a clean tree; choose durable storage for `reports/releases/<id>/` including the data archive.
4. Create the AWS budgets and Cost Anomaly Detection (SECURITY_OPERATIONS.md) before any deployment.
5. Decide whether to test an ARM64 emulator locally or accept the post-deployment first invocation as the ARM64 gate.
6. Separate written approvals for `cdk bootstrap` and for `cdk deploy`.

**Recommendation: READY FOR OWNER REVIEW. Not production-ready and not approved for deployment.**
