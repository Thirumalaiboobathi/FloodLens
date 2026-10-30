# FloodLens pilot cost model

> **Superseded in part by Phase 13 (2026-10-30).** The deployment target changed from CloudFront + S3 + Lambda Function URL to
> **AWS Amplify Hosting + API Gateway HTTP API + Lambda** (stack `FloodLensAmplify`), after the first CloudFront deployment failed on the
> account's cache-policy query-string quota (see `reports/releases/2026-10-30-rc2/ROLLBACK-INVESTIGATION.md`). Statements below about
> CloudFront, the Function URL, OAC, `config.json`, the 4-alarm set, reserved concurrency and the `FloodLens` stack describe the old design.
> Current procedure: [AMPLIFY_MIGRATION.md](AMPLIFY_MIGRATION.md). Kept for the audit trail; its commands were never run against AWS.


> **Phase 7 note.** API Gateway was removed: the "HTTP API requests" line and the `execute-api` abuse ceilings below no longer apply to the deployed design (the figures came from `scripts/cost_model.py`, which was not changed or rerun). Lambda invocations are now bounded by CloudFront cache misses. Map-tile and CloudFront figures are unchanged.

Generated 2026-10-30 by `python scripts/cost_model.py` (re-run it after changing an assumption). USD, list prices,
ap-south-1 and the India CloudFront price tier. **This is a model, not a quote.** Nothing has been deployed or billed.

Labels: **[V]** confirmed price from the AWS Price List bulk API (public files, publication date shown) · **[A]** assumption · **[U]** unknown or not checked.

## 1. Confirmed prices

| Item | Price | Source (usage type, publication) |
|---|---:|---|
| **Amazon Location map tiles (`GetTile`)** | **$0.04 per 1,000** | `APS3-Usage-Default-Maps-Tiles`, 2026-09-11 [V] |
| Location style / sprite / glyph requests | free | AWS docs "Maps pricing" [V] |
| Location free tier | 500,000 map tiles/month for the first 3 months | aws.amazon.com/location/pricing page text [V] |
| Lambda requests, arm64 | $0.20 per million | `APS3-Request-ARM`, 2026-10-22 [V] |
| Lambda compute, arm64 | $0.0000133334 per GB-second (first tier) | `APS3-Lambda-GB-Second-ARM`, 2026-10-22 [V] |
| Lambda free requests | first 1,000,000 | `Global-Request`, 2026-10-22 [V] |
| HTTP API requests | $1.05 per million (first 300 M) | `APS3-ApiGatewayHttpRequest`, 2026-10-13 [V] |
| CloudFront transfer out (India), first 10 TB | $0.109 per GB | `IN-DataTransfer-Out-Bytes`, 2026-10-19 [V] |
| CloudFront HTTPS requests (India) | $0.012 per 10,000 ($1.20/M) | `IN-Requests-Tier2-HTTPS`, 2026-10-19 [V] |
| CloudFront invalidations | first 1,000 paths/month free, then $0.005 | 2026-10-19 [V] |
| S3 storage / GET | $0.025 per GB-month / $0.004 per 10,000 GET | `APS3-TimedStorage-ByteHrs`, `APS3-Requests-Tier2`, 2026-10-15 [V] |
| CloudWatch Logs ingest / storage | $0.67 per GB / $0.03 per GB-month | `APS3-DataProcessing-Bytes`, `APS3-TimedStorage-ByteHrs`, 2026-10-07 [V] |
| CloudWatch alarm / dashboard | $0.10 per alarm (first 10 free) / $3 per dashboard (first 3 free) | `APS3-CW:AlarmMonitorUsage`, `DashboardsUsageHour-Basic` plus `Global` free rows [V] |

Not checked [U]: CloudFront Function invocations (≈ 300 k/month at 50k sessions; negligible), SNS delivery, AWS Budgets
pricing, CloudFront's separately advertised monthly free allowance (1 TB / 10 M requests — **not** applied below), CloudFront
data transfer from AWS origins, Location data-transfer charges (none seen in the price list), taxes, currency conversion.

## 2. Measured inputs (local, 2026-10-30)

| Input | Value |
|---|---|
| Web bundle | 1.87 MB raw, ≈ 0.51 MB gzip (`apps/web/dist`) |
| API payloads, gzip level 6 (proxy for CloudFront) | scenarios 0.3 KB · layers 1.8 KB · provenance 4.5 KB · polygons (whole AOI) 424 KB · roads viewport 36 KB · facilities viewport 37 KB · water viewport 62 KB |
| Lambda memory | peak RSS 156 MiB across all endpoints (1,024 MB configured) |
| Lambda duration | warm 10–290 ms locally on x86 Windows; **300 ms used** to allow for slower arm64 vCPU share at 1 GB [A] |

## 3. Session assumptions [A]

| Assumption | Low | **Base** | High |
|---|---:|---:|---:|
| Map tile requests per session | 40 | **100** | 250 |
| API requests per session (viewer → CloudFront) | 25 | **25** | 25 |
| Share of API requests served from the CloudFront cache | 75 % | **50 %** | 0 % |
| Static requests per session / transfer | 6 / 0.51 MB | 6 / 0.51 MB | 6 / 0.51 MB |
| API transfer per session | 0.9 MB | 0.9 MB | 0.9 MB |
| Lambda duration / memory | 300 ms / 1 GB | | |
| Logs per Lambda invocation (+ access log) | 1.5 KB + 0.35 KB | | |

Basis for 100 tiles/session: a 1,568 × 900 viewport at a 512 px tile size needs ≈ 12–16 tiles; ≈ 6 distinct views per session; browser caching unknown. Replace with real analytics after launch.

## 4. Monthly cost by volume (base assumptions)

| Line | 2,000 sessions | 10,000 sessions | 50,000 sessions |
|---|---:|---:|---:|
| Map tiles (Amazon Location) | $8.00 | $40.00 | $200.00 |
| CloudFront transfer | $0.30 | $1.50 | $7.50 |
| CloudFront HTTPS requests | $0.07 | $0.37 | $1.86 |
| HTTP API requests | $0.03 | $0.13 | $0.66 |
| Lambda requests | $0.00 | $0.00 | $0.00 |
| Lambda compute | $0.10 | $0.50 | $2.50 |
| S3 (storage + GET) | $0.00 | $0.00 | $0.01 |
| CloudWatch Logs | $0.03 | $0.15 | $0.77 |
| Alarms + dashboard | $0.00 | $0.00 | $0.00 |
| **TOTAL** | **$8.53** | **$42.66** | **$213.30** |

| Volume driver | 2,000 | 10,000 | 50,000 |
|---|---:|---:|---:|
| Map tile requests | 200,000 | 1,000,000 | 5,000,000 |
| Lambda invocations (= HTTP API requests) | 25,000 | 125,000 | 625,000 |
| CloudFront transfer out (GB) | 2.8 | 13.8 | 68.8 |
| S3 storage | < 0.01 GB (web bundle, versioned) | | |

Map tiles are ≈ 94 % of the bill at every volume. Everything else together is ≈ $0.5 / $2.7 / $13.

### Sensitivity (total per month)

| Scenario | tiles/session | API cache hit | 2,000 | 10,000 | 50,000 |
|---|---:|---:|---:|---:|---:|
| low | 40 | 75 % | $3.65 | $18.27 | $91.34 |
| base | 100 | 50 % | $8.53 | $42.66 | $213.30 |
| high | 250 | 0 % | $20.69 | $103.45 | $517.28 |

### First three months (Location free tier of 500,000 tiles/month applied, base)

| 2,000 | 10,000 | 50,000 |
|---:|---:|---:|
| $0.53 | $22.66 | $193.30 |

## 5. Abuse ceilings — the numbers that matter for budgets

Worst case if a client sustains the rate for a whole 30-day month.

| Path | Limit | Requests/month | Cost/month |
|---|---|---:|---:|
| HTTP API stage throttle (default) | 50 rps, 300 ms Lambda | 129,600,000 | **$680.40** |
| HTTP API stage throttle | 20 rps | 51,840,000 | $272.16 |
| HTTP API stage throttle | 10 rps | 25,920,000 | $136.08 |
| Map tiles via a leaked key with a forged Referer | 50 tiles/s | 129,600,000 | **$5,184** |
| | 100 tiles/s | 259,200,000 | $10,368 |
| | 500 tiles/s | 1,296,000,000 | $51,840 |
| | 2,000 tiles/s (default `GetTiles` quota [V], unit unstated) | 5,184,000,000 | $207,360 |

The API ceiling is bounded by the throttle you choose. **The tile ceiling is bounded only by AWS's rate quota**, so the
real protection is detection and a kill-switch (section 6). The Referer restriction stops other *websites* using the
key in a browser; it does not stop a script that sends the header [V: service authorizes on the header value].

## 6. Recommended budget and usage alerts (do not exist yet — owner to create)

| # | Alert | Suggested threshold [A] | Why |
|---|---|---|---|
| 1 | AWS Budget, account monthly cost, actual + forecast | $25 for the 2k tier, $75 for 10k, $300 for 50k; notify at 50 / 80 / 100 % | catches any runaway line |
| 2 | AWS Budget filtered to service *Amazon Location Service* | $15 / $50 / $250 matching the tiers; also 100 % forecast | tiles are the only uncapped line |
| 3 | AWS Cost Anomaly Detection (service-level monitor) | default | catches a spike inside the monthly total |
| 4 | CloudWatch alarm on `AWS/Location` `CallCount` for the key | e.g. > 5× the highest hour seen in the first week | per-key abuse signal; **dimensions must be read from `list-metrics` after first traffic** [U] |
| 5 | CloudWatch alarm on HTTP API `Count` | e.g. > 5 × expected hourly peak | detects execute-api scraping |
| 6 | Existing alarms (Lambda errors/throttles/p95, API 5xx) | in stack | needs `alarmEmail` and email confirmation |

Budgets do not stop spend by themselves. The kill-switch is: expire or delete the Location key (the UI then degrades to a
blank background with the data layers intact) and lower the API throttle with a redeploy.

## 7. Region-dependent or unknown charges

* Everything above is Mumbai (ap-south-1) or the India CloudFront tier; users elsewhere are billed on other tiers [V: price list has per-geography rows].
* CloudFront requests from edge to a custom origin for cache misses carry no extra line I could confirm; "data transfer out to origin" ($0.16/GB India) applies to uploads, which this GET-only app does not do [A].
* Tile traffic bypasses CloudFront (browser → Amazon Location directly), so caching does not reduce it.
* Costs not in the stack today: WAF, custom domain / Route 53 / ACM, CloudFront access logs, Shield, a CloudTrail trail beyond the default event history [U].
