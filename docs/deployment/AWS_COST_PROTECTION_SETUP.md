# AWS cost protection setup (for the owner to execute)

> **Superseded in part by Phase 13 (2026-10-30).** The deployment target changed from CloudFront + S3 + Lambda Function URL to
> **AWS Amplify Hosting + API Gateway HTTP API + Lambda** (stack `FloodLensAmplify`), after the first CloudFront deployment failed on the
> account's cache-policy query-string quota (see `reports/releases/2026-10-30-rc2/ROLLBACK-INVESTIGATION.md`). Statements below about
> CloudFront, the Function URL, OAC, `config.json`, the 4-alarm set, reserved concurrency and the `FloodLens` stack describe the old design.
> Current procedure: [AMPLIFY_MIGRATION.md](AMPLIFY_MIGRATION.md). Kept for the audit trail; its commands were never run against AWS.


Written for: the project owner. 2026-10-30. **No budget, alarm, anomaly subscription or notification has been created.** Every command below changes AWS and is yours to run. Labels: **[V]** verified read-only · **[A]** assumption · **[U]** unknown.

## 0. Read this first: what budgets do and do not do

* **A budget is not a spending cap.** AWS Budgets only *notifies* (and can run optional actions you configure). Nothing in this plan stops spending.
* Billing data lags by hours up to about a day, so an alert can arrive after the money is spent.
* **Forecasted** alerts are estimates and need some billing history before they work.
* Cost Anomaly Detection also only notifies, and needs a subscription to do even that.
* The largest uncapped item is Amazon Location tile requests (about $0.04 per 1,000; roughly $5,184 per month at 50 tiles/s sustained, from the Phase 6 cost model). The only *hard* controls are: the map key's expiry (at most 90 days), its referer restriction, and revoking or restricting the key (`DEPLOYMENT_RUNBOOK.md` section 16). No WAF (owner decision, and a CloudFront WAF would not protect the direct Amazon Location endpoint anyway).

## 1. Account-wide versus FloodLens-specific spend [V]

| Scope | What it contains | Used for |
|---|---|---|
| **Account-wide** (account 675613597178) | ShopFlow, AwsDeployDoctor, billsense and any other project plus FloodLens | the **$25** account budget |
| **FloodLens-specific** | Amazon Location Service (FloodLens is the only user of the service in this account: `list-keys` returned 0 keys [V]) | the **$15** Location budget |
| Not separable today | CloudFront, Lambda, S3, CloudWatch, SNS for FloodLens: the stack carries no cost-allocation tags, so Billing cannot split them from other projects [U] | tag the stack (section 6) if you want a FloodLens-only total |

Existing budgets in the account [V]: `My Monthly Cost Budget` $3, `TinaiPoet-Monthly-10USD` $10, `billsense-monthly-10usd` $10, `shopflow-monthly` $25. A new account-wide $25 budget duplicates `shopflow-monthly`'s amount but not its purpose: an alert on it means *the whole account*, not FloodLens, is near $25. The $15 Location budget is the only FloodLens-specific signal until tags exist. Expected FloodLens cost is about $8.4 per month at 2,000 sessions (range about $3.5-$20.6) [A: Phase 6 model, minus API Gateway].

## 2. Prepare the notification files

Replace `<owner address>` with the address you choose (it is not stored anywhere in this repository). Save as `notifications-account.json` and `notifications-location.json` (same content):

```json
[
  {"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":50,"ThresholdType":"PERCENTAGE"},
   "Subscribers":[{"SubscriptionType":"EMAIL","Address":"<owner address>"}]},
  {"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":80,"ThresholdType":"PERCENTAGE"},
   "Subscribers":[{"SubscriptionType":"EMAIL","Address":"<owner address>"}]},
  {"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":100,"ThresholdType":"PERCENTAGE"},
   "Subscribers":[{"SubscriptionType":"EMAIL","Address":"<owner address>"}]},
  {"Notification":{"NotificationType":"FORECASTED","ComparisonOperator":"GREATER_THAN","Threshold":80,"ThresholdType":"PERCENTAGE"},
   "Subscribers":[{"SubscriptionType":"EMAIL","Address":"<owner address>"}]},
  {"Notification":{"NotificationType":"FORECASTED","ComparisonOperator":"GREATER_THAN","Threshold":100,"ThresholdType":"PERCENTAGE"},
   "Subscribers":[{"SubscriptionType":"EMAIL","Address":"<owner address>"}]}
]
```

That gives 50/80/100% actual plus 80/100% forecast. (The request was "forecasted-spend alerts" without percentages; two levels are proposed. Drop the 80% forecast line if you want fewer e-mails.)

## 3. Create the budgets [changes AWS]

```bash
# B1: account-wide $25 per month
aws budgets create-budget --account-id 675613597178 \
  --budget '{"BudgetName":"floodlens-account-25usd","BudgetLimit":{"Amount":"25","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}' \
  --notifications-with-subscribers file://notifications-account.json

# B2: Amazon Location $15 per month (filter value must match the Billing service name exactly, see 3.1)
aws budgets create-budget --account-id 675613597178 \
  --budget '{"BudgetName":"floodlens-location-15usd","BudgetLimit":{"Amount":"15","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST","CostFilters":{"Service":["Amazon Location Service"]}}' \
  --notifications-with-subscribers file://notifications-location.json
```

**3.1 Service name [U].** `Amazon Location Service` is my best reading of the Billing service name and has not been confirmed: a Location filter on a service with no spend yet cannot be checked, and an earlier read-only `describe-budgets` CostFilters query errored in this CLI. Confirm in Billing -> Cost Explorer -> filter *Service* (type "Location"), or after the first tile request. A misspelt filter yields a budget that never alerts.

**3.2 Email.** Budget e-mail subscribers need no confirmation link, but check the spam folder for the first alert [A].

**3.3 Verify afterwards (read-only):**

```bash
aws budgets describe-budgets --account-id 675613597178 --query "Budgets[].[BudgetName,BudgetLimit.Amount,TimeUnit]" --output text
aws budgets describe-notifications-for-budget --account-id 675613597178 --budget-name floodlens-account-25usd
aws budgets describe-notifications-for-budget --account-id 675613597178 --budget-name floodlens-location-15usd
```

Expect 5 notifications each. Note the Phase 8 session could list budgets but not read the CostFilters of existing ones with that CLI [U]; if `describe-budgets` shows `CostFilters` for B2, good; if it prints nothing, check the console.

## 4. Cost Anomaly Detection

A monitor `Default-Services-Monitor` (DIMENSIONAL, per service) exists [V]. Its **subscriptions could not be read** from this CLI [U], so I cannot say whether anyone is notified.

1. Console -> Billing and Cost Management -> Cost Anomaly Detection -> *Alert subscriptions*: confirm a subscription exists on that monitor with your address and a threshold. Suggested: an absolute impact threshold of **$5** (the whole account's normal daily spend is small) with *individual alerts* or a daily summary.
2. If none exists, create one there. If you prefer the CLI: `aws ce create-anomaly-subscription` (needs the monitor ARN from `aws ce get-anomaly-monitors`). [changes AWS]
3. Detection learns a baseline over days; a brand-new service such as Amazon Location may not alert on the first day [A].

## 5. Amazon Location usage monitoring

* Metrics: namespace `AWS/Location` in CloudWatch. The stack's `FloodLens` dashboard does not yet plot them because the exact metric/dimension names were not observable before a key was in use [U].
* After the first tile requests: `aws cloudwatch list-metrics --namespace AWS/Location` and record the dimensions. Then create an alarm on tile/request count per key with a threshold of your choosing (for example the count that corresponds to $5 per day, about 125,000 tiles per day at $0.04 per 1,000) [A].
* Billing: Cost Explorer, group by *Service* and *Usage type*, daily, for the first two weeks.
* Do this daily in week one (`POST_DEPLOYMENT_VERIFICATION.md`, gate 17).

## 6. Optional: make FloodLens separable

Adding a cost-allocation tag such as `project=floodlens` would let a budget track CloudFront, Lambda, S3, CloudWatch and SNS for this project only. That needs a code change (`cdk.Tags.of(app).add(...)`) and activating the tag in Billing (activation takes up to 24 hours) [A]. It is not implemented; say so if you want it.

## 7. Checklist

| # | Step | Done by | Status |
|---|---|---|---|
| 1 | Choose the notification address | owner | open |
| 2 | Create B1 ($25 account) | owner | open |
| 3 | Confirm Billing service name, create B2 ($15 Location) | owner | open |
| 4 | Verify both budgets show 5 notifications | owner | open |
| 5 | Confirm Anomaly Detection subscription | owner | open |
| 6 | Accept that none of this caps spend; know the key-revocation procedure | owner | open |
| 7 | After deployment: Location metrics and per-key alarm | owner | open (after deploy) |
