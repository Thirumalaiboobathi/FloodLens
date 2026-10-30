# FloodLens deployment runbook (first deployment)

> **Phase 7 update (2026-10-30).** The API is now a Lambda Function URL (`AWS_IAM`) behind CloudFront origin access control; API Gateway is gone, `HEAD` is
> supported, `alarmEmail` and `locationKeyExpireTime` are mandatory, and a local release bundle exists (`reports/releases/2026-10-30-rc1`, see
> [RELEASE_ARTIFACTS.md](RELEASE_ARTIFACTS.md)). The synth flags in section 6 and the checklists below were updated accordingly. Still **NOT EXECUTED**; the
> deployment gates stand. Any source change after a bundle was made needs a **new** release id (bundles are never overwritten).

**Status: NOT EXECUTED.** Written 2026-10-30 (Phase 6). Nothing here has been run against AWS. Commands are for the **project owner** (or an operator the owner directs).
Account `675613597178`, region `ap-south-1`. Bash syntax (Git Bash on Windows); run from the repo root unless stated.

Legend: **[LOCAL]** changes nothing outside this machine · **[AWS-READ]** read-only AWS call · **[AWS-WRITE]** changes AWS state and needs the owner's explicit approval, **per command group**. Stop at every **GATE** until the owner says yes in writing.

Facts and risks referenced as P6-xx / F-xx are in [RELEASE_READINESS.md](RELEASE_READINESS.md) and [PRE_DEPLOYMENT_REVIEW.md](PRE_DEPLOYMENT_REVIEW.md).

## 1. Pre-deployment Git checks [LOCAL]

```bash
git status --short                      # must print nothing
git rev-parse --abbrev-ref HEAD         # main
git rev-parse HEAD                      # record this hash in the deployment log
git fetch --dry-run 2>&1 | head -3      # optional: shows whether the remote moved; changes nothing
git log --oneline -3
.venv/Scripts/python.exe scripts/release_integrity.py --baseline d2c67a2 --out reports/pre-deployment/release-integrity-predeploy.json
.venv/Scripts/python.exe scripts/data_baseline.py --verify     # ignored data vs recorded hashes: must print UNCHANGED
.venv/Scripts/python.exe scripts/create_release_bundle.py verify --release-id <id>   # the release bundle (RELEASE_ARTIFACTS.md) must print OK
```

Pass criteria: clean tree; release_integrity exits 0 with `git comparison: compared` and no BLOCKER; data inventory UNCHANGED.
Note (P6-01): `d2c67a2` is the root commit, so "identical" means *unchanged since that commit*, not since Phase 1.
If any frozen v1 file differs, **stop**: do not edit it; report it.

## 2. Environment [LOCAL]

```bash
export AWS_REGION=ap-south-1 AWS_DEFAULT_REGION=ap-south-1
# Never put keys or tokens in this file or shell history. Use the profile the owner chooses:
export AWS_PROFILE=<owner-chosen-profile>
node --version          # 20+ (24.13 tested)
.venv/Scripts/python.exe --version    # 3.13 for packaging commands
```

Not needed and must not be set: `EARTHDATA_TOKEN` (ingestion only, never deployed).

## 3. AWS identity and region verification [AWS-READ]

```bash
aws sts get-caller-identity --query '[Account,Arn]' --output text
```

**GATE 1:** `Account` must be exactly `675613597178`. Anything else: stop. Also confirm the region:

```bash
aws configure get region --profile "$AWS_PROFILE"    # ap-south-1 (or rely on AWS_REGION above)
```

The stack refuses to deploy to another account or region once the CLI resolves the identity (tested both ways, Phase 5), but verify first anyway.
Do not print the ARN into shared channels if it contains a username the owner considers sensitive.

## 4. Lambda package build [LOCAL, needs network for pip wheels]

```bash
.venv/Scripts/python.exe scripts/package_lambda.py
```

Expect: `lambda package: ...\infrastructure\build\lambda  ~98.6 MiB unzipped  regions=['chennai']`, exit 0. The script checks the `pydantic_core` module is Linux aarch64 cp313, removes Windows `bin/*.exe`, and refuses geo libraries.
Limits [V]: 250 MB unzipped, 50 MB zipped for direct upload; 6 MB synchronous response (largest observed 1.74 MB).
Optional extra checks done in Phase 6 and reproducible: ELF header of `pydantic_core/*.so` has `e_machine` 183 (AArch64); `diff` of `floodlens_api` against source; artifact tree hash equals `data/api`.
**Not possible locally (P6-07):** executing the arm64 module. First execution will be in AWS (section 14).

## 5. Frontend production build [LOCAL]

```bash
cd apps/web && npm ci && npx tsc --noEmit && npx vitest run && npx vite build && cd ../..
```

`apps/web/dist` must contain `index.html` and `assets/` only. Confirm there is **no key** in the bundle:
`grep -rIE "AKIA|v1\.public\.|VITE_LOCATION_API_KEY=" apps/web/dist || echo clean` (the string `VITE_LOCATION_API_KEY` alone appears as a config key name and is fine).
`.env` files must not exist in `apps/web` (only `.env.example`).

## 6. Synthesis [LOCAL]

Use explicit settings (decisions from SECURITY_OPERATIONS.md; owner to confirm values):

```bash
cd infrastructure && npm ci && npm test
# EXPIRY: must be <= 90 days from today. Compute it; do not copy this example.
EXPIRY=$(date -u -d "+85 days" +%Y-%m-%dT%H:%M:%SZ)
npx cdk synth --quiet \
  -c alarmEmail=<owner address> \
  -c locationKeyExpireTime=$EXPIRY
# both -c values are REQUIRED since Phase 7 (synth aborts without them); apiRateLimit/apiBurstLimit no longer exist
# add -c reservedConcurrency=N ONLY after checking Service Quotas (section 7)
```

`cdk synth` with a live profile performs a read-only identity lookup; that is expected. Pass criteria: exit 0, 37 resources, no warnings about missing context.
Since Phase 7 a missing, past, malformed or more-than-90-days expiry (and a missing or malformed alarm e-mail) aborts the synth (P6-06 resolved).

## 7. Quota check [AWS-READ]

```bash
aws service-quotas get-service-quota --service-code lambda --quota-code L-B99A9384 --query 'Quota.Value'
```

(`L-B99A9384` is the Lambda "Concurrent executions" quota code **[U: confirm in the console if the call errors]**.) If the value is below 100, **do not** pass `reservedConcurrency` (a reservation must leave unreserved capacity, F-05).
Also check CloudFront/Lambda quotas only if a deployment error mentions them.

## 8. Diff [AWS-WRITE (minor): see the correction]

**Correction (Phase 8).** Earlier versions of this runbook called `cdk diff` read-only. It is not: the default `--method=auto` publishes the synthesized template to the CDK staging bucket
(`cdk-hnb659fds-assets-<account>-<region>`) and creates and then deletes a CloudFormation change set (for a stack that does not exist yet it briefly creates a `REVIEW_IN_PROGRESS` stack record).
This happened once in Phase 8 (see PHASE8_RELEASE_AUDIT.md, incident I-1). **Do not run `cdk diff` for this project.** `--method=template` is documented to compare templates directly using the lookup role, but whether it also publishes assets is [U] and has not been tested. The stack does not exist yet, so a diff would list everything as new anyway. Review the output of `cdk synth` (local, offline) instead.

On a first deployment everything is new. Read specifically for: IAM statements with `Resource: *`, anything `AWS::EC2::*` (should be none), `AWS::WAFv2::*`, KMS, NAT, Secrets Manager (should be none), `DeletionPolicy`, and the Location key `NoExpiry`/`ExpireTime`.

## 9. Cost and security review [LOCAL, owner]

Checklist, owner signs off in writing:

- [ ] Budgets B1/B2 and Cost Anomaly Detection exist (SECURITY_OPERATIONS.md 1.2-1.3). They are created by the owner in the console or CLI; this runbook does not create them.
- [ ] Alarm email address is correct and the owner can confirm the SNS subscription.
- [ ] Key expiry is as intended and a reminder exists to rotate before it lapses.
- [ ] P6-05 and P6-08 are implemented (Function URL + OAC; HEAD) but **unverified in AWS**: the post-deployment gate (POST_DEPLOYMENT_CHECKLIST.md section H) must confirm the direct Function URL is rejected and CloudFront can reach the function.
- [ ] The owner understands there is no stage throttle: protections are the optional `reservedConcurrency`, the `LambdaTrafficSpike` alarm and CloudFront caching.
- [ ] Rollback and key-revocation procedures (sections 15-16) have been read.
- [ ] Expected monthly cost range accepted: $8.53 base, $3.65-$20.69 range at 2,000 sessions (COST_MODEL.md).

**GATE 2 - bootstrap approval (separate).** Bootstrap creates AWS resources (the CDK toolkit stack, an S3 assets bucket, an ECR repository, and IAM roles). Check first whether it is already done:

```bash
aws cloudformation describe-stacks --stack-name CDKToolkit --region ap-south-1 --query 'Stacks[0].StackStatus' --output text   # [AWS-READ]
```

Only if absent and the owner approves:

```bash
npx cdk bootstrap aws://675613597178/ap-south-1                # [AWS-WRITE] - owner approval required
```

**GATE 3 - deployment approval (separate from bootstrap).** Owner states the commit hash from section 1, the flags from section 6, and that the diff was read. Then:

```bash
npx cdk deploy FloodLens <same -c flags> --require-approval broadening     # [AWS-WRITE] - owner approval required
```

(The stack id is `FloodLens`.) Record the outputs `SiteUrl`, `ApiEndpoint`, `SiteBucketName`, `MapsApiKeyArn`. First deploy takes several minutes (CloudFront propagation).

## 10. Post-deployment smoke tests [read-only HTTP]

```bash
SITE=https://<SiteUrl host>; API=<ApiEndpoint>; B=$SITE/v1/regions/chennai
curl -sS -o /dev/null -w "%{http_code}\n" $SITE/                       # 200
curl -sS $SITE/v1/health                                               # {"status":"ok"...}
curl -sS -o /dev/null -w "%{http_code}\n" $B/scenarios                 # 200
curl -sS -o /dev/null -w "%{http_code} %{content_type}\n" "$B/water?bbox=70,5,90,25"   # 422 application/json, not HTML
curl -sS $SITE/config.json | .venv/Scripts/python.exe -c "import sys,json; d=json.load(sys.stdin); print(sorted(d))"   # keys only; never print values into tickets
```

Then follow [POST_DEPLOYMENT_CHECKLIST.md](POST_DEPLOYMENT_CHECKLIST.md) section A-F; it is the master list and records each result. A failed item becomes a finding; it is not waved through.

## 11. CloudFront and CSP validation

Checklist section A (headers, SPA fallback, cache behaviour, compression) and section D (CSP in a real browser console with DevTools open). Specific expectations from the synthesized stack:

* `content-security-policy` includes `connect-src 'self' https://maps.geo.ap-south-1.amazonaws.com`, `worker-src 'self' blob:`, `script-src 'self'`, no `unsafe-eval`.
* Zero `Refused to ...` messages. If one appears, note the directive and host **before** widening anything. Candidate to watch **[A]**: `img-src` does not list the maps host; MapLibre normally loads tile/sprite images through `fetch`/`createImageBitmap` (covered by `connect-src` and `blob:`), but a fallback path would need `img-src`.
* `x-cache` on `/config.json` is never `Hit from cloudfront`.

## 12. API validation

Checklist sections A, B, C. Add: compare three response bodies from `$B/...` with the same requests made locally (`curl localhost:8000` against the local API) for equality of JSON structure (timestamps `as_of_utc` differ by design).
Confirm the direct URL behaviour you decided on in P6-05 (`curl -sS -o /dev/null -w "%{http_code}\n" $API/v1/health`: `200` if option 0; `403` if the Function-URL option was built).

## 13. Map rendering validation

Open `$SITE` in Chrome: Monochrome basemap draws under the data; tile requests are `200` from `maps.geo.ap-south-1.amazonaws.com`; zoom to street level and confirm roads, facilities and water layers; block the maps host in DevTools to see the "Basemap unavailable" banner with data intact. Take screenshots into `reports/` only if they show no key (the key appears in the Network panel URLs; crop it out).

## 14. Lambda first-run check (closes P6-07)

CloudWatch Logs for the API function: first invocation has `Init Duration` and no `Runtime.ImportModuleError`. If an import error mentions `pydantic_core` or `_pydantic_core`, the arm64 package is wrong: go to rollback (section 15). Record init duration and `Max Memory Used` in COST_MODEL.md.

## 15. Rollback strategy

| Situation | Action | Notes |
|---|---|---|
| `cdk deploy` fails in the first deployment | CloudFormation rolls back and removes created resources. | **[U]** The new Location key may refuse deletion during rollback (the CLI model says a key must have been deactivated for 90 days before `DeleteKey`; whether CloudFormation uses force-delete is unverified, P6-09). Be prepared for a `ROLLBACK_FAILED`/`DELETE_FAILED` state that needs manual handling with the owner. |
| Deploy succeeded, app broken, data fine | Redeploy the previous good commit (`git checkout <hash>` in a clean worktree, rebuild package and web, `cdk deploy`). | Stack updates are atomic per CloudFormation; a failed update rolls back automatically. |
| Bad API behaviour only | Lower throttle or redeploy; Lambda versions are not used, so there is no alias flip. | Add a published version/alias if rollback speed becomes important [A]. |
| Site bucket contents wrong | Re-run the deploy; bucket is versioned (noncurrent versions kept 30 days) and `RETAIN`. | The bucket is not deleted by `cdk destroy`. |
| Cost or abuse incident | Section 16 first; then throttle. | |
| Full teardown wanted | **Do not run `cdk destroy` without a separate approval.** | Retained: site bucket and two log groups. The Location key cleanup may fail as above. |

Keep the previous deployment's commit hash, flags and `diff.txt` in the deployment log so a rollback is a rebuild, not an investigation.

## 16. Key revocation procedure

See SECURITY_OPERATIONS.md 2.5. Summary: update the key's expiry or restrictions with `--force-update` (owner-run, **[AWS-WRITE]**), confirm the map degrades gracefully, create a replacement by rotation, record the CloudFormation drift. Deleting a key is not an emergency tool.

## 17. Deployment log (fill in)

Date, operator, commit hash, flags, diff reviewed by, GATE 1/2/3 approvals with time, outputs, smoke-test results, anomalies.
