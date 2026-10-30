# Migration to AWS Amplify Hosting + API Gateway HTTP API

Written for: the project owner and whoever deploys FloodLens. 2026-10-30 (Phase 13). **Local implementation only. Nothing was deployed, created, deleted or committed.** Labels: **[V]** verified here (tests, synth, read-only AWS call) · **[A]** assumption · **[U]** not verified until deployment.

## 1. Why

The first deployment of the CloudFront design failed at creation: the API cache policy listed 15 query strings and the account limit "Query strings per cache policy" is 10 (code `L-50C9D326`). The stack `FloodLens` is in `ROLLBACK_COMPLETE` (see `reports/releases/2026-10-30-rc2/ROLLBACK-INVESTIGATION.md`). Rather than raise a CloudFront quota and keep a 37-resource hand-built edge stack, the frontend moves to Amplify Hosting and the API to an API Gateway HTTP API. There is no cache policy, so the quota is no longer a dependency.

## 2. Old and new architecture

| | Old (rc2, never deployed) | New |
|---|---|---|
| Frontend | private S3 bucket, CloudFront distribution, S3 OAC, viewer function for SPA routes, response-headers policy, bucket deployments | **Amplify Hosting**, built from GitHub `main` by `amplify.yml` |
| API entry | CloudFront `/v1/*` → Lambda Function URL (`AWS_IAM`, Lambda OAC) | **API Gateway HTTP API** `GET`/`HEAD /v1/{proxy+}` → Lambda (AWS_PROXY, payload 2.0), called cross-origin |
| API cache | CloudFront cache policy (15 query strings) | none (every request invokes Lambda) |
| Runtime config | `config.json` written by two CDK bucket deployments + a custom resource that read the map key | Amplify environment variables set by the owner |
| Browser→API | same origin, no CORS | cross origin: API Gateway CORS allow-list |
| Stack | `FloodLens` (failed, `ROLLBACK_COMPLETE`) | `FloodLensAmplify` (21 resources) |

```
Browser ──HTTPS──> Amplify Hosting (static React app, SPA rewrite)
   │ GET/HEAD https://<apiId>.execute-api.ap-south-1.amazonaws.com/v1/...   (CORS: the Amplify origin only)
   ├──────────────> API Gateway HTTP API ($default stage, throttled, JSON access logs) ──AWS_PROXY 2.0──> Lambda (FastAPI + Mangum, arm64)
   └─ tiles/style ─> maps.geo.ap-south-1.amazonaws.com (restricted browser key; bypasses everything above)
```

Removed from the future stack: CloudFront distribution, both OACs, the API cache policy, the SPA viewer function, the response-headers policy, the site S3 bucket and its policy, Function URL and its two permissions, both `BucketDeployment`s, the key-reader custom resource and the three CDK helper Lambdas/layer. **Kept:** the API Lambda (unchanged configuration), its retained log group, the Location key, the SNS topic/subscription, the four Lambda alarms, the dashboard. **Added:** HTTP API, route/integration/stage, access-log group, two route permissions, a 5xx alarm.

## 3. API Gateway routing and behaviour [V by template tests, U in AWS]

* Routes: `GET /v1/{proxy+}` and `HEAD /v1/{proxy+}` only. `/docs`, `/openapi.json`, the bare `/v1` and every other path return the gateway's 404 and never reach Lambda. The application has no bare `/v1` route either, so nothing is lost.
* Integration: `AWS_PROXY`, payload format `2.0`, timeout 20 s (Lambda timeout 15 s; HTTP API maximum 30 s). No parameter mapping, no authorizer: the query string reaches FastAPI through `rawQueryString`, so all 15 parameters (`bbox boundary end facility_type feature_type ids kind limit min_class offset scenario start stream water_class zoom`) pass unchanged. 55 tests drive each parameter through a synthetic HTTP API event.
* Stage: `$default` (no `/stage` prefix, so paths equal the old ones), auto-deploy, throttle **10 requests/s sustained, burst 20** (`-c apiRateLimit`, `-c apiBurstLimit`). This is a pilot figure [A]: it sits under the shared Lambda pool of 10 for typical sub-second calls but heavy responses (about 1 s) can still saturate it.
* Access logs (30 days, retained on delete): request id, time, method, route, path, status, protocol, response length, latencies, integration status, error message. **No client IP, user agent, headers or query string.**
* CORS: allow-origins = the Amplify origin(s) in `-c frontendOrigins`; methods `GET, HEAD, OPTIONS`; headers `accept, content-type, x-request-id`; exposed `x-request-id, x-floodlens-schema-version, x-content-sha256, content-length`; no credentials; max-age 1 day. The localhost origin is added only with `-c allowLocalDevCors=true`. API Gateway answers `OPTIONS`; the application adds no CORS headers.
* Lambda permission: `lambda:InvokeFunction` for `apigateway.amazonaws.com`, `SourceArn` = this API id, `/*/*/v1/{proxy+}` (CDK creates one per route). No public principal. The execute-api endpoint stays enabled because there is no custom domain; the old "no execute-api" rule applied only to the CloudFront design.
* Responses are **not compressed** (HTTP APIs do not compress; CloudFront did). See section 11.

## 4. Lambda

Unchanged: Python 3.13, arm64, 1,024 MB, 15 s, handler `floodlens_api.lambda_handler.handler`, `FLOODLENS_ARTIFACT_DIR=/var/task/data/api`, logs 30 days (retained). Package rebuilt: 98.6 MiB unzipped (limit 250 MiB), 1,040 files, 630 data files identical to `data/api`, one native module (`pydantic_core` aarch64, ELF e_machine 183), no NumPy/GeoPandas/rasterio/shapely/pyproj/pandas [V]. Only the handler docstring changed. **No reserved concurrency** (owner decision; the stack refuses `-c reservedConcurrency`). The account pool is 10 and the increase request (case 179336288300281) is still `CASE_OPENED`. FloodLens shares that pool with ten other Lambda functions: burst traffic can throttle them and they can throttle FloodLens (HTTP 429/5xx, `LambdaThrottles` alarm). **ARM64 runtime compatibility is NOT VERIFIED until a real invocation succeeds.**

## 5. Amazon Location map key

The key is created by the stack: actions `geo-maps:GetStyleDescriptor, GetTile, GetSprites, GetGlyphs`, resource `geo-maps:ap-south-1::provider/default`, referrers = each frontend origin + `/*`, mandatory expiry (≤ 90 days; plan 85, reminder at day 75), name `floodlens-web-maps-<8 hex>`.

**Delivery decision: an Amplify environment variable, not a runtime endpoint.** CloudFormation never returns a key's value. A public config endpoint would need a custom resource plus an API route, a Lambda permission to describe keys and a new response contract; the old `config.json` path needed two bucket deployments and three helper Lambdas. The owner instead reads the value once and sets `VITE_LOCATION_API_KEY` in Amplify:

```
aws location describe-key --key-name <MapsApiKeyName output> --region ap-south-1 --query Key --output text
```

Do not paste the value into chat, a ticket, Git or `amplify.yml`. It will be visible in the built JS and in the style request to every visitor: **it is a public browser key, not a secret.** The referrer restriction is enforced by Amazon Location only for browser-sent `Referer`; any non-browser client can forge it, so expiry, action scope and budgets are the real limits. Emergency restriction or revocation: `docs/deployment/SECURITY_OPERATIONS.md` 2.5. Rotation: redeploy with a new expiry, update the Amplify variable, rebuild. The app still reads an optional `/config.json` (404 on Amplify is ignored); style, India political view (`IND`), overlays, legend and failure handling are unchanged.

## 6. Amplify frontend

* `amplify.yml` (repo root, monorepo form): `appRoot: apps/web`, `npm ci`, `npm run build` (`tsc --noEmit && vite build`), artifacts `dist`, cache `node_modules`. It contains no secrets and no API routing. In the console set `AMPLIFY_MONOREPO_APP_ROOT=apps/web`. Build uses `nvm install 20 && nvm use 20` [U: confirm on the first build].
* SPA rewrite: `apps/web/amplify-rewrites.json` (one rule, `index.html`, status 200, static assets excluded; tested). Rewrites are set in the console (App settings → Rewrites and redirects → Open text editor, paste the JSON). **Never route `/v1` through Amplify**; the browser calls API Gateway directly.
* Headers: `apps/web/customHttp.yml` (HSTS, nosniff, DENY framing, referrer policy and a CSP whose `connect-src` is `'self'`, `https://*.execute-api.ap-south-1.amazonaws.com` and `https://maps.geo.ap-south-1.amazonaws.com`). If the console does not show them after the first build, paste the file under Custom headers [U]. The `execute-api` wildcard is wider than the single API host; tighten it to the exact host once known.
* Environment variables (Amplify console → Environment variables; all `VITE_*` are public): `VITE_API_BASE_URL` (= output `ApiBaseUrl`, https origin, no path, no trailing slash), `VITE_LOCATION_API_KEY`, `VITE_LOCATION_REGION=ap-south-1`, `VITE_LOCATION_STYLE=Monochrome`, `VITE_LOCATION_POLITICAL_VIEW=IND`. A production build without `VITE_API_BASE_URL` shows "The FloodLens API address is not configured for this build" instead of calling the static host. Local development leaves it unset (Vite proxies `/v1`).

## 7. Deployment sequence (needs the owner's approval; none of it has been run)

Because the Amplify domain is needed for CORS and the key referrer, and the key and API URL are needed for the Amplify build:

1. **Resolve the old stack** (separate decision): `FloodLens` in `ROLLBACK_COMPLETE` cannot be updated. The new stack has a different name, so nothing collides, and deleting the old one is optional housekeeping (no resources remain; it is an owner-approved `delete-stack`). Leave the Phase 8 staging object alone.
2. **Commit and push** this migration (owner approval), then build a new release bundle from the clean commit.
3. **Create the Amplify app** in the console (Host web app → GitHub → this repository → branch `main`; app root `apps/web`). Authorize through the GitHub App flow; no token goes into source. Cancel or let the first build fail; the origin is then known: `https://main.<appId>.amplifyapp.com`.
4. **Deploy the backend:** `python scripts/package_lambda.py` then
   `cd infrastructure && npx cdk deploy FloodLensAmplify -c alarmEmail=<monitored email> -c mapsApiKeyArn=arn:aws:geo:ap-south-1:<account>:api-key/<name> (the key is created by hand; the stack does not create it) -c frontendOrigins=https://main.<appId>.amplifyapp.com`
   (keep CDK's IAM prompt; run it from a terminal). Never pass `reservedConcurrency`; never run `cdk diff`.
5. Read outputs `ApiBaseUrl`, `MapsApiKeyName`; read the key (section 5).
6. In Amplify set the five variables, the rewrite rule and (if needed) custom headers; start a build.
7. Confirm the SNS e-mail subscription.
8. Run the post-deployment checks (section 9).

## 8. Custom domain

Not configured. If a domain is added in Amplify (it provisions the certificate), add that origin to `-c frontendOrigins` (it becomes a CORS origin and a key referrer) and redeploy the backend; no CloudFront or ACM work is needed. A custom domain on the API is a separate option (an ACM certificate in ap-south-1 and a domain mapping on the HTTP API); the execute-api URL needs neither.

## 9. Monitoring and post-deployment checks [U: nothing has run in AWS]

Alarms to SNS (5-minute periods, missing data not breaching): Lambda Errors ≥ 1, Throttles ≥ 1, Duration p95 ≥ 10,000 ms, Invocations ≥ 3,000, API Gateway 5xx ≥ 1. Dashboard `FloodLensAmplify`. The 5xx metric dimension is the API id [U: confirm the alarm leaves INSUFFICIENT_DATA after traffic].

Checks, in order: (1) `GET <ApiBaseUrl>/v1/health` 200 with `x-floodlens-schema-version`; (2) **first ARM64 invocation**: read the cold-start `REPORT` line and look for `ImportModuleError`; stop and keep the evidence if one appears; (3) `OPTIONS` preflight from the Amplify origin returns the allow-origin header, and from another origin does not; (4) `POST`/`DELETE` return 404 or 405 at the gateway; (5) `/docs` and `/openapi.json` return 404; (6) each of the 15 parameters returns the same result as locally; (7) `HEAD` has status and headers, no body; (8) GeoTIFF bytes start `4949 2a00` and match `x-content-sha256`; (9) historical and recent rainfall, freshness and water overlay in the app; (10) tiles render under the restricted key and fail gracefully when blocked; (11) mobile and desktop layout; (12) SNS subscription not `PendingConfirmation`; (13) `aws lambda get-policy` shows only the API Gateway statements; (14) log groups and retention.

## 10. Rollback

* Failed stack create: CloudFormation rolls back; the API and access log groups are Retain (remove by hand if they were created); rename the Location key before retrying.
* Frontend problem: in Amplify redeploy the previous successful build (the backend is independent).
* Abnormal tile spend: restrict or revoke the key first (SECURITY_OPERATIONS 2.5).
* Traffic abuse: lower the stage throttle with a redeploy (`-c apiRateLimit`), or set the route throttle to zero in the console as an emergency stop (changes stack drift; record it).
* Do not `cdk destroy` casually; present a plan first.

## 11. Cost and limits (see COST_MODEL.md; prices below are NOT VERIFIED here)

* Map tiles remain the dominant, **uncapped** cost (about $8 / $40 / $200 per month at 2k / 10k / 50k sessions in the model). Budgets alert and do not cap. The $500 credits do not guarantee free operation.
* New lines [A/U, list prices to be confirmed]: Amplify build minutes and hosting (small for one app), API Gateway HTTP API requests (about a dollar per million), CloudWatch logs (two 30-day groups), Lambda (every request now invokes it; there is no edge cache).
* **Data transfer is the main change.** HTTP APIs do not compress and there is no cache, so API bytes leave uncompressed from the API and every repeat request invokes Lambda. The old model counted compressed, cached bytes. Expect API transfer to grow several times; at 10,000 sessions/month it could add roughly $5-10 [A]. Adding `GZipMiddleware` to FastAPI would remove most of it but changes the response encoding; it is not done here and is a recommended follow-up that needs an owner decision.
* Quotas read (read-only): API Gateway routes per HTTP API 300, throttle 10,000 rps account-wide; Amplify apps 25, build artifact 5 (units per console); Lambda concurrency 10 (increase pending); Location key requests 10/s. None blocks this design. The CloudFront query-string quota is no longer relevant.

## 12. Cleanup of the failed `FloodLens` stack (optional, owner-approved)

It holds no resources (rollback complete; no bucket or log group was ever created). `aws cloudformation delete-stack --stack-name FloodLens --region ap-south-1` removes only the empty record. It is not required, and the CDK assets bucket and the Phase 8 staging object must be left alone. Not run.
