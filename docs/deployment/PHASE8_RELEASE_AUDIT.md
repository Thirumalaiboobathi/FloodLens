# FloodLens Phase 8: release candidate audit

Written for: the project owner, to decide whether to approve a deployment. 2026-10-30. Machine-readable companion: [phase8-audit.json](../../reports/pre-deployment/phase8-audit.json).
Labels: **[V]** verified by something executed in this phase · **[S]** synthesized template only · **[A]** assumption · **[U]** unknown · **[AWS]** needs a deployment to verify.

**Nothing was bootstrapped, deployed, committed or pushed. Protected data, manifests, frozen schemas and exposure code are unchanged. One unintended AWS side effect occurred; read incident I-1 first.**

## Incident I-1: `cdk diff` wrote to AWS (disclosed, not reversed)

While preparing the deployment package I ran `cdk diff` believing it to be read-only (the Phase 6 runbook labelled it that way). It is not. Its output said "Publishing FloodLens Template" and "create a read-only change set". Verified afterwards with read-only calls:

* **One object uploaded** to the existing CDK staging bucket `cdk-hnb659fds-assets-675613597178-ap-south-1`: key `9f43afec267d276c10c46092878a64e5212dc7ed7c7fdbb422df8a547ff6ce19.json`, 36,965 bytes, 2026-10-30T11:26:08Z. It is the synthesized CloudFormation template (placeholder alarm e-mail `owner@example.invalid`, expiry 2026-12-29). It contains no credentials, no Lambda package, no web files and no data.
* **A transient stack record** `FloodLens` was created (change-set review state) and deleted by CDK: `DELETE_COMPLETE`, 2026-10-30T11:26:07Z to 11:26:15Z. No `FloodLens` stack exists now (`describe-stacks`: does not exist) and no change set remains.
* No resource was created in the application sense (no bucket, function, distribution, key, topic).

This violates the Phase 8 rule "do not upload anything to AWS". I did **not** delete the object (that would be another AWS change). Impact is negligible (about 37 KB, no secret), but it is your call whether to remove it; the same file name would be reused only if an identical template is deployed. The runbook (section 8) is corrected: `cdk diff` is an AWS-WRITE step; the local-only form is `cdk diff --method=template` [U: confirm it publishes nothing]. All other AWS calls this phase were read-only `describe`/`get`/`list` calls.

## A. Git

| Item | Result [V] |
|---|---|
| Branch / HEAD | `main` / `d2c67a2eb08818bcfcbdf7a81990d08cc4a8ba66` ("first commit"); `origin/main` identical (`git ls-remote`); 1 commit in `--all` |
| Working tree | **Dirty.** 16 modified + 11 untracked paths (counting the `reports/releases/` folder as one), nothing staged |
| Phase 6 / 7 committed? | **No.** Neither is committed; there is no Phase 6 commit |
| Reproducible from the commit? | **No.** The commit contains none of the Phase 6/7 code, tests, docs or the release script. Do not describe `d2c67a2` as the release. |
| Ignored data changed? | No: `data_baseline.py --verify` UNCHANGED (135 raw/processed files, 630 `data/api` files) |
| Protected files changed? | None. `git diff HEAD -- schemas data/fixtures data/manifests geospatial` is empty |

Change inventory (full per-file list in the JSON): application source 2 (`services/api/floodlens_api/main.py`, `lambda_handler.py`); release tooling 3 (`scripts/create_release_bundle.py`, `data_baseline.py`, `release_integrity.py`); tests 3 (`test_head_and_function_url.py`, `test_release_bundle.py`, `floodlens-stack.test.ts`); infrastructure/config 4 (`floodlens-stack.ts`, `bin/floodlens.ts`, `Makefile`, `.gitignore`); documentation 13; generated report 1 (`phase6-git-baseline.json`); deployment artifact 3 files (`reports/releases/2026-10-30-rc1/`; the 13.5 MB archive is git-ignored). Untracked build outputs (`infrastructure/build`, `cdk.out`, `apps/web/dist`) are ignored, not listed.

## B. Release bundle `2026-10-30-rc1`

* `verify` (full, including the 800 MB raw-data hash): **OK**, 1,869 files, source/package/web/manifest/archive all match. Not overwritten. A rebuilt web bundle gave the same hash (the build is deterministic here).
* It was created on a **dirty tree**: it is a *draft* that pins the exact bytes of the current working tree, not a reproducible release. It cannot be regenerated from commit `d2c67a2`.
* Archive `data-api.tar.gz` (git-ignored, 13,464,329 bytes, SHA-256 `c6abb1d0…02c8`): present; hash equals the manifest; **independently extracted** and compared: 630 files, identical to `data/api`, all paths under `data/api/`, only `.json` and `.tif`, secret/credential scan clean.
* Script review: sorted, deterministic ordering; explicit commit and dirty state recorded; refuses symlinks and outside-project paths; rejects duplicate (case-insensitive) paths; fails on missing inputs; never overwrites; excludes `.env*`, keys, `.venv`, `node_modules`; archive verified byte-for-byte. No change was needed, and none was made (any change to the script would alter the `source_tree` hash of rc1).
* Known limits: zip hash not recorded (zips are not byte-reproducible [U]); pip wheels not hash-pinned (P6-13).
* **To get a clean release candidate:** (1) the owner reviews and commits Phase 6, 7 and 8 files; (2) `git status` must be empty; (3) rebuild `data/api` only if needed (do not; hashes must match), rerun `package_lambda.py` and the web build; (4) `create_release_bundle.py create --release-id 2026-11-xx-rc2` (no `--allow-dirty`); (5) `verify`. Then the bundle's commit hash identifies the source. The assistant does not commit.
* **Durable storage (recommendation, nothing uploaded):** copy `reports/releases/<id>/` (it includes the archive) to at least one location off this laptop that the owner controls (an external drive and a private cloud folder or a private repository release asset). Keep `phase6-git-baseline.json` with it. Do not make it public. A private S3 bucket would be an AWS change needing separate approval.

## C. Infrastructure (fresh synth this phase) [S]

37 resources, `BootstrapVersion` parameter only: S3 bucket + policy; CloudFront distribution, 2 OACs (S3 and Lambda), function, cache policy, response-headers policy; Lambda `ApiFunction` (python3.13, arm64, 1024 MB, 15 s) + role + Function URL + 2 permissions + retained log group; Location API key; SNS topic + e-mail subscription; 4 alarms; dashboard; and CDK helper resources (`ReadMapsKey` custom resource with its Node 24 provider function, role, policy and log group; two `BucketDeployment` custom resources sharing one Python function with 2 AWS CLI layers, role, policy and 2 log groups; CDK metadata).

| Check | Result |
|---|---|
| Function URL | one, `AuthType AWS_IAM`, `InvokeMode BUFFERED`, no CORS [S] |
| OAC (API) | `OriginAccessControlOriginType lambda`, `SigningBehavior always`, `SigningProtocol sigv4`; attached to the single custom origin (HTTPS only, TLS 1.2, read timeout 20 s, domain = Function URL host) [S] |
| Origin request behaviour | no origin request policy and no forwarded headers: viewer `Host` is not sent, which SigV4 to a Function URL needs [A] |
| Lambda permissions | exactly two, principal `cloudfront.amazonaws.com`, `SourceArn` = this distribution; `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction` (`InvokedViaFunctionUrl: true`); no `*` principal, no account-wide statement [S] |
| `/v1/*` | GET, HEAD; cache policy keyed on the 15 API query parameters (TTL 0 / 5 min / 1 h); `/config.json` uncached; default behaviour private S3 via OAC with the SPA rewrite function [S] |
| S3 | block public access, SSE-S3, versioned, TLS-only deny, read allowed only to the CloudFront service principal with a `SourceArn` condition, `Retain` [S] |
| Response headers | HSTS 1 year, nosniff, frame DENY, referrer policy, CSP (`script-src 'self'`, connect/font to `maps.geo.ap-south-1.amazonaws.com`, `frame-ancestors 'none'`) [S]; browser behaviour [AWS] |
| Removed | zero `AWS::ApiGateway*` resources, no `execute-api` string, no throttle settings, no `ApiEndpoint` output [S] |
| IAM | API role: logging only. `ReadMapsKey`: `geo:DescribeKey` on the one key. Bucket-deployment role: S3 on the CDK assets bucket and the site bucket, and `cloudfront:CreateInvalidation`/`GetInvalidation` on `*` (a CDK default, deploy-time only). Only one `Resource: *` statement. |
| Frontend | no AWS signing code or keys in `apps/web/src` (test-enforced); `config.json` gets one value, the public map key [S] |
| Retention | site bucket and API log group `Retain`; helper log groups `Delete` (30-day retention); custom-resource records `Delete` (they hold no data) |
| Unexpected resources | none. The Node 24 function, two layers and extra log groups are CDK helpers for `AwsCustomResource`/`BucketDeployment` |
| `cdk diff` against AWS | 42 additions (36 resources excluding the CDK metadata record, plus the description, the `BootstrapVersion` parameter and 4 outputs), **0 replacements, 0 deletions** (first deployment) [V]; but see I-1 |

Security findings: none new. Residual: the unsigned Function URL refusal and CloudFront's acceptance of the signature are **[AWS]** (gate H in `POST_DEPLOYMENT_VERIFICATION.md`).

## D. API and Function URL correctness [V locally]

* 24 GET operations and 24 matching HEAD operations in `/openapi.json`; no other methods documented; all 24 GET operations byte-identical to the original code (proved in Phase 7).
* API suite (189 passed, 1 skipped) includes: GET vs HEAD parity on 28 URLs (status, headers, content-type, `Content-Length` = GET body length, empty body) across rainfall, water, scenarios, errors; Function URL v2 synthetic events (GET, HEAD, query strings including percent-encoding, missing optional fields, errors with structured bodies, 1.74 MB JSON identical to the direct response and under 6 MB, base64 GeoTIFF byte-identical, missing routes, traversal attempt, 405s, unknown event shape rejected). GeoTIFF HEAD is asserted never to open the file.
* The browser still calls `/v1/...` same-origin (client test and infrastructure test).
* **OPTIONS returns 405 and no CORS headers. Acceptable and unchanged:** the app is same-origin through CloudFront, so the browser sends no preflight; CloudFront allows only GET and HEAD for `/v1/*`, so an OPTIONS request is answered by CloudFront and never reaches the function [A]. Adding CORS would widen the published behaviour for no benefit; not changed.
* Events are synthetic, built from the documented payload format 2.0; real events are **[AWS]**.

## E. AWS account readiness (read-only calls) [V]

| Item | Result |
|---|---|
| Identity | `aws sts get-caller-identity`: account **675613597178** (matches), principal IAM user `AI-agent` (policies include `AdministratorAccess`); default profile (no `AWS_PROFILE`), credentials from the shared credentials file |
| Region | `ap-south-1` (config file) |
| CDK bootstrap | **Already present:** stack `CDKToolkit` `CREATE_COMPLETE` (created 2026-08-20), qualifier `hnb659fds`, bootstrap version **32** (the app needs at least 8), staging bucket and roles exist. **Bootstrap is not required** and re-running it would modify a toolkit stack shared with the account's other projects. |
| FloodLens stack | does not exist (nothing deployed) |
| Existing workloads | The account is shared: stacks `ShopFlowStack`, `AwsDeployDoctorStack`, `billsense`, `aws-sam-cli-managed-default`; 10 Lambda functions; 2 CloudFront distributions; 3 SNS topics; no Location keys |
| Lambda concurrency | **Account quota 10 concurrent executions, 10 unreserved** (Service Quotas `L-B99A9384`, adjustable, no pending request). See below. |
| Function URL / CloudFront / Location Maps in region | Function URLs and CloudFront are standard features [A]; `maps.geo.ap-south-1.amazonaws.com` resolves [V]. Actual creation is **[AWS]**. |
| Budgets | 4 existing budgets for other projects (e.g. "My Monthly Cost Budget" $3). **No FloodLens budget exists.** One Cost Anomaly monitor exists (`Default-Services-Monitor`, dimension SERVICE); its subscriptions could not be read (CLI error) [U]. |
| Key expiry / SNS | no key, no topic exist yet (nothing created) |

### Reserved concurrency (decision: "configurable after quota check")

The quota check says **no reservation is possible today**. Lambda must leave unreserved capacity (a floor of 10 for small quotas, 100 otherwise [A: exact floor]); with a quota of 10 that leaves 0 reservable, and a reservation would fail the deployment. Therefore the deployment package uses **no `reservedConcurrency`** (the stack omits it). Consequences:

1. All functions in the account in this region share 10 concurrent executions. A burst of FloodLens cache misses can throttle the other projects' functions (429 `TooManyRequestsException`), and vice versa. This is a cross-project risk, not only a FloodLens one. Expected load is small and CloudFront caches `/v1` for 5 minutes, but it is not bounded.
2. Ceiling: about 10 / 0.3 s ≈ 33 requests per second of cache misses for the whole account at the measured 150-300 ms duration [A].
3. Throttled requests surface as 429/5xx at CloudFront; the `LambdaThrottles` alarm (threshold 1 per 5 min) will notify.
4. **Recommendation:** the owner requests a Service Quotas increase for Lambda concurrent executions (an AWS change, owner action; a typical new-account target is 1,000, subject to AWS review), then deploys with `-c reservedConcurrency=10` (an adequate pilot cap: 10 × 3.3/s ≈ 33 rps of misses, an order of magnitude above expected load), which also protects the other projects from a FloodLens spike. Re-check the quota before using it.
5. Reserved concurrency limits simultaneous executions, **not** request rate or spend; it is not a spending cap.

## F. Cost and spending [A from the existing model; not re-run]

Assumptions unchanged from `COST_MODEL.md` (Phase 6: base about $8.53 per month at 2,000 sessions, $3.65-$20.69 range; $42.66 at 10,000; $213.30 at 50,000), minus about $0.13 for the removed API Gateway; `cost_model.py` was not re-run. Dominant cost: Amazon Location tiles at $0.04 per 1,000 [V in Phase 5/6].

* **Owner-approved budgets** (to be created by the owner, nothing created): account $25 per month; Amazon Location $15 per month. Alerts at 50%, 80% and 100% of actual spend and a forecast alert at 100% (and 80% forecast optional).
* **The account is shared.** A $25 account-wide budget will also count the other projects' spend (existing budgets show $3-$10 limits), so it can fire for reasons unrelated to FloodLens. Prefer the Location-filtered $15 budget as the FloodLens signal; optionally tag resources `Project=FloodLens` (the stack already does) and activate that cost allocation tag (a billing-setting change, owner decision).
* Cost Anomaly Detection: a service-dimension monitor already exists; add a subscription for the owner's address or confirm one exists (console). Pricing of Anomaly Detection remains unconfirmed [U].
* **Limits, stated plainly:** AWS Budgets and Cost Anomaly Detection **notify; they do not stop spending**, and reporting lags by hours. Lambda reserved concurrency limits simultaneous executions, not spend. **Map-tile spend is separate from Lambda and uncapped** (a public key whose Referer rule only constrains browsers: about $5,184 per month at 50 tiles/s sustained). Mitigations are the key expiry (at most 90 days), the Location budget, a per-key call alarm to be created after first traffic, and the emergency restriction procedure.
* **WAF:** not added (owner decision). A CloudFront WAF would protect requests to the site's own CloudFront distribution only; it would **not** protect direct requests from a browser or script to `maps.geo.ap-south-1.amazonaws.com`, which is where tile spend occurs.

Proposed commands for the owner (nothing was run) are in `DEPLOYMENT_APPROVAL_PACKAGE.md`.

## G. Deployment parameters [V locally]

* `locationKeyExpireTime` required; five validation tests plus a test through the real CDK entrypoint cover missing, malformed, impossible (`2026-02-30`), past, equal-to-now, 90 days exactly (accepted) and 90 days + 1 s (rejected), and time-zone normalization to UTC. `alarmEmail` required and format-checked; missing or invalid fails the synth (tested through the real CDK entrypoint). The SNS e-mail subscription is created. **Unconfirmed subscriptions receive nothing**: the recipient must click the AWS confirmation link; check `PendingConfirmation` after deployment.
* Supplying real values (alarm address, expiry date) is the owner's decision; this audit used placeholders for synth only.
* Emergency restriction and rotation: `SECURITY_OPERATIONS.md` 2.5 and runbook section 16 (update the key's restrictions or expiry with `--force-update`; deletion needs 90 days of deactivation, so it is not an emergency tool; whether a past expiry is accepted is **[U]**). Not performed. Rotation before expiry = redeploy with a later date; whether CloudFormation updates the key in place or replaces it is **[U]**.

## H. ARM64 [V static; runtime NOT verified]

Rechecked against the current package: 1,040 files, 98.6 MiB unzipped (limit 250), one native module `pydantic_core` ELF AArch64 tagged `cpython-313-aarch64-linux-gnu`; no Windows binaries; no GeoPandas/Rasterio/Fiona/Shapely/PyProj/GDAL; handler resolves; code and artifacts equal source; 12 pins match. The development host is AMD64 and no emulator was installed. Classification: **POST_DEPLOYMENT_VERIFICATION_REQUIRED**. ARM64 compatibility is **not proven**. The first-invocation procedure is in `POST_DEPLOYMENT_VERIFICATION.md`.

## I. Verification plan

`POST_DEPLOYMENT_VERIFICATION.md`: 17 checks with exact commands, each labelled locally verified / synthesized / requires deployment / requires monitoring. No production verification is claimed.

## J. Scientific and data integrity [V]

Exposure engine and outputs, raw and processed data, manifests and frozen schemas unchanged (no diff against `HEAD`; data inventory UNCHANGED; 19 schemas equal the model export). Rainfall stays separate from exposure scoring; Final (2023 event) and Late (October 2026) streams remain distinct streams; the IMERG random-error field is absent from API code, frontend and artifacts (bundle check and the geospatial guard test); water responses keep ODbL attribution (water API tests). Release-integrity rerun: schema files and versions identical; 16 GET snapshots identical; only the 24 HEAD operations added.

## K. Tests (all executed in this phase)

| Suite | Passed | Failed | Skipped | Warnings |
|---|---|---|---|---|
| Geospatial pytest | 174 | 0 | 0 | 0 reported |
| API pytest | 189 | 0 | 1 (duplicate-path test needs a case-sensitive filesystem; this host is Windows) | 2 (httpx deprecation in the Starlette TestClient; Mangum `asyncio.get_event_loop` deprecation) |
| Frontend vitest | 83 | 0 | 0 | none reported |
| Infrastructure vitest | 32 | 0 | 0 | 0 |
| Ruff (geospatial, services, scripts) | clean | | | |
| TypeScript (web, infrastructure) | clean | | | |
| Web production build | succeeded | | | |
| Schema drift | "schemas match models" | | | |
| Manifest validation | all ok | | | |
| Release integrity | exit 0, one LOW finding (CRLF checkout, hashes compared LF-normalized) | | | |
| Data baseline | UNCHANGED | | | |
| Secret scan (bundle check) | none found | | | |
| Offline `cdk synth` | succeeded, 37 resources | | | |
| Bundle verify (full) | OK | | | |

A successful synth and passing template tests are **not** proof of deployability. Browser tests were not run.

## L. Findings

| ID | Severity | Finding | Needs owner? |
|---|---|---|---|
| I-1 | LOW (process) | `cdk diff` uploaded a 37 KB template and created a transient stack record; see incident above. | Optional cleanup decision |
| P8-01 | **HIGH** | Lambda concurrency quota is 10, shared account-wide; no reservation is possible; FloodLens and the other projects can throttle each other. | Yes: quota increase request, or accept |
| P8-02 | **HIGH** | Map-tile spend uncapped; public key; budgets only notify (carried from P6-04). | Yes: create budgets, accept |
| P8-03 | MEDIUM | Working tree dirty; Phase 6/7/8 uncommitted; the only bundle is a dirty-tree draft; no clean RC exists. | Yes: commit, then regenerate |
| P8-04 | MEDIUM | Function URL + OAC, unsigned-request refusal, CSP, tiles, alarms unverified in AWS. | Deployment gates |
| P8-05 | MEDIUM | ARM64 runtime unverified (POST_DEPLOYMENT_VERIFICATION_REQUIRED). | Deployment gate |
| P8-06 | MEDIUM | Shared account: the $25 account budget will include other projects' spend; Location filtered budget is the real signal. | Yes |
| P8-07 | MEDIUM | No API throttle or WAF (owner accepted WAF absence); without reserved concurrency the only API limit is the shared quota. | Covered by P8-01 |
| P8-08 | MEDIUM | Key deletion/rotation behaviour in CloudFormation unknown; 90-day expiry means a recurring rotation duty and an expired key blanks the basemap. | Yes: process |
| P8-09 | LOW | Deployer credentials are an IAM user with AdministratorAccess (not least privilege); CDK deploys through the bootstrap roles. | Owner awareness |
| P8-10 | LOW | Cost Anomaly Detection subscriptions unreadable by CLI; confirm in console. | Yes |
| P8-11 | LOW | Wildcard `cloudfront:CreateInvalidation` (CDK default) in the deploy-time role; pip wheels not hash-pinned; Mangum deprecation warning (Python 3.13 fine). | No |
| P8-12 | INFORMATIONAL | CDK bootstrap already exists (v32), so no bootstrap is needed; OPTIONS 405 acceptable; `COST_MODEL.md` figures not re-run. | No |

**No BLOCKER.** P8-01 and P8-03 are the items that stop me recommending deployment approval.

## M. Owner approvals (separate)

1. **Bootstrap:** not required (already present). `BOOTSTRAP_APPROVAL_PACKAGE.md` documents the evidence and the command that would run; the recommendation is to approve nothing.
2. **Deployment:** `DEPLOYMENT_APPROVAL_PACKAGE.md`; approval cannot be recommended until the prerequisites in its section 2 are met.

## N. Recommendation

**READY FOR OWNER REVIEW.** Not "ready for bootstrap approval" (bootstrap is unnecessary) and not "ready for deployment approval": the tree is uncommitted, no clean release candidate exists, the Lambda quota and budgets are unresolved, and the real alarm e-mail and key expiry have not been supplied.
