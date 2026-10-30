# Post-deployment verification plan

Written for: the person running the first deployment checks. 2026-10-30. **Nothing in this plan has been run against AWS.** It complements `POST_DEPLOYMENT_CHECKLIST.md` (detailed commands) with an ordered gate list and status labels.

Labels: **LOCAL** = verified on this machine before deployment · **SYNTH** = true of the synthesized template only · **AWS** = requires the deployed stack · **MON** = requires monitoring over time.

Variables (from stack outputs): `SITE=https://<SiteUrl>`, `FNURL=<ApiFunctionUrl>`, `B=$SITE/v1/regions/chennai`, `FN=<ApiFunction name>`, `TOPIC=<AlarmTopic ARN>`. Never paste the map key into a ticket or chat.

## Ordered gates (stop at the first failure and report; do not "fix forward")

| # | Check | Status before deploy | Exact test | Pass |
|---|---|---|---|---|
| 1 | Dashboard via CloudFront | LOCAL (build, 83 tests); AWS for delivery | `curl -sI $SITE/` | 200, `text/html`, HSTS, nosniff, frame DENY, referrer-policy, CSP present; `http://` redirects to https |
| 2 | `/config.json` | LOCAL (loader tests), SYNTH (behaviour, no-cache) | `curl -sD- $SITE/config.json` | JSON with only the 4 `VITE_*` keys, `cache-control: no-store`, never `Hit from cloudfront` twice |
| 3 | CSP in a browser | SYNTH (policy text); AWS (behaviour) | open `$SITE` in Chrome DevTools | no `Refused to ...` console errors; style/tiles/glyphs requests only to `maps.geo.ap-south-1.amazonaws.com` |
| 4 | **Direct unsigned Function URL refused** | SYNTH (AuthType AWS_IAM, no public permission); **AWS** to prove | `curl -s -o /dev/null -w "%{http_code}\n" $FNURL/v1/health` | **403**. A 200 = public function: **BLOCKER, stop** |
| 5 | **CloudFront → Function URL via OAC** | SYNTH; AWS | `curl -s -o /dev/null -w "%{http_code}\n" $SITE/v1/health` | 200 and `x-floodlens-schema-version: 1.0.0`. A 403 = signature or permission not accepted |
| 6 | GET API | LOCAL (189 API tests, synthetic events) | `curl -s $B/rainfall`, `$B/scenario-catalog`, `$B/summary` | 200, JSON schema 1.0.0/1.1.0 shapes; `$B/water?bbox=70,5,90,25` → 422 `bbox_too_large` as JSON (not index.html); unknown path → 404 JSON |
| 7 | HEAD API | LOCAL (28 parity cases + Function URL events) | `curl -sI $B/rainfall` and `$B/rainfall/<id>/raster` | same status and content-type as GET, no body; record whether `content-length` survives [U] |
| 8 | Raster retrieval | LOCAL (byte-identical base64 handler test) | `curl -s $B/rainfall/<id>/raster \| head -c 4 \| xxd` | `4949 2a00`, `content-type: image/tiff`; sha256 of the full download equals `x-content-sha256` |
| 9 | Water features | LOCAL | `curl -s "$B/water?bbox=80.20,13.00,80.25,13.05&limit=1"` | 200, `matched` > 0, `source.license` `ODbL 1.0`, attribution present; query string survives signing |
| 10 | Historical (Final) rainfall | LOCAL | `curl -s $B/rainfall?stream=historical_final` | only `obs_final_*` datasets; labelled "rainfall accumulation", never flood depth |
| 11 | Recent (Late) rainfall | LOCAL | `curl -s $B/rainfall?stream=recent_late`; `$B/rainfall/freshness` | only `obs_late_*` datasets, labelled provisional; `data_freshness` present; nothing described as live or real-time |
| 12 | Amazon Location tiles | none locally (no real key) → AWS | browser: map draws the Monochrome basemap under the layers; Network shows 200s from the maps host | tiles render; blocking the maps host shows the "Basemap unavailable" banner and the data layers still work |
| 13 | Error handling | LOCAL (frontend tests) | block `/v1` in DevTools, reload | error panel appears, no blank screen, no stack trace |
| 14 | Retry behaviour | LOCAL (frontend tests) | unblock, press Retry | the panel recovers without a page reload |
| 15 | SNS confirmation | AWS | `aws sns list-subscriptions-by-topic --topic-arn $TOPIC --query "Subscriptions[].SubscriptionArn"` | not `PendingConfirmation`; the owner clicked the link. Until then no alarm e-mail is delivered |
| 16 | CloudWatch alarms | SYNTH (4 alarms, SNS action); MON | `aws cloudwatch describe-alarms --alarm-name-prefix FloodLens --query "MetricAlarms[].[AlarmName,StateValue]"` | all `OK` or `INSUFFICIENT_DATA`; optional `set-alarm-state` test (changes state) produces an e-mail |
| 17 | Cost and usage | MON | owner's budgets in Billing; `aws cloudwatch list-metrics --namespace AWS/Location` | budgets exist and show the right scope; create the per-key call alarm once the real dimensions are visible; review daily for the first week |

Also confirm after deployment: `aws lambda get-policy --function-name $FN` shows exactly the two scoped statements, `aws lambda get-function-url-config --function-name $FN` shows `AWS_IAM`/`BUFFERED` with no `Cors`, and `aws cloudformation describe-stack-resources --stack-name FloodLens` lists no `AWS::ApiGateway*`, NAT, VPC, WAF or KMS resource.

## First ARM64 invocation (closes the ARM64 gap; currently POST_DEPLOYMENT_VERIFICATION_REQUIRED)

ARM64 compatibility of the Lambda package is **not proven** until this passes.

1. Make exactly one request through CloudFront: `curl -s -o /dev/null -w "%{http_code}\n" $SITE/v1/health` (a cold start).
2. Within a minute read the log: `aws logs filter-log-events --log-group-name /aws/lambda/$FN --filter-pattern "?ImportModuleError ?Runtime.ImportModuleError ?Traceback ?ERROR" --max-items 5`: expect no events.
3. Read the report line: `aws logs filter-log-events --log-group-name /aws/lambda/$FN --filter-pattern "REPORT" --max-items 3`: record `Init Duration`, `Duration`, `Max Memory Used` (expected roughly 150-300 MB of 1,024 [A]).
4. Fetch the heaviest response once: `curl -s -o /dev/null -w "%{http_code} %{size_download}\n" "$B/scenarios/rain_200mm/polygons?bbox=80.05,12.85,80.35,13.25&limit=5000"` expect `200`, about 1.7 MB (or less if compressed).
5. `aws lambda get-function --function-name $FN --query "Configuration.[Architectures,Runtime,CodeSize]"` expect `arm64`, `python3.13`.
6. If an `ImportModuleError` appears: stop, keep the evidence, do not redeploy blindly. The likely causes are a wheel for the wrong platform or ABI; compare with `validation.json` of the bundle.
7. Record results in the deployment log (`DEPLOYMENT_RUNBOOK.md` section 17).

## Rollback and emergency key restriction (Phase 9)

**Do not execute any command in this plan against an undeployed stack**: every `curl`/`aws` check above needs the stack outputs. Nothing here has been run.

Decide in this order when a gate fails:

| Symptom | First action (owner-run, each changes AWS) | Detail |
|---|---|---|
| Gate 4 returns 200 (Function URL public) | `aws lambda delete-function-url-config --function-name $FN` (closes the door at once; CloudFront then returns 5xx for `/v1`) and keep the evidence | then fix the template; do not redeploy blindly |
| Gate 5 returns 403/5xx for `/v1` only | leave the site up (static content works), investigate OAC/permissions with `aws lambda get-policy` and `get-function-url-config`; redeploy the previous bundle if a code change is the cause | `DEPLOYMENT_RUNBOOK.md` section 15 |
| Stack creation fails | CloudFormation rolls back; delete retained leftovers by hand (empty site bucket, log group); rename the Location key before retrying | `DEPLOYMENT_APPROVAL_PACKAGE.md` section 7 |
| Bad release after success | redeploy the previous clean bundle (new `cdk deploy` of the earlier commit); do not `cdk destroy` casually | |
| **Abnormal tile spend or `CallCount`** | **Revoke first, investigate second**: `aws location update-key --key-name <name> --expire-time <now ISO> --force-update` (a timestamp at or before now may be rejected [U]); fallback: `update-key --restrictions <json with an unmatchable referer> --force-update` | `SECURITY_OPERATIONS.md` section 2.5. The map degrades; the data layers keep working (gate 12). Then record the CloudFormation drift and rotate (2.3) with expiry at most 90 days |
| Lambda throttling (429s, `Throttles` alarm) | check the account's concurrency (`LAMBDA_QUOTA_PLAN.md` section 7) before touching the stack | |

Do not rehearse a key revocation on the production key. Test the degraded "Basemap unavailable" state (gate 12) by blocking the maps host in DevTools instead.

## Additional gates added in Phase 9

| # | Check | Pass |
|---|---|---|
| 18 | Quota and reservation: `aws lambda get-account-settings` and `get-function-concurrency --function-name $FN` | matches what the owner approved (`LAMBDA_QUOTA_PLAN.md`) |
| 19 | Key lifecycle: `aws location describe-key --key-name <name> --query "[ExpireTime,Restrictions.AllowReferers]"` | `ExpireTime` is at most 90 days away; referer = the distribution; actions map-only. Calendar reminder at day 75 |
| 20 | Budgets exist and are the intended scope: the three commands in `AWS_COST_PROTECTION_SETUP.md` 3.3 | 5 notifications each |
| 21 | Anomaly subscription confirmed in the console | owner has seen it |

## Not verifiable by anyone before deployment

Whether CloudFront's signed request is accepted (checks 4-5), the production CSP (3), real tile rendering (12), the effect of a Lambda quota of 10 under load (watch `Throttles`), `content-length` through the Function URL on HEAD (7), CloudFront error-caching behaviour for 4xx, and anything about cost behaviour (17). Record each result as pass, fail or "unexpected" with the date; anything unexpected becomes a finding.
