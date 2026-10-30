"""FloodLens pilot cost model (list prices, USD/month, ap-south-1 / India edge). Prints Markdown tables.

Prices: AWS Price List bulk API (public, read 2026-10-30). Everything under ASSUMPTIONS is an estimate;
nothing here is a quote. Usage: python scripts/cost_model.py
"""

from __future__ import annotations

from dataclasses import dataclass

# --- VERIFIED list prices (AWS Price List bulk files; publication dates in the comments) ----------------
P = {
    "tile_per_1k": 0.04,          # AmazonLocationService APS3-Usage-Default-Maps-Tiles / GetTile (2026-09-11)
    "lambda_req_per_m": 0.20,     # AWSLambda APS3-Request-ARM (2026-10-22)
    "lambda_gb_s": 0.0000133334,  # AWSLambda APS3-Lambda-GB-Second-ARM tier 1 (2026-10-22)
    "httpapi_per_m": 1.05,        # AmazonApiGateway APS3-ApiGatewayHttpRequest, first 300M (2026-10-13)
    "cf_gb": 0.109,               # AmazonCloudFront IN-DataTransfer-Out-Bytes, first 10 TB (2026-10-19)
    "cf_https_per_m": 1.20,       # AmazonCloudFront IN-Requests-Tier2-HTTPS: $0.012 per 10,000 (2026-10-19)
    "s3_gb_month": 0.025,         # AmazonS3 APS3-TimedStorage-ByteHrs, first 50 TB (2026-10-15)
    "s3_get_per_m": 0.40,         # AmazonS3 APS3-Requests-Tier2: $0.004 per 10,000 (2026-10-15)
    "logs_ingest_gb": 0.67,       # AmazonCloudWatch APS3-DataProcessing-Bytes (2026-10-07)
    "logs_store_gb_month": 0.03,  # AmazonCloudWatch APS3-TimedStorage-ByteHrs (2026-10-07)
    "alarm_each": 0.10,           # AmazonCloudWatch APS3-CW:AlarmMonitorUsage; first 10 free (Global-CW:AlarmMonitorUsage)
    "dashboard": 3.00,            # AmazonCloudWatch DashboardsUsageHour-Basic; first 3 free (Global-...)
}
FREE_LAMBDA_REQ = 1_000_000       # AWSLambda Global-Request free tier (price list)
FREE_TILES_FIRST_3_MONTHS = 500_000  # aws.amazon.com/location/pricing (page text)

# --- MEASURED locally (scripts run 2026-10-30; compressed with gzip level 6, a proxy for CloudFront) ------
STATIC_GZ_MB = 0.51   # apps/web/dist: 1.87 MB raw
API_GZ_PER_SESSION_MB = 0.90  # 1 session-load of each endpoint (0.57 MB gz) + ~2 extra viewport sets (0.27 MB)
LAMBDA_PEAK_RSS_MIB = 156     # all heavy endpoints in one process


@dataclass
class Assume:
    """ESTIMATES: replace with analytics once the pilot has traffic."""
    name: str
    static_req: int = 6
    api_req: int = 25
    tiles: int = 100          # map tile requests per session (browser caching unknown)
    api_cache_hit: float = 0.50   # share of API requests answered by CloudFront without invoking Lambda
    lambda_s: float = 0.30    # average billed duration per invocation at 1,024 MB (local warm: 0.01-0.29 s)
    mem_gb: float = 1.0
    log_kb_per_invoke: float = 1.5
    log_kb_per_apigw_req: float = 0.35
    stack_alarms: int = 4
    stack_dashboards: int = 1


SCEN = [Assume("low", tiles=40, api_cache_hit=0.75), Assume("base"), Assume("high", tiles=250, api_cache_hit=0.0)]


def month(sessions: int, a: Assume, tile_free: bool = False) -> dict[str, float]:
    invokes = sessions * a.api_req * (1 - a.api_cache_hit)
    tiles = sessions * a.tiles
    gb_out = sessions * (STATIC_GZ_MB + API_GZ_PER_SESSION_MB) / 1024
    cf_req = sessions * (a.static_req + a.api_req)
    s3_get = sessions * a.static_req * 0.1  # ~10% of static hits miss the edge cache
    ingest_gb = (invokes * a.log_kb_per_invoke + invokes * a.log_kb_per_apigw_req) / 1024 / 1024
    c = {
        "Map tiles (Amazon Location)": max(0, tiles - (FREE_TILES_FIRST_3_MONTHS if tile_free else 0)) / 1000 * P["tile_per_1k"],
        "CloudFront transfer": gb_out * P["cf_gb"],
        "CloudFront HTTPS requests": cf_req / 1e6 * P["cf_https_per_m"],
        "HTTP API requests": invokes / 1e6 * P["httpapi_per_m"],
        "Lambda requests": max(0, invokes - FREE_LAMBDA_REQ) / 1e6 * P["lambda_req_per_m"],
        "Lambda compute": invokes * a.lambda_s * a.mem_gb * P["lambda_gb_s"],
        "S3 (storage + GET)": 0.01 * P["s3_gb_month"] + s3_get / 1e6 * P["s3_get_per_m"],
        "CloudWatch Logs": ingest_gb * P["logs_ingest_gb"] + ingest_gb * P["logs_store_gb_month"],
        "Alarms + dashboard": max(0, a.stack_alarms - 10) * P["alarm_each"] + max(0, a.stack_dashboards - 3) * P["dashboard"],
    }
    c["TOTAL"] = sum(c.values())
    c["_invokes"], c["_tiles"], c["_gb"] = invokes, tiles, gb_out
    return c


def fmt(x: float) -> str:
    return f"${x:,.2f}"


def main() -> None:
    print("### Monthly cost by volume (base assumptions, list price, no free tier except the Lambda 1M requests)\n")
    print("| Line | 2,000 sessions | 10,000 sessions | 50,000 sessions |\n|---|---:|---:|---:|")
    base = SCEN[1]
    cols = [month(s, base) for s in (2_000, 10_000, 50_000)]
    for k in [k for k in cols[0] if not k.startswith("_")]:
        print(f"| {'**'+k+'**' if k == 'TOTAL' else k} | " + " | ".join(fmt(c[k]) if k != "TOTAL" else f"**{fmt(c[k])}**" for c in cols) + " |")
    print("\n| Volume driver | 2,000 | 10,000 | 50,000 |\n|---|---:|---:|---:|")
    print("| Map tile requests | " + " | ".join(f"{c['_tiles']:,.0f}" for c in cols) + " |")
    print("| Lambda invocations (= HTTP API requests) | " + " | ".join(f"{c['_invokes']:,.0f}" for c in cols) + " |")
    print("| CloudFront transfer out (GB) | " + " | ".join(f"{c['_gb']:,.1f}" for c in cols) + " |")

    print("\n### Sensitivity: total per month (low / base / high assumptions)\n")
    print("| Scenario | tiles/session | API cache hit | 2,000 | 10,000 | 50,000 |\n|---|---:|---:|---:|---:|---:|")
    for a in SCEN:
        print(f"| {a.name} | {a.tiles} | {a.api_cache_hit:.0%} | " + " | ".join(fmt(month(s, a)["TOTAL"]) for s in (2_000, 10_000, 50_000)) + " |")

    print("\n### First three months (Location free tier of 500,000 tiles/month applied), base\n")
    print("| 2,000 | 10,000 | 50,000 |\n|---:|---:|---:|\n| " + " | ".join(fmt(month(s, base, True)["TOTAL"]) for s in (2_000, 10_000, 50_000)) + " |")

    print("\n### Abuse ceilings (worst case if a client sustains the limit for a whole 30-day month)\n")
    secs = 30 * 24 * 3600
    print("| Path | Limit | Requests/month | Cost/month |\n|---|---|---:|---:|")
    for rate in (50, 20, 10):
        n = rate * secs
        cost = n / 1e6 * P["httpapi_per_m"] + n / 1e6 * P["lambda_req_per_m"] + n * 0.30 * P["lambda_gb_s"]
        print(f"| HTTP API stage throttle | {rate} rps (300 ms Lambda) | {n:,.0f} | {fmt(cost)} |")
    for rate in (50, 100, 500, 2000):
        n = rate * secs
        print(f"| Map tiles via a leaked/forged-Referer key | {rate} tiles/s | {n:,.0f} | {fmt(n / 1000 * P['tile_per_1k'])} |")


if __name__ == "__main__":
    main()
