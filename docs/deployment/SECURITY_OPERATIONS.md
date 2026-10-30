# FloodLens security and cost operations plan (pilot)

> **Phase 7 update (2026-10-30), read this first.** Several controls below were written for API Gateway and are superseded:
>
> | Topic | Phase 6 text | Now |
> |---|---|---|
> | Public `execute-api` URL (P6-05) | open, throttled | **Removed.** The API is a Lambda Function URL, `AWS_IAM`, invocable only by this CloudFront distribution (OAC). Unsigned direct calls must fail (verify after deployment, checklist H). |
> | Stage throttle 20 rps / burst 40 | recommended | **Does not exist.** Function URLs have no stage throttle, usage plans or WAF. Do not pass `apiRateLimit`/`apiBurstLimit` (synth aborts). |
> | What limits API abuse now | the throttle | (1) Only CloudFront can invoke, so traffic must come through CloudFront, which caches `/v1` for 5 minutes keyed on the 15 known query parameters; (2) optional `-c reservedConcurrency=N` hard-caps concurrent executions (check the quota first; N must leave at least 100 unreserved); (3) the `LambdaTrafficSpike` alarm (default 3000 invocations per 5 minutes, about 10 rps sustained; `-c invocationAlarmThreshold`), which **warns but does not block**; (4) Lambda errors/throttles/p95 alarms; (5) the owner's budgets. CloudFront-level rate limiting would need WAF on the distribution (not added: it creates billable resources; decision for the owner). |
> | Key expiry (P6-06) | optional | **Mandatory**, 1-90 days, validated at synth. |
> | Alarm e-mail (P6-11) | optional | **Mandatory**; the subscription is created but stays pending until the recipient confirms it. |
> | HEAD | 405 | supported (additive). |
>
> The cost ceilings below that assume the `execute-api` path (about $272/month at 20 rps, about $680 at 50 rps) no longer apply to a direct caller. Through CloudFront, cache hits cost nothing at Lambda and misses are bounded by the uncached query space and reserved concurrency. The **map-tile** spend (P6-04) is unchanged and still uncapped: the public key, its expiry, budgets and the Amazon Location call alarm remain the only controls.

Phase 6, 2026-10-30. **Nothing in this document has been created or executed.** No budget, alarm, key or AWS resource exists.
Every AWS-changing step belongs to the owner and needs explicit approval.

Labels: **[V]** verified (source named) · **[A]** assumption or estimate · **[U]** unknown, must be checked before relying on it.
Prices are in [COST_MODEL.md](COST_MODEL.md) (AWS Price List, read 2026-10-30); this file does not repeat them except where a decision depends on one.

## 1. Spending protection plan

### 1.1 What can and cannot be capped

| Layer | Hard cap exists? | What actually limits spend |
|---|---|---|
| Map tiles (Amazon Location) | **No** | A rate quota only (`GetTiles`, default 2,000, unit not stated [V: quotas page, Phase 5]). Quotas limit speed, not dollars. |
| HTTP API requests | No spend cap | Stage throttle (50 rps / burst 100 by default, set in the stack). |
| Lambda | No spend cap | Throttle upstream; optional reserved concurrency (quota-dependent, see 1.5). |
| CloudFront | No spend cap | Cache hit ratio; no WAF/rate rules in the pilot. |
| AWS Budgets | **Notifies only** [V: Budgets page says "monitor and receive notifications"] | Budget *actions* exist (cost extra beyond the first two) but are not recommended here: an automatic action that edits IAM/SCP is more machinery than the pilot warrants [A]. |

Consequence: the real protection is **fast detection plus a human kill-switch**, not a ceiling. Budget data is delayed (AWS Budgets updates
several times a day, not in real time [A]), so a CloudWatch usage alarm is the fast signal.

### 1.2 AWS Budgets (owner to create; monitoring is free [V], first two action-enabled budgets free, then $0.10/day each [V: AWS Budgets pricing page])

| # | Budget | Amount (pilot ~2,000 sessions/month) [A] | Notifications |
|---|---|---|---|
| B1 | Account, monthly cost | **$25** (base estimate $8.53, high case $20.69; first three months about $0.5-$4 with the tile free tier) | 50 % actual, 80 % actual, 100 % actual, **100 % forecasted**, plus 80 % forecasted |
| B2 | Filter: service *Amazon Location Service*, monthly | **$15** (base $8, high $10; tiles are about 94 % of the bill) | same five thresholds |

Rationale: B1 catches any runaway line; B2 isolates the one uncapped line so it cannot hide inside an otherwise small account total.
Scale the amounts with the tier in COST_MODEL.md section 6 ($75/$50 at 10k sessions, $300/$250 at 50k). Send to at least **two** addresses (one person on leave must not mean no alert). Use a monthly period and the "cost" type with charges and credits as the owner's accounting prefers [A].
A forecast notification needs enough usage history (AWS states a minimum of history before a forecast is produced [U: not re-checked]); do not rely on it during the first days.

### 1.3 Cost Anomaly Detection (owner to create)

* Monitor type: **AWS services** (one monitor covering all services); an anomaly then names the service that spiked, which is the quickest pointer to "tiles vs API vs CloudFront".
* Alert: individual-alert subscription to the same two addresses, threshold small enough to matter for a pilot (for example absolute impact >= $5) [A]; email delivery is enough.
* Pricing: described as free in secondary sources only; I could not confirm it on an AWS page, so treat as **[U]** and check the console before enabling. Cost Explorer must be enabled for it [A].
* It detects after the fact (usage must be billed and ingested first). It complements, and does not replace, the per-key alarm in 2.4.

### 1.4 API throttling and burst limits

* Stack default: `throttlingRateLimit` 50 rps, `throttlingBurstLimit` 100, configurable with `-c apiRateLimit=` / `-c apiBurstLimit=` (positive integers) [V: stack code and template].
* Sizing [A]: 2,000 sessions/month is roughly 70 sessions/day; even a busy hour is a few requests per second because the browser caches responses and CloudFront serves about half of API calls from cache. **20 rps / burst 40 is ample for the pilot** and lowers the sustained-abuse ceiling from about $680 to about $272 per month (COST_MODEL.md section 5). Recommendation: deploy with `-c apiRateLimit=20 -c apiBurstLimit=40`, raise only on evidence.
* The throttle is shared by every caller of the stage, legitimate or not. See 3.3: a flood to the direct URL consumes the same budget as CloudFront traffic.

### 1.5 Lambda concurrency, memory and timeout

* Configured: python3.13, arm64, **1,024 MB**, **15 s** timeout, no reserved concurrency unless `-c reservedConcurrency=N` [V: template].
* Measured locally [V]: peak RSS about 156 MiB, warm responses 1-290 ms, largest response 1.74 MB (the polygons limit=5000 case); largest bundled artifact 2.3 MB. Memory 1,024 MB is conservative; lowering it saves little (compute is about $0.50/month at 10k sessions) and reduces CPU. Keep it until real `Max Memory Used` and duration are read from CloudWatch.
* Timeout 15 s is generous against 300 ms typical; a runaway request costs at most 15 s x 1 GB. Consider 10 s after the first week's data [A].
* Steady-state concurrency at 20 rps x 0.3 s is about 6; at the default 50 rps about 15 [A]. A reservation of 20 would cap the Lambda while leaving the throttle as the real limiter. **Caveat [V: Lambda quotas docs, Phase 5]:** new accounts can have a total-concurrency quota as low as 10, in which case a reservation fails the deployment. Check Service Quotas (read-only, in the console or `aws service-quotas get-service-quota`) before passing `reservedConcurrency`; do not guess.

### 1.6 Abuse scenarios and responses

| Scenario | Effect | Detect | Response |
|---|---|---|---|
| A. Forged-Referer script pulls tiles with the public key | Up to the tile quota rate, **$5,184/month at 50 tiles/s sustained**, no cap [V arithmetic] | 2.4 alarm, B2, anomaly monitor | Section 2.5 kill-switch (expire/replace key) |
| B. Direct hits on the execute-api URL | Billed requests plus Lambda; capped by throttle at about $272 (20 rps) to $680 (50 rps) per month | API `Count` alarm, B1 | Lower throttle (redeploy), or move to Function URL + OAC (section 3) |
| C. Cache-busting through CloudFront | Same throttle as B; the cache key only contains the 15 API parameters, so random parameters do not defeat the cache [V: cache policy + test] | `Count`, cache hit ratio | Nothing needed |
| D. Large polygon pulls to inflate transfer | CloudFront $0.109/GB; one polygon response is about 0.4 MB compressed [V] | transfer in B1 | Throttle |
| E. Viral legitimate traffic | Tiles scale linearly: about $0.004 per session | B1, B2 | Raise budgets deliberately; nothing breaks |
| F. Throttle exhaustion by B | Legitimate calls get 429 while the flood lasts | 4xx alarm [A: not in stack] | Section 3.3 |

## 2. Amazon Location key lifecycle

### 2.1 What the key is

The key is a **browser credential that is public by design**: it is delivered in `/config.json` and appears in every tile URL (`?key=...`), so anyone who opens DevTools can read it [V: app code `loadRuntimeConfig`, `docs` F-21]. It must **never** be described as a secret and must not be treated as access control. Its protections are *scope* limits, not secrecy:

* actions limited to `geo-maps:GetStyleDescriptor`, `GetTile`, `GetSprites`, `GetGlyphs`; resource `arn:aws:geo-maps:ap-south-1::provider/default` [V: template];
* allowed referer `https://<distribution domain>/*` plus any `extraReferers` [V: template];
* optional expiry, **absent by default** (see 2.2).

Referer enforcement is a header check. A browser on another site cannot spoof it; a script can send any header it likes [V: documented behaviour that authorization uses the header value; Phase 5]. So the restriction blocks casual reuse from other websites, not deliberate abuse.

### 2.2 Expiry (target: at most 90 days)

* Stack behaviour [V: synthesized template]: **`NoExpiry: true` unless `-c locationKeyExpireTime=<ISO 8601>` is passed.** A deploy without it creates a key that never expires. This finding (P6-06) is stated again in RELEASE_READINESS.md.
* Required for the pilot: pass an expiry no more than 90 days out, and put the renewal date in the owner's calendar. After expiry the app degrades gracefully: the "Basemap unavailable" banner shows and data layers keep working [V: Phase 4 tests; **[U]** not exercised against real AWS].
* Proposed (not made, needs owner approval because it changes deploy behaviour): make the stack **refuse to synthesize without an expiry** unless `-c locationKeyNoExpiry=true` is given explicitly. Small, locally testable, no API contract change.

### 2.3 Rotation

The key's `KeyName` is derived from the stack id, so changing restrictions or expiry through CloudFormation updates the same key in place. To rotate (new key value):

1. Owner approves. Deploy a change that gives the key a new name (replacement): CloudFormation creates the new key and updates `config.json` via the deployment custom resource; new page loads receive the new key.
2. **[U] Old-key deletion:** the Location CLI model states "The API key must have been deactivated more than 90 days previously" before `DeleteKey` [V: `aws location delete-key help`, AWS CLI 2.34.59, read offline]. Whether CloudFormation's cleanup of the replaced key force-deletes is **not verified**; a failed cleanup would leave the stack in `UPDATE_COMPLETE_CLEANUP_IN_PROGRESS` / `DELETE_FAILED` states. Test this in a throwaway stack before relying on it, only with owner approval. Until then, rotate by **expiring the old key** (2.5), not deleting it.
3. Existing browser tabs keep the old key until reload; that is acceptable.

### 2.4 Per-key usage monitoring (does not exist yet)

* Metric: namespace `AWS/Location`, metric `CallCount`, dimension `ApiKeyName` [V: AWS docs, Phase 5]. The exact dimension set for **tile** calls is **[U]**; read it with `aws cloudwatch list-metrics --namespace AWS/Location` after the first real traffic, then create the alarm. (That command is read-only; creating the alarm is an owner action.)
* Suggested alarm [A]: sum over 1 hour > 10,000 tile calls (about $0.40/hour). Expected pilot traffic is about 6,700 tiles/day on average (2,000 sessions x 100 tiles / 30 days); recalibrate to about 5x the busiest hour seen in week one. Action: SNS to the owner (the stack's alarm topic exists when `alarmEmail` is given).
* Pair with budget B2; neither is a cap.

### 2.5 Emergency revocation and unexpected billing

Decision rule: if tile spend or `CallCount` is abnormal, **revoke first, investigate second** (the map degrades; nothing else breaks).

1. Confirm in Cost Explorer filtered to *Amazon Location Service* and the per-key metric (read-only).
2. Revoke, fastest first (owner-run, each changes AWS state):
   * `aws location update-key --key-name <name> --expire-time <now, ISO 8601> --force-update`. **[U]** whether a timestamp in the past or "now" is accepted; the CLI model documents `--expire-time` and `--force-update` ("for updating ExpireTime or Restrictions") but not this edge. If rejected, use the next line.
   * `aws location update-key --key-name <name> --restrictions <json with an unmatchable referer> --force-update` (documented updatable field [V: CLI model]).
   * Deleting is **not** an emergency tool (needs 90 days of deactivation [V]).
3. Record that the stack is now out of sync with the live key (CloudFormation drift); do not "fix" it by redeploying until the cause is known.
4. Create the replacement via rotation (2.3) with a tighter referer, expiry <= 90 days, and optionally a lower throttle.
5. File an AWS Support billing inquiry if charges are unexplained (owner). Keep the evidence: request counts, dates, the Referer values if any logging exists (none does for tiles).

## 3. Public API exposure

### 3.1 Facts

* The HTTP API has a default `https://<id>.execute-api.ap-south-1.amazonaws.com` endpoint. It serves `GET|HEAD /v1/{proxy+}` [V: template], and nothing else (`/docs`, `/openapi.json` return 404 there [V]).
* HTTP APIs support **no resource policy, no WAF association, no API keys/usage plans** [V: AWS docs, Phase 5]. You can disable the default endpoint only with a custom domain in front [V: Phase 5 review; **[U]** not re-fetched this phase].
* CloudFront can add a custom origin header, but the HTTP API itself cannot validate it. Only code in the Lambda (or a Lambda authorizer) can [V: reasoning from the feature list above].

### 3.2 Options compared

| Option | Closes the direct URL? | Reduces spend ceiling? | Complexity | Verified in real AWS? |
|---|---|---|---|---|
| **0. Keep as is** (throttle + alarms) | No | Only via the lower throttle | none | n/a |
| 1. CloudFront origin custom header, checked in Lambda | No: the request still reaches API Gateway and **invokes the Lambda** (billed) before being rejected | No (rejection saves compute only; HTTP API request and invocation still billed [A]) | small code change in `main.py`; **adds a shared secret that lives in the CloudFront config and is visible to anyone with `cloudfront:GetDistribution`** | no |
| 2. Lambda authorizer on the API | No: the authorizer is itself a billed Lambda call (cacheable) | No | medium | no |
| 3. Custom domain + disable default endpoint | Yes for the default URL, but the custom domain is public and equally throttled | No | Route 53/ACM, extra cost, DNS ownership [U] | no |
| 4. WAF | Not available on HTTP API [V]; would need a REST API or CloudFront-level WAF | WAF has its own price [U] | large; switches API type | no |
| **5. Replace HTTP API with a Lambda Function URL (`AuthType AWS_IAM`) behind CloudFront using OAC** | **Yes.** An unsigned direct request is refused by Lambda's IAM check (403) [V: CloudFront and Lambda docs] | Yes for the direct path: only CloudFront can invoke. The tile cost is unaffected | small-medium; see 3.4 | no |

### 3.3 Residual risk that is easy to miss

The stage throttle is shared by all callers. A flood to the direct URL at the limit uses the same budget as CloudFront-origin traffic, so it can make legitimate cache misses return **429** for as long as it lasts. Cached responses (5 minutes, about half of API calls) keep working. That is an availability risk, not a cost one, and options 0-4 do not remove it.

### 3.4 Recommendation for the pilot

* **Preferred, if the owner accepts a small infrastructure change: option 5.** It is the only option here with a measurable security benefit (the execute-api path disappears) at modest complexity, and the public contract does **not** change: the browser still calls `https://<distribution>/v1/...` and Lambda Function URLs use the same payload format 2.0 that the Mangum handler already receives [V: `lambda_handler.py` is Mangum; payload format of function URLs is 2.0 **[U: not re-fetched]**]. Facts supporting feasibility:
  * CloudFront OAC supports Lambda function URLs; the URL must use `AuthType AWS_IAM`; CloudFront needs `lambda:InvokeFunctionUrl` **and** `lambda:InvokeFunction` on the function, scoped to the distribution [V: CloudFront and Lambda docs, fetched this phase]. GET/HEAD need no payload hash [V].
  * The installed CDK (2.275.0) has `FunctionUrlOrigin.withOriginAccessControl` [V: local type definitions]; it should create the permissions. Template assertions can test it offline.
  * Costs: HTTP API's $1.05/M requests disappears; throttling and the API access log go with it (a Function URL has no stage throttle; Lambda concurrency becomes the only limiter, so `reservedConcurrency` or an account concurrency limit matters more, [U] on new accounts).
  * Unknowns to test on a first deploy: signed-request behaviour for `HEAD`, 6 MB/response size behaviour is unchanged, error pages from a 403 at the origin, whether Lambda bills a request rejected by the IAM check (believed not, **[U]**).
* **Otherwise (option 0 with tighter limits):** deploy with `apiRateLimit 20`, `apiBurstLimit 40`, an API `Count` alarm, and accept F-02 explicitly. This is a reasonable pilot posture if the owner prefers to deploy the architecture already tested.
* **Not recommended for the pilot:** options 1-4. Option 1 adds a secret and code while leaving the direct path and the bill intact; options 3-4 add domains, accounts or a different API type for little gain at pilot scale.

No infrastructure change was made. Option 5 needs the owner's decision because it replaces the front door of the API; it is not an API contract change (paths, methods, payloads are identical) but it is a deployment architecture change.

### 3.5 What cannot be claimed

* That putting CloudFront in front protects the API: it does not, by itself.
* That the key's Referer restriction stops abuse: it does not (2.1).
* That any control here was exercised against real AWS: none was.

## 4. Operational routine after deployment (owner)

* Daily for the first week: Cost Explorer (service view), the stack's alarm email, and `CallCount` for the key.
* Weekly: tiles per session (replace the 100 assumption in COST_MODEL.md), Lambda `Max Memory Used`, `Init Duration`, cache hit ratio.
* Before day 80 of the key's life: rotate (2.3).
* Any change to the stack: `cdk synth`, read the template, then approve. (`cdk diff` is not read-only: it publishes the template to the CDK staging bucket and creates a change set.)
