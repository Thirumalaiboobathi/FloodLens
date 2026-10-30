# Deployment approval package

> **Superseded in part by Phase 13 (2026-10-30).** The deployment target changed from CloudFront + S3 + Lambda Function URL to
> **AWS Amplify Hosting + API Gateway HTTP API + Lambda** (stack `FloodLensAmplify`), after the first CloudFront deployment failed on the
> account's cache-policy query-string quota (see `reports/releases/2026-10-30-rc2/ROLLBACK-INVESTIGATION.md`). Statements below about
> CloudFront, the Function URL, OAC, `config.json`, the 4-alarm set, reserved concurrency and the `FloodLens` stack describe the old design.
> Current procedure: [AMPLIFY_MIGRATION.md](AMPLIFY_MIGRATION.md). Kept for the audit trail; its commands were never run against AWS.


Written for: the project owner. 2026-10-30. **Not executed. This package does not request approval yet:** section 2 lists prerequisites that are not met. Bootstrap is separate and not required ([BOOTSTRAP_APPROVAL_PACKAGE.md](BOOTSTRAP_APPROVAL_PACKAGE.md)).

## 1. What would be deployed

| Item | Value |
|---|---|
| Stack | `FloodLens` (CloudFormation, CDK app `infrastructure/bin/floodlens.ts`) |
| Account / region | **675613597178** / **ap-south-1** (the app aborts if the credentials are for another account or the default region differs) |
| Existing stack | none (`describe-stacks`: does not exist) so this is a first deployment: **0 replacements, 0 deletions**, 42 additions per `cdk diff` |
| Architecture | Browser → CloudFront → Lambda Function URL (`AWS_IAM`, OAC, SigV4) → FastAPI → published artifacts; private S3 for the site; Amazon Location key for the basemap |
| Source | commit `d2c67a2` **plus uncommitted Phase 6/7/8 work**. A deployment from this tree is not reproducible from Git. Deploy only from a **clean-tree** release bundle (prerequisite 1) |

### Resource inventory (37 synthesized; 36 appear in a diff)

| Service | Resources | Notes |
|---|---|---|
| S3 | 1 bucket + bucket policy | private, SSE-S3, versioned, TLS-only, `Retain` |
| CloudFront | distribution (PriceClass_200, HTTP/2+3), 2 OACs (S3, Lambda), 1 viewer-request function, 1 cache policy, 1 response-headers policy | default cert `*.cloudfront.net`; TLS 1.2 minimum not enforceable on it |
| Lambda | `ApiFunction` (python3.13, arm64, 1024 MB, 15 s), Function URL, 2 permissions | log group `Retain`, 30 days |
| Lambda (CDK helpers) | 1 Node 24 custom-resource provider, 1 Python bucket-deployment function, 2 AWS CLI layers | deploy-time only |
| Amazon Location | 1 API key (map actions only, referer = the distribution, expiry ≤ 90 days) | public browser key, not a secret |
| IAM | 3 roles + 2 policies | see IAM below |
| SNS | 1 topic + 1 e-mail subscription | must be confirmed by the recipient |
| CloudWatch | 4 alarms, 1 dashboard, 4 log groups | 30-day retention |
| Custom resources | `ReadMapsKey` (`describeKey`), 2 `BucketDeployment`s (site, `config.json`; `prune: false`) | records `Delete`, hold no data |

Not created: API Gateway, WAF, KMS, VPC/NAT, Secrets Manager, reserved concurrency, budgets, anomaly monitors, IAM users or access keys.

### IAM permissions the stack creates

API function: `AWSLambdaBasicExecutionRole` only (logging). `ReadMapsKey`: `geo:DescribeKey` on the one key. Bucket deployment: S3 read on the CDK assets bucket, S3 read/write/delete on the site bucket, `cloudfront:CreateInvalidation/GetInvalidation` on `*` (CDK default). Resource policy on the function: exactly `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction` for `cloudfront.amazonaws.com`, `SourceArn` = this distribution. No `*` principal.

## 2. Prerequisites (all must be true before you approve)

| # | Prerequisite | Status |
|---|---|---|
| 1 | Phases 6-8 reviewed and **committed by the owner**; `git status` empty; a new bundle created from the clean tree (`create_release_bundle.py create --release-id <new>` without `--allow-dirty`) and `verify` OK | **NOT MET** (dirty tree; rc1 is a draft) |
| 2 | Lambda concurrency: either a quota increase is granted and `-c reservedConcurrency=10` is used, or the owner explicitly accepts running with the shared quota of 10 and no reservation (cross-project throttling risk, audit finding P8-01) | **NOT MET** (quota 10, no reservation possible) |
| 3 | Budgets created by the owner: account $25/month and Amazon Location $15/month, alerts at 50/80/100% actual and forecast (commands below) | **NOT MET** (none for FloodLens) |
| 4 | Alarm e-mail address chosen, reachable, and the owner will confirm the SNS subscription | **NOT MET** (placeholder only) |
| 5 | Key expiry date chosen, at most 90 days after the day you run the command, with a rotation reminder | **NOT MET** (placeholder only) |
| 6 | Cost Anomaly Detection subscription confirmed in the console | not confirmed |
| 7 | Owner's written, explicit approval for the deploy command (and awareness of the shared-account and AdministratorAccess context, findings P8-06, P8-09) | pending |
| 8 | The leftover from audit incident I-1 (a 37 KB template object in the staging bucket) accepted or removed | decision pending |

## 3. Parameters (owner fills in)

| Parameter | Value | Rule |
|---|---|---|
| `alarmEmail` | `<owner address>` | required; valid e-mail; the recipient clicks the confirmation link or alarms are never delivered |
| `locationKeyExpireTime` | `<YYYY-MM-DDTHH:MM:SSZ>` | required; real date, in the future, **at most 90 days** from the day of the command. Suggest 85 days; set a rotation reminder at 75 |
| `reservedConcurrency` | omit now; `10` after a granted quota increase | optional. Limits simultaneous executions only, **not** request rate or spend |
| `invocationAlarmThreshold` | default 3000 per 5 minutes (about 10 requests/second sustained) | optional; a warning alarm, it blocks nothing |

## 4. Cost assumptions

Phase 6 model (not re-run), minus the removed API Gateway (about $0.13): about **$8.4 per month** at 2,000 sessions (range about $3.5-$20.6), about $42 at 10,000, about $213 at 50,000; Amazon Location tiles dominate. CloudFront, Lambda, S3, CloudWatch and SNS are near zero at pilot scale. **Budgets and anomaly detection notify but do not stop spend; map-tile spend is uncapped** (about $5,184 per month at 50 tiles/s sustained). No WAF (owner decision); a CloudFront WAF would not cover direct calls to the Amazon Location endpoint.

### Budget and monitor commands for the owner (NOT executed; they change AWS)

```bash
# Account budget B1 ($25) - shared account: includes other projects' spend
aws budgets create-budget --account-id 675613597178 --budget '{"BudgetName":"floodlens-account-25usd","BudgetLimit":{"Amount":"25","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}' \
  --notifications-with-subscribers file://notifications-account.json
# Location budget B2 ($15), filtered to the service (the exact service name string is [A]; confirm in Cost Explorer)
aws budgets create-budget --account-id 675613597178 --budget '{"BudgetName":"floodlens-location-15usd","BudgetLimit":{"Amount":"15","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST","CostFilters":{"Service":["Amazon Location Service"]}}' \
  --notifications-with-subscribers file://notifications-location.json
```

Each notifications file lists, for the owner's e-mail: `ACTUAL` at 50, 80 and 100 percent and `FORECASTED` at 100 percent (`ComparisonOperator GREATER_THAN`, `ThresholdType PERCENTAGE`). A `Default-Services-Monitor` anomaly monitor already exists; add or verify a subscription in the console. Do this **before** deploying.

## 5. Artifacts (draft rc1, dirty tree; regenerate on a clean tree and replace these)

| Artifact | Files | Listing SHA-256 |
|---|---|---|
| `data/api` (published API artifacts) | 630 | `60c24975555371d15dcfdb74821d8e4fb97a880a69d6884d40632292848fddbb` |
| Lambda package (`infrastructure/build/lambda`, 98.6 MiB) | 1,040 | `8b33da52a445042b193019adca6e9b1c7beba67a66c4386e85c3a350b7968c6f` |
| Web build (`apps/web/dist`) | 7 | `75a3fd5befb546c3d82bf9d7cb060968f978a80cfbd64ef79457c5cb6244c5fa` |
| Source tree | 177 | `286c92637d09d0f82b28bc1fe0a316a25bedb1ea98f77c846edfbb7bcc929df6` |
| `data-api.tar.gz` | 1 | `c6abb1d0dc97b95be2bf73743d70c8e32ed0fec91fe6ccf872a5cf35152c02c8` |

Source hashes will change when Phase 8 files are included and again after the owner commits; the rc2 values are the ones to approve.

## 6. Exact commands (would be executed only after written approval)

```bash
# 0. clean tree, then
.venv/Scripts/python.exe scripts/create_release_bundle.py verify --release-id <rc2>      # must print OK
cd infrastructure
npx cdk synth --quiet -c alarmEmail=<owner address> -c locationKeyExpireTime=<expiry>   # local
# do NOT run cdk diff (it writes to AWS; incident I-1). Review the synthesized template instead.
npx cdk deploy FloodLens -c alarmEmail=<owner address> -c locationKeyExpireTime=<expiry> [-c reservedConcurrency=10]
```

`cdk deploy` will ask interactively to approve IAM and security-group changes; answer only after reading the list (it matches section 1). Expected duration: 10-20 minutes (CloudFront creation dominates) [A].

## 7. Rollback

* Failure during the first deployment: CloudFormation rolls the stack back; the site bucket and the API log group are `Retain` and may be left behind (delete by hand if empty). The Amazon Location key may not delete cleanly (the CLI model requires 90 days of deactivation) [U]: expect to rename the key rather than reuse the name on a retry.
* After a successful deployment: redeploy the previous bundle; do **not** `cdk destroy` casually (retained resources remain). Revoke an abused map key by updating its restrictions or expiry with `--force-update` (runbook section 16), not by deleting it.
* The runbook (`DEPLOYMENT_RUNBOOK.md` sections 15-16) has the full procedure.

## 8. After deployment

Run [POST_DEPLOYMENT_VERIFICATION.md](POST_DEPLOYMENT_VERIFICATION.md) in order, starting with gates 4 and 5 (the direct Function URL must be refused and CloudFront must reach the function). Until then nothing is production-verified.
