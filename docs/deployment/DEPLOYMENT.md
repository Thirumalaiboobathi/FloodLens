# FloodLens deployment architecture (ap-south-1) — prepared, NOT deployed

Status: CDK code is written, unit-tested and `cdk synth` succeeds. **Nothing has been
deployed, no AWS resource exists, and no data has been uploaded.** Deployment needs explicit approval
(account 675613597178, region ap-south-1). The stack refuses to synthesize for any other account or region
*when the CDK knows the account* (see Phase 5 review).

> **Phase 5 corrections.** (1) Earlier text said synth works "with no credentials". On a machine that has a default
> AWS profile the CLI silently used it for a read-only identity lookup, so that was not a credential-free test.
> Truly offline synth is `AWS_SHARED_CREDENTIALS_FILE=/nonexistent AWS_CONFIG_FILE=/nonexistent AWS_DEFAULT_REGION=ap-south-1 npx cdk synth`;
> it works and leaves the account as `unknown-account`, so the account guard only acts at deploy time.
> (2) The Amazon Location tile price **is** published: $0.04 per 1,000 tiles in Mumbai (Price List 2026-09-11); the
> "$0.40" figure and the "$160" upper bound below were wrong. See [COST_MODEL.md](COST_MODEL.md) for the current model
> and [PRE_DEPLOYMENT_REVIEW.md](PRE_DEPLOYMENT_REVIEW.md) for findings.

## 1. Architecture

```
Browser ──HTTPS──> CloudFront (default *.cloudfront.net certificate)
                     │  default behaviour     : private S3 bucket via Origin Access Control (React build)
                     │  /config.json (no cache): same bucket; written at deploy time (basemap key + style)
                     │  /v1/*  (5 min cache)   : HTTP API ──> Lambda (FastAPI + Mangum, python3.13, arm64)
                     └─ response headers: HSTS, nosniff, frame DENY, referrer policy, CSP
Browser ──HTTPS──> maps.geo.ap-south-1.amazonaws.com   (Amazon Location Maps v2, restricted API key)
Offline (laptop / processing environment, not AWS): ingestion, terrain, exposure, rainfall, artifact build
```

* **One origin for app and API**, so no CORS and no cross-origin credentials. CloudFront routes `/v1/*` to the API.
* **Raster and geospatial processing stay offline.** The API Lambda contains only `floodlens_api`, FastAPI,
  Pydantic and Mangum, plus the prebuilt artifact tree `data/api/<region>`. No rasterio, GeoPandas, GDAL or numpy
  is packaged (`scripts/package_lambda.py` fails the build if one appears).
* **No Docker.** `scripts/package_lambda.py` cross-installs Linux/arm64 wheels with `pip --platform`.
* **SPA fallback is a CloudFront Function on the default behaviour only.** Distribution-wide custom error
  pages were rejected on purpose: they would turn API `404`/`422` JSON into the HTML app shell.

### Why artifacts are bundled in the Lambda package rather than read from S3 (decision, with numbers)

| Measure | Value | Limit |
|---|---|---|
| Dependencies (arm64 wheels, py3.13) | 16 MB unzipped, 3.1 MB zipped | |
| Artifacts `data/api/chennai` | 91 MB raw (roads 78 MB in 600 tiles, polygons 9.8 MB, water 1 MB), 13 MB zipped | |
| **Package total** | **98.6 MiB unzipped** (re-measured in Phase 5; ≈ 16 MB zipped) | 250 MB unzipped, 50 MB zipped direct upload |
| Largest single response (`polygons?limit=20000`) | 2.2 MB | 6 MB Lambda response, 10 MB HTTP API |
| Largest road tile file | 0.94 MB | |

Bundling means no per-request S3 reads, no S3 IAM on the API role, atomic code+data releases, and the same
files the tests ran against. The cost is that new data needs a redeploy (minutes, CDK-managed). Move to an S3
read-through cache (`FLOODLENS_ARTIFACT_BUCKET`, files copied to `/tmp`) only if the artifact tree approaches
~200 MB, more regions are added, or rainfall ingestion becomes frequent. That is a future change and is not built.

Lambda memory is 1024 MB (not 512) because the store keeps up to 64 parsed JSON files in an LRU cache; large
road tiles parse to several MB of Python objects each. Timeout 15 s.

## 2. Resource inventory (39 CloudFormation resources + CDK metadata in the Phase 5 template; the 36 below plus three explicit 30-day log groups for the deploy-time helper functions)

| Service | Resources |
|---|---|
| S3 | 1 bucket (private, block-public-all, SSE-S3, versioned, TLS-only policy, `RETAIN`), 1 bucket policy (CloudFront OAC read only) |
| CloudFront | 1 distribution (PriceClass_200, HTTP/2+3, IPv6), 1 OAC, 1 function (SPA rewrite), 1 cache policy, 1 response headers policy |
| API Gateway | 1 HTTP API, 1 `$default` stage (throttle 50 rps / burst 100, access logs), 1 integration, 2 routes (`GET`/`HEAD /v1/{proxy+}`) |
| Lambda | 1 API function (python3.13, arm64, 1024 MB, 15 s), 1 CDK custom-resource function (`ReadMapsKey`), 1 CDK bucket-deployment function + CLI layer, 2 permissions |
| Amazon Location | 1 API key (`geo-maps:GetStyleDescriptor/GetTile/GetSprites/GetGlyphs` on `arn:aws:geo-maps:ap-south-1::provider/default`, referrer = the CloudFront domain, optional expiry) |
| IAM | 3 roles + 2 policies, all CDK-generated (see §5) |
| CloudWatch | 2 log groups (30 days, `RETAIN`), 4 alarms (Lambda errors, throttles, p95 > 10 s; API 5xx ≥ 5), 1 dashboard |
| SNS | 1 topic; an e-mail subscription only if `-c alarmEmail=` is passed |
| Custom resources | 2 bucket deployments (site, `config.json`; `prune: false`), 1 `describeKey` call |

Also required once, **at deploy time**: `cdk bootstrap` creates the `CDKToolkit` stack (asset bucket, roles) in the account.

## 3. Estimated monthly cost (pilot) — read the caveats

Assumptions: 2,000 map sessions/month, ~60 API calls/session before caching (≈ 120 k requests), ~200 map tiles/session
(≈ 400 k tiles), < 5 GB transfer. Prices are approximate and **must be re-checked in the AWS Pricing Calculator**.

| Item | Estimate | Note |
|---|---|---|
| Lambda (arm64, 1 GB, ~150 ms) | ≈ $0 | ~18 k GB-s and 120 k requests are inside the always-free allowance |
| API Gateway HTTP API | ≈ $0.15 | ≈ $1 per million requests; CloudFront caching reduces calls |
| CloudFront | ≈ $0 | inside the always-free 1 TB / 10 M requests |
| S3 + CloudWatch logs/alarms/dashboard | < $1 | 4 alarms and 1 dashboard fall inside free allowances |
| **Amazon Location tiles** | **$0 for the first 3 months, then ≈ $16** at 400 k tiles/month | 500 k tile requests/month free for 3 months. **$0.04 per 1,000 tiles in ap-south-1** (AWS Price List, `APS3-Usage-Default-Maps-Tiles`, `GetTile`). Style, sprite and glyph requests are free (AWS docs). *(Superseded by COST_MODEL.md; the earlier "$16–$160" bracket was wrong above $16.)* |
| **Total without maps** | **≈ $1 or less** | maps dominate and scale with sessions |

Not included, and not configured here (billing changes were out of scope): AWS Budgets/cost alarms, a custom domain
and ACM certificate, WAF (≈ $5+/month), CloudFront access logging.

## 4. Amazon Location basemap

* **Availability, verified** from AWS documentation: Maps v2 is offered in `ap-south-1` (listed among regions in the
  official MapLibre sample) with styles `Standard`, `Monochrome`, `Hybrid`, `Satellite`. A live, anonymous GET of
  `https://maps.geo.ap-south-1.amazonaws.com/v2/styles/Monochrome/descriptor` returned `200` (no AWS resource or credential involved).
* **Style:** `Monochrome` (Amazon describes it as a minimalist canvas for data-visualisation overlays), `color-scheme=Light`,
  `political-view=IND` (India's view of Gilgit-Baltistan; AWS-documented option). Change with `mapStyle` context or `config.json`.
* **Authentication:** an Amazon Location *API key* (`v1.public.…`). Chosen over Cognito identity pools because it needs no identity
  pool, no SigV4 signing in the browser and no runtime credentials; it is restricted to four map actions on one resource and to the
  site's referrer. It is **not** an AWS access key. Cognito remains the alternative if per-user quotas or non-referrer controls are needed.
* **Delivery:** CloudFormation does not return an API key's value. The stack reads it back with a `describeKey` custom resource
  (`geo:DescribeKey` on that one key ARN only, response data hidden from logs) and writes it to `config.json` in the private bucket.
  The browser loads `/config.json` at start-up (`no-store`). The key is therefore never in git, the CDK source, the template or the JS bundle.
* **Local development:** no key needed (blank background, everything else works). To use the basemap locally, create a *separate*
  key restricted to `http://localhost:5173/*` and put it in an untracked `apps/web/.env.local` (see `apps/web/.env.example`).
* **Failure handling:** the style descriptor is fetched first with an 8 s timeout. Failure (bad URL/network) or later tile errors
  (note: AWS serves the descriptor *without* validating the key, so a bad key only shows up as tile errors) show a banner
  "Basemap unavailable" and the data layers keep working on a plain background. The key/URL is never shown in the UI or errors.
* **Readability:** data overlays are translucent (rainfall 40 %, exposure 55 %) over a grey basemap; legend and attribution are always shown.

### Environment variables (frontend; none are secrets except the key, which must stay out of git)

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_BASE` | empty (same origin) | API base; leave empty in production |
| `VITE_MAP_STYLE_URL` | unset | Full style URL; wins over the Location variables (https only, or http://localhost) |
| `VITE_LOCATION_API_KEY` | unset | Amazon Location browser key (dev only; production uses `config.json`) |
| `VITE_LOCATION_REGION` | `ap-south-1` | Maps endpoint region |
| `VITE_LOCATION_STYLE` | `Monochrome` | `Standard`/`Monochrome`/`Hybrid`/`Satellite` |
| `VITE_LOCATION_COLOR_SCHEME` | `Light` | `Light`/`Dark` (Standard and Monochrome) |
| `VITE_LOCATION_POLITICAL_VIEW` | `IND` | ISO country code; empty disables |

CDK context (`-c name=value`): `alarmEmail`, `locationKeyExpireTime` (ISO-8601; default no expiry), `mapStyle`, `extraReferers` (comma list), `webDistDir`, `lambdaDir`.

## 5. Security review

| Area | State |
|---|---|
| Public S3 | None. Block-public-access all four, TLS-only deny, OAC-scoped `s3:GetObject` to this distribution only. Test asserts no `*` principal allow. |
| Credentials | No access keys, secrets, tokens or EARTHDATA values anywhere in source, template or bundle (tests scan the template). No IAM users or access keys are created. |
| API Lambda role | Logging only (`AWSLambdaBasicExecutionRole`). No S3, no other service. Env holds only the artifact path. |
| CDK-managed roles | Bucket-deployment role has `s3:DeleteObject*` on the site bucket and `cloudfront:CreateInvalidation` on `*` (CDK defaults, deploy-time only; `prune: false`). `ReadMapsKey` role: `geo:DescribeKey` on one key. |
| API exposure | Read-only: only `GET`/`HEAD /v1/**`. Inputs validated (bbox ranges, 2° span cap, enums, id patterns, paged limits); internals never leak (500 body is generic). |
| **Direct API access** | The `execute-api` URL is public and bypasses CloudFront. Acceptable for read-only public data; mitigated by stage throttling. HTTP APIs cannot take WAF. Reserved Lambda concurrency was **not** set because a new account's unreserved-concurrency floor (10) can make it fail; check quotas, then set it. |
| Map key abuse | Referrer restriction stops other browsers' sites, but anyone can read `/config.json` and call tiles with a forged `Referer`, which would bill the account. Mitigate: expiry (`locationKeyExpireTime`), a billing/usage alarm (not configured here), WAF/rate limit on a custom domain, or move to Cognito. |
| TLS | HSTS 1 year. **Minimum TLS 1.2 is not enforced**: CloudFront ignores it on the default `*.cloudfront.net` certificate; it needs a custom domain + ACM certificate in us-east-1. |
| CSP | `default-src 'self'`; scripts `'self'` only; `style-src 'unsafe-inline'` (MapLibre and React inline styles); connect/font to the Amazon Location maps host only; `frame-ancestors 'none'`. Not yet exercised in a real CloudFront deployment. |
| Data licensing | ODbL attribution and licence travel with every water response and provenance document; the UI attribution names OSM, NASA GES DISC and Copernicus DEM. |
| Logging | Lambda and API access logs 30 days. CloudFront access logs are off (needs a logging bucket). No request bodies or keys are logged. |
| Destructive changes | Bucket and log groups `RETAIN`; no pruning; no `autoDeleteObjects`; tests assert no billing/Organizations/IAM-user resources. The custom-resource *records* are `Delete` (they hold no data). |

## 6. Runbook (not executed; every step needs approval)

```
# 1. offline, no AWS
.venv/Scripts/python.exe scripts/build_api_artifacts.py --region chennai
(cd apps/web && npm run build)
.venv/Scripts/python.exe scripts/package_lambda.py
(cd infrastructure && npm test && npx cdk synth --quiet)

# 2. needs approval; run with the intended profile, account 675613597178
(cd infrastructure && npx cdk bootstrap aws://675613597178/ap-south-1)     # creates CDKToolkit stack
(cd infrastructure && npx cdk diff)                                         # review
(cd infrastructure && npx cdk deploy -c alarmEmail=you@example.org)         # creates the stack
```

Smoke test after an approved deploy: `GET <SiteUrl>/v1/health`, `GET <SiteUrl>/v1/regions/chennai/rainfall`, open the site, confirm the
Monochrome basemap and layers load. Rollback: `cdk deploy` the previous commit; the bucket and log groups are retained by design.

## 7. Known limitations

* Not deployed or tested in AWS; CloudFront behaviours, CSP and the `describeKey`/`config.json` path are validated only by template tests and synth.
* TLS 1.2 minimum, WAF, access logging, cost alarms and a custom domain are not configured.
* Map-tile spend has no hard cap (see PRE_DEPLOYMENT_REVIEW.md, finding F-03); budgets and alarms must be created by the owner.
* Data refresh requires re-packaging and redeploying the Lambda.
