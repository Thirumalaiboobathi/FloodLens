# FloodLens post-deployment checklist

**Status: NOT RUN.** The stack has not been deployed, so nothing below has been verified. Each line states what to run
and what a pass looks like; record the actual result next to it. Only use this after the owner has approved and run the deployment.

Set once (values come from the stack outputs `SiteUrl`, `ApiFunctionUrl`, `SiteBucketName`):

```bash
SITE=https://dxxxxxxxxxxxx.cloudfront.net      # SiteUrl
FNURL=https://xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.lambda-url.ap-south-1.on.aws   # ApiFunctionUrl (not a public entry point)
BUCKET=floodlens-sitebucket-...                 # SiteBucketName
B=$SITE/v1/regions/chennai
```

Never paste the Location key into a ticket, chat or log. Everything here is a read-only GET/HEAD unless marked **(changes state)**.

## A. Routing, headers, caching (CloudFront)

- [ ] `curl -sI $SITE/` → `200`, `content-type: text/html`, and all of: `strict-transport-security`, `x-content-type-options: nosniff`, `x-frame-options: DENY`, `referrer-policy`, `content-security-policy`.
- [ ] `curl -sI http://…cloudfront.net/` → `301/302` to https (viewer redirect).
- [ ] `curl -sI $SITE/some/deep/link` → `200 text/html` (SPA fallback via the CloudFront Function).
- [ ] `curl -sI $SITE/assets/does-not-exist.js` → `403` (S3 XML, expected, F-17) — **not** `200 text/html`.
- [ ] `curl -s $SITE/config.json` → JSON with only `VITE_LOCATION_API_KEY`, `VITE_LOCATION_REGION=ap-south-1`, `VITE_LOCATION_STYLE=Monochrome`, `VITE_LOCATION_POLITICAL_VIEW=IND`; header `content-type: application/json`; `cache-control: no-store`; `x-cache` is never `Hit from cloudfront` on two consecutive requests.
- [ ] `curl -sD- $SITE/v1/health` → `200` JSON and `x-floodlens-schema-version: 1.0.0`.
- [ ] Two identical `curl -sI` calls to `$B/rainfall` → second shows `x-cache: Hit from cloudfront` and an `age` header (5-minute default TTL).
- [ ] `curl -sI "$B/rainfall?junk=1"` → also a **Hit** (unknown query parameters are not in the cache key, F-15). If it is a Miss, the allow-list is not applied.
- [ ] A known-good parameter changes the key: `$B/water?bbox=80.2,13.0,80.3,13.1` vs `…80.2,13.0,80.3,13.2` are separate cache entries.
- [ ] `curl -s -o /dev/null -w "%{http_code} %{content_type}\n" "$B/water?bbox=70,5,90,25"` → `422 application/json` and body code `bbox_too_large` (an API error must not be replaced by `index.html`).
- [ ] `curl -s -w "%{http_code}\n" $B/nonexistent` → `404 application/json`.
- [ ] Error caching: repeat a 422 request twice and note whether the second is a CloudFront hit (default error-caching TTL is short; record the observed `age`) [A].
- [ ] `curl -sI $B/rainfall` → `200`, correct `content-type`, `content-length`, **no body** (HEAD is supported since Phase 7). `curl -sI $B/rainfall/<scenario_id>/raster` → `200 image/tiff`. Record whether `content-length` survives the Function URL and CloudFront [U]; a missing length is cosmetic.
- [ ] Compression: `curl -s -H 'accept-encoding: br,gzip' -D- -o /dev/null "$B/scenarios/rain_200mm/polygons?bbox=80.05,12.85,80.35,13.25&limit=5000"` → `content-encoding: br` or `gzip`; transfer ≈ 0.4 MB, not 1.7 MB.
- [ ] Raster: `curl -s $B/rainfall/<scenario_id>/raster | head -c 4 | xxd` → `4949 2a00` (`II*\0`, a TIFF), `content-type: image/tiff`.

## B. Origin access and public exposure

- [ ] S3 is private: `curl -s -o /dev/null -w "%{http_code}\n" https://$BUCKET.s3.ap-south-1.amazonaws.com/index.html` → `403`.
- [ ] `aws s3api get-public-access-block --bucket $BUCKET` → all four `true`; `aws s3api get-bucket-policy-status --bucket $BUCKET` → `IsPublic: false`.
- [ ] **Expected finding F-02**: `curl -s $API/v1/health` → `200` (the execute-api URL is public). Record it. `curl -s -o /dev/null -w "%{http_code}\n" $API/docs` → `404`.
- [ ] Optional **(small cost)**: ≈ 150 rapid `curl` requests to `$API/v1/health`; some `429` responses confirm the stage throttle (default 50 rps / 100 burst, or the value you set).
- [ ] `curl -v --tlsv1.0 --tls-max 1.0 $SITE/` — record whether TLS 1.0 is accepted (expected on the default certificate, F-18).

## C. Lambda

- [ ] CloudWatch Logs `/aws/lambda/…ApiFunction…`: the first invocation shows `Init Duration` and **no** `ImportError`/`Runtime.ImportModuleError` (F-06 — the first time the arm64 package runs). Record init duration and `Max Memory Used` (expect ≈ 150–300 MB of 1,024) and update COST_MODEL.md.
- [ ] `/v1/regions/chennai/scenarios/rain_200mm/polygons?bbox=80.05,12.85,80.35,13.25&limit=5000` → `200`, ≈ 1.7 MB uncompressed from the origin, well under the 6 MB limit.
- [ ] API access log group has JSON lines with `status`, `latencyMs`.
- [ ] No function error in the first hour: CloudWatch metric `Errors` = 0; `Throttles` = 0.

## D. Amazon Location key and basemap

- [ ] Open `$SITE` in Chrome with DevTools → Network: the style descriptor and tile requests go to `maps.geo.ap-south-1.amazonaws.com` and return `200`; the Monochrome basemap draws under the data layers.
- [ ] DevTools → Console: **no CSP violations** (`Refused to …`). If a directive blocks something legitimate, record which host/type before widening it.
- [ ] Block `maps.geo.ap-south-1.amazonaws.com` in DevTools and reload: the "Basemap unavailable" banner appears and the data layers still work.
- [ ] Restrictions: `aws location describe-key --key-name <name> --query 'Restrictions'` (do **not** print the whole response) → actions are exactly `geo-maps:GetStyleDescriptor/GetTile/GetSprites/GetGlyphs`, resource `arn:aws:geo-maps:ap-south-1::provider/default`, referer `https://<distribution domain>/*`; note the expiry.
- [ ] Referer behaviour (documents F-03): one tile request with no Referer should be refused; one with the correct Referer header from curl is accepted. Do not repeat it in a loop.
- [ ] `aws cloudwatch list-metrics --namespace AWS/Location` → read the real dimension set for tile calls, then create the per-key `CallCount` alarm (F-08).

## E. Application behaviour (real browser)

- [ ] Dashboard, Historical rainfall, Recent rainfall, Compare, Provenance panels load; Retry works after a blocked `/v1` request.
- [ ] Zoom to street level: roads, facilities and **water** layers load, each as a bounded request; the water layer shows a hint when zoomed out beyond 2°.
- [ ] Layer readability over the real basemap (rainfall opacity 0.4, water colour `#1f6f8b`); take screenshots and compare with Phase 4's.
- [ ] A real phone at ≈ 390 px width (Phase 4 only emulated it with an iframe).
- [ ] Rainfall is described as "rainfall accumulation" everywhere, never flood depth; nothing is labelled live or real-time.

## F. Alarms, budgets, hygiene

- [ ] The alarm e-mail subscription is **confirmed** (the recipient must click the link) — otherwise F-04 stays open.
- [ ] **(changes state, optional)** `aws cloudwatch set-alarm-state --alarm-name <LambdaErrors alarm> --state-value ALARM --state-reason test` → an e-mail arrives; the alarm returns to OK at the next evaluation.
- [ ] The owner has created the budgets in COST_MODEL.md §6 and they show the right scope.
- [ ] Log groups have 30-day retention (`aws logs describe-log-groups --query 'logGroups[].[logGroupName,retentionInDays]'`).
- [ ] `aws cloudformation describe-stack-resources --stack-name FloodLens` lists no NAT, VPC, WAF, KMS, Secrets Manager or reserved-capacity resource.
- [ ] `git status` is clean and the deployed commit is the one that passed Phase 5 validation.

## G. Record

Fill in the date, deploying person, commit, and the actual result of every item; attach screenshots. Anything that
fails goes into PRE_DEPLOYMENT_REVIEW.md as a new finding with a severity.

## H. Phase 7 gates: Function URL, OAC, first ARM64 invocation (changes nothing)

Section B's `execute-api` checks (F-02) no longer apply; use these.

- [ ] **Direct access is refused:** `curl -s -o /dev/null -w "%{http_code}\n" $FNURL/v1/health` → `403` (AuthType AWS_IAM, unsigned). A `200` means the Function URL is public: **stop and report as a BLOCKER**.
- [ ] Same for `$FNURL/v1/regions` → `403`.
- [ ] **CloudFront reaches the function:** `curl -s -o /dev/null -w "%{http_code}\n" $SITE/v1/health` → `200`. A `403` from CloudFront here means the OAC signature or the two Lambda permissions were not accepted.
- [ ] `aws lambda get-policy --function-name <ApiFunction> --query Policy --output text` → exactly two statements, principal `cloudfront.amazonaws.com`, each with `AWS:SourceArn` = this distribution, actions `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction`; no `"Principal":"*"`. [AWS-READ]
- [ ] `aws lambda get-function-url-config --function-name <ApiFunction>` → `AuthType: AWS_IAM`, `InvokeMode: BUFFERED`, no `Cors`. [AWS-READ]
- [ ] A query string survives signing: `$B/water?bbox=80.20,13.00,80.25,13.05` → 200 with `matched` > 0; and `$B/scenarios/rain_200mm/polygons?bbox=80.05,12.85,80.35,13.25&limit=5000` → 200, about 1.7 MB (large buffered response under the 6 MB limit).
- [ ] The first invocation shows no `Runtime.ImportModuleError` in the function log group (the ARM64 module loads); record `Init Duration` and `Max Memory Used`.
- [ ] No API Gateway exists: `aws cloudformation describe-stack-resources --stack-name FloodLens --query "StackResources[?starts_with(ResourceType,'AWS::ApiGateway')]"` → `[]`.
- [ ] The alarm e-mail subscription is **Confirmed**, not "PendingConfirmation": `aws sns list-subscriptions-by-topic --topic-arn <AlarmTopic>`. Unconfirmed subscriptions receive nothing.
- [ ] `aws location describe-key --key-name <name> --query '[ExpireTime,NoExpiry]'` → an expiry at most 90 days away, `NoExpiry: false`. Calendar a rotation reminder.
