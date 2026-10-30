# FloodLens — Pre-deployment security, cost and release-integrity review (Phase 5)

> **Superseded in part by Phase 13 (2026-10-30).** The deployment target changed from CloudFront + S3 + Lambda Function URL to
> **AWS Amplify Hosting + API Gateway HTTP API + Lambda** (stack `FloodLensAmplify`), after the first CloudFront deployment failed on the
> account's cache-policy query-string quota (see `reports/releases/2026-10-30-rc2/ROLLBACK-INVESTIGATION.md`). Statements below about
> CloudFront, the Function URL, OAC, `config.json`, the 4-alarm set, reserved concurrency and the `FloodLens` stack describe the old design.
> Current procedure: [AMPLIFY_MIGRATION.md](AMPLIFY_MIGRATION.md). Kept for the audit trail; its commands were never run against AWS.


> **Phase 7 note.** F-02 (public `execute-api` URL) and F-09 (HEAD 405) are addressed in code (Function URL + OAC; HEAD support), the key expiry (F-03) and the alarm e-mail (F-04) are mandatory. See [PHASE7_IMPLEMENTATION_REPORT.md](PHASE7_IMPLEMENTATION_REPORT.md). Nothing is verified in AWS.

Reviewed: 2026-10-30. Scope: local review of the Phase 4 tree. **Nothing was deployed, uploaded, committed or pushed;
no AWS resource was created or changed; no protected data, manifest, exposure output or frozen schema was modified.**

Legend used throughout: **[V]** verified this session (source named) · **[A]** assumption or estimate · **[U]** unknown / not verifiable before deployment.
Severity: BLOCKER (stop; do not approve deployment) · HIGH · MEDIUM · LOW · INFORMATIONAL.

## 0. Verdict

**Not ready for deployment approval yet. Ready for owner review.** One BLOCKER (release integrity cannot be proven
because there is no Git history) and two HIGH risks that need an owner decision (uncapped map-tile spend; public
execute-api URL). Everything else is MEDIUM or below and has a stated mitigation or a post-deployment check.
Exact prerequisites are in section 10.

## 1. Findings register

| ID | Sev | Finding | Status |
|---|---|---|---|
| F-01 | **BLOCKER** | The repository has **no commits** (`git log` fails: "current branch 'main' does not have any commits yet") and `schemas/`, `services/`, `scripts/*` Phase 2–4 files, `infrastructure/` and `reports/` are untracked [V]. The brief says Phase 4 was committed by the owner; this working tree does not show that. The frozen-v1 byte-identity comparison against Git history **cannot be performed**. | Open — needs owner. See §2. |
| F-02 | HIGH | Direct `execute-api` URL stays publicly usable and **cannot be closed on an HTTP API without a custom domain** [V: AWS docs]. Only the stage throttle bounds it: worst case ≈ **$680/month** if 50 rps were sustained for a month [A: 300 ms Lambda]. | Open — owner decision. See §3. |
| F-03 | HIGH | **Map-tile spend has no cap.** The browser key is public by design; its Referer restriction is enforced from a request header that non-browser clients can set [V: AWS docs say the service "authorizes requests only when the HTTP Referer header matches"]. A leaked-key client at 100 tiles/s ≈ **$10,368/month** at the verified $0.04/1,000 price. Quotas are rate limits, not spend caps [V]. | Open — budgets and alarms are owner actions. See §4. |
| F-04 | MEDIUM | Alarms have **no recipient** unless `-c alarmEmail=…` is passed [V: stack code; test "subscribes an email only when one is supplied"]. A default deploy would alarm silently. | Open — prerequisite. |
| F-05 | MEDIUM | New AWS accounts can have **reduced Lambda concurrency and memory quotas** [V: Lambda quotas page]. A reserved-concurrency reservation can fail a deploy if it leaves < 100 unreserved [A, from AWS behaviour; not quoted on the page I read]. The account's real quotas are unknown [U]. | Open — check Service Quotas (read-only) before deploy. |
| F-06 | MEDIUM | The Linux/arm64 Lambda package has **never been executed** — the native `pydantic_core` module can only load on Linux arm64 [V: wheel tag `cp313-manylinux2014_aarch64`; local import impossible on Windows]. Handler logic was exercised on Windows with the identical pure-Python code and identical dependency versions [V]. | Mitigated by wheel-tag check; first real test is the post-deployment smoke test. |
| F-07 | MEDIUM | CloudFront routing, CSP, `config.json`, OAC and the `describeKey` custom resource are validated only by template assertions and synth [V]. | Post-deployment checklist. |
| F-08 | MEDIUM | No usage alarm on the map key. The metric exists (`AWS/Location`, dimension `ApiKeyName`, `CallCount`) [V: AWS docs search result] but the **exact dimension set for tile calls is not documented in what I could read** [U]; an alarm built on a guessed dimension would never fire. | Create after first traffic, from `list-metrics`. |
| F-09 | MEDIUM | `HEAD` requests return **405** from the API (tested locally) while the HTTP API route and CloudFront advertise GET and HEAD [V]. Unused by the frontend. Fixing it changes published API behaviour (additive), so **not changed — asking**. | Owner decision. |
| F-10 | MEDIUM | Phase 4 statement "synth works with no credentials" was **inaccurate**: this machine has a default AWS profile and the CLI used it for a read-only identity lookup (the account appeared in the template). Offline synth is verified now with credential/config files pointed at nothing; it leaves the account `unknown-account`, so the account guard only acts when the CLI resolves an identity at deploy time [V: both directions tested]. | Corrected in DEPLOYMENT.md. |
| F-11 | LOW | `schemas/` use CRLF on this Windows checkout (`core.autocrlf=true`, no `.gitattributes`) [V]. Byte hashes differ by platform. | **Fixed:** `.gitattributes` (`schemas/** text eol=lf`); report hashes the LF-normalised form. |
| F-12 | LOW | Lambda dependencies were `>=` ranges: not reproducible [V]. | **Fixed:** `services/api/requirements-lambda.txt` pins the exact tested versions; packager uses it. |
| F-13 | LOW | Windows console scripts (`bin/*.exe`) leaked into the Lambda package [V]. | **Fixed:** packager removes `bin/` and verifies the aarch64/cp313 native module. |
| F-14 | LOW | Helper Lambdas (key reader, two bucket deployments) logged to auto-created groups with **no retention** [V: template]. | **Fixed:** three explicit 30-day groups (DESTROY on stack deletion). |
| F-15 | LOW | The API cache keyed on the *entire* query string, so random extra parameters defeated the cache [V]. | **Fixed:** CloudFront forwards/keys only the 15 parameters the API reads; a Python test keeps the list equal to the OpenAPI document. |
| F-16 | LOW | The bucket-deployment role holds `s3:DeleteObject*` on the site bucket although `prune:false`, and `cloudfront:CreateInvalidation` on `*` [V: template]. Both are CDK defaults. | Accepted; documented. |
| F-17 | LOW | Missing static assets return S3's XML **403** (OAC grants GetObject only, no ListBucket) and there is no favicon in `public/` [V]. | Accepted. |
| F-18 | LOW | TLS 1.2 minimum cannot be enforced on the default `*.cloudfront.net` certificate [V earlier]. | Custom domain needed. |
| F-19 | INFO | **Amazon Location tile price is now verified: $0.04 per 1,000 tiles in Mumbai** (`APS3-Usage-Default-Maps-Tiles`, op `GetTile`; Price List published 2026-09-11) [V]. The Phase 4 upper bracket ($160) was wrong. | Corrected. |
| F-20 | INFO | Package 98.6 MiB unzipped / ≈ 16 MB zipped vs limits 250 MB unzipped, 50 MB zipped [V: Lambda quotas]. Largest response 2.09 MB vs 6 MB synchronous limit [V]. | OK. |
| F-21 | INFO | The map key being readable at `/config.json` is expected: it appears in every tile URL anyway [V]. | OK. |
| F-22 | INFO | `/docs`, `/openapi.json` are not reachable through the HTTP API (the only route is `/v1/{proxy+}`) [V: template]. | OK. |

## 2. Task 1 — Release integrity

Machine-readable: `reports/pre-deployment/release-integrity.json` (generated by `scripts/release_integrity.py`).

What was **not** possible [V]: comparing the original frozen v1 schemas and fixtures with "the latest committed version
using Git history". There are zero commits, so there is no earlier version and no later version. Every `schemas/v1/*`
file, including the 8 frozen v1 schemas and their fixtures, has the same modification time to the millisecond
(2026-10-30 14:04:11) because the exporter regenerates them together. **I therefore make no claim that the v1 files are
byte-identical to their original release.** An earlier session's "byte-identical" statement had no independent baseline either.

What **was** verified [V]:

* all 19 schema files equal the LF-normalised export of their Pydantic models (8 frozen v1 + 9 rainfall 1.1.0 + 2 water 1.1.0);
* all 19 fixtures validate against their schemas (jsonschema 4.26);
* the 8 frozen v1 schemas contain no reference to water or rainfall models (the water additions are isolated);
* the water additions are two new schema files, two new fixtures and two new routes (`/water`, `/water/provenance`) and nothing else under `/v1/regions/{region}/water*`;
* `export_schemas.py --check`: "schemas match models";
* the report records SHA-256 of every schema/fixture, a hash of each of the 24 route operations, and response hashes for 16 fixed requests — a **baseline for the next review**, not evidence about the past.

To close F-01 the owner can: (1) confirm where Phase 4 was committed (a different clone?); or (2) commit the tree and
re-run `python scripts/release_integrity.py --baseline <earliest commit that contains schemas/>`. The script already
compares the 8 frozen schemas and fixtures byte-for-byte (LF-normalised) against any revision and exits non-zero on a difference.

## 3. Task 2 — API origin security

Facts [V: AWS API Gateway "Choose between REST APIs and HTTP APIs" page; CloudFront OAC-for-Lambda page]:

| Control | HTTP API | Note |
|---|---|---|
| Resource policy | **No** | the usual "allow only CloudFront IPs / source" mechanism does not exist for HTTP APIs |
| AWS WAF | **No** | WAF can attach to the CloudFront distribution instead |
| API keys / usage plans / per-client throttling | **No** | only stage/route throttling |
| IAM authorizer, JWT, Lambda authorizer | Yes | none of which a CloudFront origin can satisfy without sending credentials |
| Disable the default endpoint | Exists as an API setting [A, recalled] | only useful with a custom domain, which this pilot does not have |

Options considered:

1. **Accept the public execute-api URL** (recommended for the pilot) — the data is public, read-only and GET-only; the risk is cost, not confidentiality. Bound it with a lower throttle and budgets (below).
2. **Shared-secret origin header** (CloudFront adds `X-Origin-Verify`, Lambda rejects without it) — works mechanically, but the secret lives in the template/CloudFormation console, rotation is manual, and a rejected request is still billed (API Gateway + a Lambda invocation). It stops casual scraping, not cost abuse. **Not implemented** — weak and not testable before deployment.
3. **Lambda Function URL (`AWS_IAM`) + CloudFront OAC** — the only mechanism that is IAM-enforced without a custom domain [V: OAC supports Lambda function URLs, requires `AuthType AWS_IAM`, GET needs no payload hash]. It removes the API Gateway throttle, so the **only** rate bound becomes reserved concurrency (possibly unavailable on a new account, F-05), and an attacker can still reach the origin through CloudFront by cache-busting. Replaces the committed architecture and cannot be behaviour-tested locally. **Not implemented; offered as the upgrade path** together with a WAF rate rule on CloudFront.
4. **Custom domain + disable default endpoint** — closes the bypass; needs a domain and ACM certificate (also fixes TLS 1.2, F-18).

Important insight [V by calculation]: locking the origin does not change the spend ceiling, because an attacker can still
send cache-missing requests *through* CloudFront and they hit the same throttle. What bounds spend is the throttle,
reserved concurrency and alerts.

**Implemented locally (no contract change, local dev unaffected):**

* CloudFront API cache policy now forwards and keys only the 15 query parameters the API reads (`API_QUERY_PARAMS`), so random parameters can no longer force misses. Guard test `services/api/tests/test_deploy_contract.py` fails if the list and the OpenAPI document diverge.
* Optional CDK context `apiRateLimit`, `apiBurstLimit`, `reservedConcurrency` (defaults unchanged: 50/100, no reservation). Example for a pilot: `-c apiRateLimit=20 -c apiBurstLimit=40`, which lowers the worst-case month from ≈ $680 to ≈ $272 [A].
* Infrastructure tests for the above (20 tests, all passing).

**Recommendation:** option 1 now, with `apiRateLimit` ≈ 20 (size it from expected peak: 50k sessions/month averages well under 1 rps [A]) and budgets (§4/COST_MODEL.md); move to option 3 or 4 before any public launch.

## 4. Task 3 — Amazon Location API key

Current key (from `lib/floodlens-stack.ts`) [V]:

| Property | Value | Assessment |
|---|---|---|
| Allowed actions | `geo-maps:GetStyleDescriptor`, `GetTile`, `GetSprites`, `GetGlyphs` | least privilege; static maps excluded |
| Allowed resource | `arn:aws:geo-maps:ap-south-1::provider/default` | only maps, only this region |
| Referers | `https://<distribution domain>/*` (+ `extraReferers`) | browser-enforced; **bypassable by non-browser clients** |
| Expiry | none unless `-c locationKeyExpireTime=…` | **recommend setting one** |
| Value in repo/template/bundle | none (scan + template test) | good |

* **Is `/config.json` exposure expected?** Yes [V]. The browser must send the key with every style/tile request, so anyone can read it from network traffic regardless of where it is stored; AWS also documents "Show API key"/`DescribeKey` as the way to obtain it. Treat it as a **public identifier with a spend risk**, not a secret. Keeping it out of Git and the JS bundle is hygiene, not protection.
* **Expiry and rotation** [V: AWS best practices]: "Use key expiration to enforce regular rotation. Create a new key before the old one expires, update your application, then deactivate the old key"; a deactivated key can be deleted after 90 days. Recommend an expiry of ≤ 90 days for the pilot. Replacing the key in this stack means a new key name plus redeploy so `config.json` is rewritten [A — not exercised].
* **Quotas** [V]: `GetTiles` default 2000 (rate; unit not stated in the text I read), adjustable up to 4000. A rate limit, so it does **not** cap spend.
* **Monitoring** [V]: CloudWatch `AWS/Location`, `CallCount` (Sum), dimension `ApiKeyName` combined with `OperationName`/`ResourceName`. Exact tile dimensions unknown before traffic [U] (F-08).
* **Separate dev key** [V: AWS best practice "use separate credentials per application/environment"]: create a key limited to the local dev origin by hand; never reuse the production key locally.

Protection against misuse and unexpected billing (owner actions — none created here):

1. AWS Budgets on the account **and** one filtered to Amazon Location Service, with forecast and actual alerts (COST_MODEL.md §6).
2. AWS Cost Anomaly Detection monitor on services [A].
3. Per-key `CallCount` alarm once real dimensions are known.
4. A written kill-switch: expire or delete the key (the frontend then shows the "Basemap unavailable" banner and keeps all data layers working — verified in Phase 4 with a bad key).
5. Short expiry (≤ 90 days).

## 5. Task 4 — Cost verification

See [COST_MODEL.md](COST_MODEL.md). Summary: map tiles are ≈ 94 % of the pilot bill. Base case ≈ **$8.5 / $43 / $213 per month** at
2,000 / 10,000 / 50,000 sessions (list price, no free tier); first-3-months with the Location free tier ≈ $0.5 / $23 / $193.
Every unit price is read from the AWS Price List bulk files [V]; session behaviour is an assumption [A].

## 6. Task 5 — Lambda package review

| Check | Result |
|---|---|
| Package size | 98.6 MiB unzipped (1,072 files), ≈ 16 MB zipped [V, measured] vs 250 MB / 50 MB limits [V] |
| Breakdown | artifacts 93.9 MB, `pydantic_core` 4.7 MB, `pydantic` 1.9 MB, `fastapi` 1.2 MB [V] |
| Runtime / arch | `python3.13` supported, deprecation 2029-06-30; all runtimes support x86_64 and arm64 [V: Lambda runtimes page]. `pydantic_core` wheel `cp313-manylinux2014_aarch64` [V] |
| Dependencies | 12 pure-Python or native wheels, all pinned now; versions identical to the dev venv the tests ran against [V]. No rasterio, GeoPandas, GDAL or numpy (packager refuses them) [V] |
| Packaged code vs source | identical after rebuild [V] (the previous build was stale by two `# noqa` comments) |
| Memory | peak RSS 156 MiB after exercising every endpoint kind vs 1,024 MB configured [V: local, x86 Windows, a proxy] — headroom is for CPU, not memory |
| Cold start | local import 1.9 s [V: Windows x86; not an AWS measurement]; real figure [U] until deployed. Artifacts load lazily per request and are cached; first heavy request 100–290 ms locally [V]. Provisioned concurrency is not justified [A] |
| Timeout | 15 s vs measured worst 0.29 s [V locally]; HTTP API integration limit is 30 s [A] |
| Largest response | 2.09 MB uncompressed (polygons, baseline/50mm) [V]; ≈ 0.42 MB gzip. Lambda's synchronous limit is 6 MB [V] → ~2.9× headroom. FastAPI sends uncompressed JSON; CloudFront compresses |
| Artifacts bundled vs S3 | no measurable reason to switch: well inside every limit, no S3 permissions needed on the function. Revisit if artifacts approach ~200 MB or a single response approaches ~4 MB [A thresholds] |

Package validation run locally: `scripts/package_lambda.py` (rebuilt, exit 0), diff against source, wheel-tag check, forbidden-package check.

## 7. Task 6 — CloudFront and CSP readiness

Reviewed against the synthesized template, the built `index.html` and the API. **Nothing here has run on CloudFront.**

| Area | Observation | Status |
|---|---|---|
| `/config.json` | own behaviour, S3 origin, `CACHING_DISABLED`, `Cache-Control: no-store` set at upload | [V template] — runtime [U] |
| `/v1/*` | HTTP API origin, GET/HEAD, 5-min default TTL (0 s min, 1 h max), query string allow-list | [V template] |
| Cache of errors | API sends no `Cache-Control`. CloudFront's default error-caching TTL (10 s, recalled) may cache 404/422/500 briefly | [A] — check |
| CSP | `default-src 'self'`; `script-src 'self'`; `style-src 'self' 'unsafe-inline'`; `connect-src 'self' https://maps.geo.ap-south-1.amazonaws.com`; `worker-src 'self' blob:`; `img-src 'self' data: blob:` | built `index.html` has **no inline script** and no external host except the maps host [V] |
| Style inline | `'unsafe-inline'` for styles is needed by MapLibre/React inline styles [A]; it is the weakest directive | LOW |
| MIME types | `.js`, `.css`, `.html`, `.json` inferred by the deployer from extension [A] | verify |
| CORS | none: app and API share one origin; direct cross-origin browser calls to the API are not possible (no CORS headers) [V locally] | OK |
| Error responses | no custom error pages by design so API 404/422 are not masked [V] | OK |
| SPA fallback | CloudFront Function on the **default behaviour only**: URIs without a dot become `/index.html` [V]; missing assets give S3 XML 403 | LOW |
| Compression | `compress: true` on all behaviours; gzip+brotli in the API cache policy; `image/tiff` is not compressible (response is ~1.4 KB) [V] | OK |
| OAC | one OAC shared by the default and `/config.json` behaviours; bucket policy allows `s3:GetObject` to `cloudfront.amazonaws.com` conditioned on this distribution's ARN; no public access [V template] | OK |
| `HEAD` | API returns 405 (F-09) | owner decision |

Post-deployment checks: [POST_DEPLOYMENT_CHECKLIST.md](POST_DEPLOYMENT_CHECKLIST.md).

## 8. Task 7 — CDK and deployment safety (synthesized template, 39 resources + metadata)

| Item | Result |
|---|---|
| Account/region guard | `bin/floodlens.ts` aborts for a different account or region [V: tested both ways — exit 1 for 111111111111 and for eu-west-1, exit 0 for 675613597178/ap-south-1]. **Inert offline** (account unknown); effective at deploy because the CLI sets `CDK_DEFAULT_ACCOUNT` from the credentials (F-10) |
| Least privilege | API role = `AWSLambdaBasicExecutionRole` only. Key reader: `geo:DescribeKey` on the one key ARN. Deployer: S3 on the CDK asset bucket and site bucket, `cloudfront:CreateInvalidation/GetInvalidation` on `*` (CDK default, F-16). No `*` actions, no IAM users/keys [V] |
| Retention | site bucket `Retain`/`Retain`; API function and access log groups `Retain`; versioned, 30-day noncurrent expiry. Helper log groups `Delete` by design [V] |
| S3 | all four public-access blocks on, SSE-S3, TLS-only deny, ownership enforced, access only via OAC [V] |
| Lambda role | one role, basic logging only, no S3/Location/secrets access, env contains only `FLOODLENS_ARTIFACT_DIR` [V] |
| API throttle | 50 rps / 100 burst default, configurable (§3); access logs on [V] |
| SNS / alarms | 1 topic, 4 alarms (Lambda errors, throttles, p95 > 10 s; API 5xx ≥ 5); **subscription only if `alarmEmail` given** (F-04); email subscriptions need the recipient to confirm [A] |
| Log retention | 30 days on all five groups [V] |
| Recurring cost | no NAT, VPC, RDS, WAF, Secrets Manager, KMS CMK, reserved or provisioned capacity [V]. Standing costs: 4 alarms and 1 dashboard (inside free allowances [V: price list `Global` rows]), S3 and log storage pennies |
| Secrets | none in template, source or bundle (scan clean) [V] |
| Destructive/billing resources | none; test asserts no Budgets/Billing/Organizations/IAM users or keys [V] |

## 9. Task 8 — Final validation (all executed this session)

| Check | Result |
|---|---|
| Geospatial tests | **174 passed** |
| API tests | **132 passed** (131 + 1 new deployment-contract test) |
| Frontend tests | **83 passed** (8 files) |
| Infrastructure tests | **20 passed** (17 + 3 new) |
| Ruff | **All checks passed** (after fixing 11 findings in files I added) |
| TypeScript | `tsc --noEmit` clean for `apps/web` and `infrastructure` |
| Production build | succeeded (33.8 s); bundle contains no map key |
| Schema drift | "schemas match models" |
| Manifest validation | `validate_data.py` exit 0, all datasets ok |
| Offline `cdk synth` | succeeds with credential/config files pointed at nothing; 40 template entries |
| Secret scan | clean (only a fake test constant and a test regex) |
| Protected data | no file under `geospatial`, `data/raw`, `data/processed`, `data/manifests` changed in the last 120 minutes except `geospatial/.pytest_cache` (a test cache); manifest checksums validate [V] |
| `randomError` | appears only in `geospatial/floodlens_geo/ingestion/imerg.py` (ingestion); absent from API, frontend, infrastructure and scoring [V] |

Not run, by design: `cdk bootstrap`, `cdk deploy`, `cdk destroy`, any AWS API call that mutates anything, any upload.
Read-only network use: AWS documentation pages and the public AWS Price List bulk files (streamed, not saved); PyPI
wheels for the Lambda package build.

## 10. Exact deployment prerequisites

1. **Resolve F-01**: confirm where Phase 4 is committed, or commit and re-run `scripts/release_integrity.py --baseline <rev>`; expect exit 0.
2. **Decide** F-02 (accept public execute-api for the pilot, lowering the throttle, or move to a custom domain / Function URL + OAC) and F-09 (HEAD).
3. **Create budgets and alerts yourself** (COST_MODEL.md §6): account budget, Amazon Location budget, anomaly monitor.
4. **Check Lambda quotas** (Service Quotas, read-only) before deciding on `reservedConcurrency` (F-05).
5. Choose deploy-time context: `alarmEmail` (required in practice, F-04), `locationKeyExpireTime` (≤ 90 days), `apiRateLimit`/`apiBurstLimit`.
6. Build immediately before deploying: `python scripts/package_lambda.py`, `npm run build`, then `cdk synth` and read the template. Do not run `cdk diff`: it publishes the template to the staging bucket (DEPLOYMENT_RUNBOOK.md section 8).
7. Bootstrap needs to be approved separately (it creates the CDK toolkit resources in the account).
8. After deployment, work through POST_DEPLOYMENT_CHECKLIST.md and create the per-key usage alarm.

## 11. Remaining risks

* Map-tile spend cannot be capped (F-03); mitigation is detection plus a manual kill-switch.
* The first execution of the packaged Lambda and of the CloudFront/CSP path will be in AWS (F-06, F-07).
* Direct API access remains possible (F-02).
* Data refresh requires rebuild and redeploy.
* Pilot assumptions in COST_MODEL.md are estimates; real analytics will move them.

---
**Phase 6 update (2026-10-30):** a commit now exists (`d2c67a2`, single root commit, equals `origin/main`), so F-01 is superseded by P6-01 in [RELEASE_READINESS.md](RELEASE_READINESS.md): the baseline is the first verified one, not a historical one. The three response hashes in `release-integrity.json` that contain `data_freshness.as_of_utc` were never reproducible (script fixed in Phase 6). F-02, F-03 and F-09 are analysed further in [SECURITY_OPERATIONS.md](SECURITY_OPERATIONS.md).
