# Lambda concurrency quota plan

> **Superseded in part by Phase 13 (2026-10-30).** The deployment target changed from CloudFront + S3 + Lambda Function URL to
> **AWS Amplify Hosting + API Gateway HTTP API + Lambda** (stack `FloodLensAmplify`), after the first CloudFront deployment failed on the
> account's cache-policy query-string quota (see `reports/releases/2026-10-30-rc2/ROLLBACK-INVESTIGATION.md`). Statements below about
> CloudFront, the Function URL, OAC, `config.json`, the 4-alarm set, reserved concurrency and the `FloodLens` stack describe the old design.
> Current procedure: [AMPLIFY_MIGRATION.md](AMPLIFY_MIGRATION.md). Kept for the audit trail; its commands were never run against AWS.


Written for: the project owner. 2026-10-30. **Nothing in this document has been submitted or changed.** No quota request was made and no reserved concurrency is assigned. Labels: **[V]** verified by a read-only call or local test · **[A]** assumption · **[U]** unknown · **[AWS]** needs the deployed stack.

## 1. Current state [V, read-only, 2026-10-30]

| Item | Value |
|---|---|
| Quota | *Concurrent executions* (Service Quotas `lambda` / `L-B99A9384`), ap-south-1: **10**, adjustable |
| Unreserved concurrency | **10** (so no function in the account holds a reservation) |
| Pending increase request | none |
| Functions already in the region | 10 (ShopFlow, AwsDeployDoctor, billsense and others) |
| FloodLens | not deployed; the stack creates 1 API function plus 2 deploy-time helper functions (custom-resource provider, bucket deployment) |

`reservedConcurrency` is optional in the stack (`infrastructure/lib/floodlens-stack.ts`, passed as `-c reservedConcurrency=<n>`). It is **unset** now.

## 2. Reserved versus unreserved concurrency

* **Account quota**: the maximum number of function executions running at the same moment in a region, summed over every function. Concurrency is roughly *requests per second x seconds per request*.
* **Reserved concurrency (a function setting)** does two things at once: it *guarantees* that many executions to that function, and it *caps* the function at that many. The amount is taken out of the account pool.
* **Unreserved concurrency** is what is left for every function without a reservation. Lambda always keeps **at least 100** units unreserved, so a reservation is only accepted if `quota - all reservations >= 100` [A: documented AWS rule, not tested here].
* It limits simultaneous executions, **not** the request rate over time and **not** spending.

**Consequence for this account:** with a quota of 10 the unreserved pool is already below 100, so *any* reservation, including 10, is expected to be rejected, and a failed reservation would fail the stack deployment. That is why the Phase 8 package omitted it. `-c reservedConcurrency=10` is only usable after the quota is raised.

## 3. Recommended quota request (owner submits; not submitted)

| Field | Recommendation |
|---|---|
| Quota | Lambda *Concurrent executions*, ap-south-1 |
| Desired value | **1000** (the usual default for new regions/accounts) [A] |
| Why not less | 1000 leaves about 990 for the other projects after FloodLens reserves 10, and satisfies the 100-unreserved rule with a wide margin. A value such as 110 would be the bare minimum and leaves the other projects with almost nothing to burst into |
| Why this is safe | A quota is a ceiling, not a spend. Lambda is billed per request and per GB-second; raising the ceiling costs nothing by itself [V: AWS pricing model] |
| Approval time | unknown [U]; AWS may ask for a use case or grant a smaller value |

Exact command for the owner (this **changes AWS state**; I did not run it):

```bash
aws service-quotas request-service-quota-increase --service-code lambda --quota-code L-B99A9384 --desired-value 1000 --region ap-south-1
```

The console equivalent is Service Quotas -> AWS services -> AWS Lambda -> *Concurrent executions* -> Request increase.

## 4. Headroom recommendation

Do **not** assume 10 is sufficient, and do not reserve 10 until all of these hold:

1. The granted quota is known (command below) and `quota - 10 - existing reservations >= 100`, preferably `>= 500`.
2. Account-wide peak concurrency over the last 14 days is well below the unreserved remainder (CloudWatch `AWS/Lambda` `ConcurrentExecutions`, statistic Maximum, no function dimension).
3. The owner accepts that FloodLens becomes capped at 10 simultaneous executions.

Sizing [A]: most FloodLens responses are small cached JSON and should take well under a second; the heaviest (a 1.7 MB polygon response) takes longer and is rare. At 10 concurrent executions and an average of 0.3 s the function can serve about 33 requests per second; at 1 s about 10 per second. The existing `LambdaTrafficSpike` alarm fires at about 10 requests per second sustained (3,000 invocations per 5 minutes), so a reservation of 10 and the alarm are the same order of magnitude. CloudFront caching in front of the API lowers real concurrency further. These figures are not measured: **[AWS] load behaviour is unverified**.

## 5. What happens when concurrency is exhausted

| Situation | Behaviour |
|---|---|
| FloodLens at its reservation (10) | additional requests are **throttled**: the Function URL returns **HTTP 429** to CloudFront, which returns it to the browser. The dashboard shows its normal error panel with Retry. No request is queued; none is lost silently [A: standard behaviour for synchronous invocation, [AWS] to confirm]. Throttled calls are not billed, and the `Throttles` metric rises |
| Account pool exhausted (no reservation, today) | FloodLens and the other projects throttle each other: whichever asks when the pool is full gets 429 |
| Deployment itself | CDK helper functions also need concurrency during `cdk deploy`; with a quota of 10 shared with busy projects a custom-resource call could throttle and fail the deployment [A] |
| Cost | throttling does not raise the bill; Amazon Location tiles (the dominant cost) are fetched by the browser directly and are not limited by Lambda concurrency |

The stack already defines a `Throttles` alarm on the API function (alongside errors, latency and the traffic spike; asserted by the infrastructure tests, [S]), notifying the SNS topic. It only reaches you once the e-mail subscription is confirmed.

## 6. Impact on other projects

| Option | Other projects | FloodLens |
|---|---|---|
| A. Keep quota 10, no reservation (today) | all 10+ functions and FloodLens share 10; a burst anywhere can throttle everyone | no guarantee; can starve others |
| B. Raise to 1000, reserve 10 (recommended) | lose 10 of 1000 (1%); they can no longer be starved by FloodLens | guaranteed 10, capped at 10 |
| C. Raise to 1000, no reservation | shared pool of 1000; practically unconstrained at pilot scale | no cap; protection against a traffic spike is the alarm only |
| D. Reserve without raising | **rejected by Lambda** (expected) and fails the stack | n/a |

## 7. Verification commands (read-only)

```bash
export MSYS_NO_PATHCONV=1   # Git Bash on Windows
# the quota and what is unreserved
aws service-quotas get-service-quota --service-code lambda --quota-code L-B99A9384 --query "Quota.[QuotaName,Value,Adjustable]" --output text
aws lambda get-account-settings --query "AccountLimit.[ConcurrentExecutions,UnreservedConcurrentExecutions]" --output text
# status of a submitted request
aws service-quotas list-requested-service-quota-change-history-by-quota --service-code lambda --quota-code L-B99A9384 --query "RequestedQuotas[].[Status,DesiredValue,Created]" --output text
# existing per-function reservations
for f in $(aws lambda list-functions --query "Functions[].FunctionName" --output text); do echo "$f $(aws lambda get-function-concurrency --function-name $f --query ReservedConcurrentExecutions --output text)"; done
# account-wide peak concurrency, last 14 days (adjust dates)
aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name ConcurrentExecutions --start-time <ISO-14d-ago> --end-time <ISO-now> --period 86400 --statistics Maximum
```

After deployment [AWS]: `aws lambda get-function-concurrency --function-name <ApiFunction>` should print `10` and `get-account-settings` should show unreserved = quota - 10.

## 8. Order of operations

1. Owner submits the quota request; waits for `APPROVED`/`CASE_CLOSED` and re-reads the quota (section 7).
2. Owner confirms headroom (section 4).
3. Deploy with `-c reservedConcurrency=10` (or deploy without it first and add it by a later redeploy, which changes only the function setting).
4. Verify with `get-function-concurrency` and watch `Throttles` for the first week.

If the increase is refused, the options are the owner's: deploy without a reservation and accept shared-pool risk (option A), or ask for a smaller grant that still satisfies `quota - 10 >= 100`.
