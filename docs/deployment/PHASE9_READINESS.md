# Phase 9 readiness: deployment risk resolution and final release preparation

Written for: the project owner. 2026-10-30. Phase 9 prepared documents and ran local checks only. **Not bootstrapped, deployed, uploaded to, committed, pushed or changed in AWS.** Read-only AWS calls were used (identity, quota, budgets, S3 head/get/list, IAM get/list, CloudFormation list). Labels: **[V]** verified · **[S]** synthesized template only · **[A]** assumption · **[U]** unknown · **[AWS]** needs the deployed stack.

**Verdict: READY FOR OWNER ACTIONS. NOT READY FOR DEPLOYMENT APPROVAL** until the items in section 12 are done: nothing is blocked technically, but the tree is uncommitted, no clean bundle exists, the Lambda quota is unchanged, no budgets exist, and the alarm e-mail and key expiry have not been supplied.

## 1. Accidental `cdk diff` activity (incident I-1)

**What happened.** In Phase 8 I ran `cdk diff` believing it read-only. Its default method (`auto`) published the synthesized template to the CDK staging bucket and created, then deleted, a CloudFormation stack record while computing a change set.

**What remains** (read-only inspection this phase, 2026-10-30):

| Item | State |
|---|---|
| S3 object | bucket `cdk-hnb659fds-assets-675613597178-ap-south-1`, key `9f43afec267d276c10c46092878a64e5212dc7ed7c7fdbb422df8a547ff6ce19.json`, **36,965 bytes**, last modified 2026-10-30T11:26:08Z, current version `PY.uLGgPaZvMkvMaI9t93Bf7vpOxkSzk`, server-side encrypted with a KMS key (`aws:kms`), no tags [V] |
| Bucket | versioning **Enabled**; lifecycle rules: noncurrent versions expire after 30 days; incomplete uploads abort after 1 day [V]. Nothing expires the *current* version |
| Stack record | `FloodLens`: `DELETE_COMPLETE` (created 11:26:07Z, deleted 11:26:15Z). No live stack, no change set [V] |

**Is it referenced?** No live stack references it: `FloodLens` is deleted and no other stack has that name. The key is the SHA-256 of the file's content (I downloaded a copy and the hash equals the key [V]), so another project could only point at it if it produced a byte-identical template, which cannot happen. A future FloodLens deployment uses real e-mail and expiry values, so its template hashes differently and does not reuse it [A]. CDK staging objects are inert files; nothing reads them unless a stack template URL names them. I could not enumerate every stack's template URL read-only, so "referenced by nothing" rests on the content-hash argument [A].

**Does it contain sensitive values?** No, by scan of the downloaded copy (kept outside the repository): no access-key patterns, no private keys, no passwords or secrets. Matches: the placeholder address `owner@example.invalid` (twice, SNS subscription) and three `ServiceToken` fields (custom-resource Lambda references, not credentials). It does contain the account ID and resource logical names, which are not secrets [V].

**Why it is not deleted.** Deleting it is itself a change to AWS (restriction of this phase) and the owner told me not to. It is also harmless: about 37 KB, negligible cost, no secret, no reference.

**Recommended cleanup, owner-approved only (not run):**

1. Decide whether to keep it. The simplest correct choice is to **leave it**.
2. If you want it gone, delete the *specific version* (a plain delete only adds a delete marker and keeps the data):
   `aws s3api delete-object --bucket cdk-hnb659fds-assets-675613597178-ap-south-1 --key 9f43afec267d276c10c46092878a64e5212dc7ed7c7fdbb422df8a547ff6ce19.json --version-id PY.uLGgPaZvMkvMaI9t93Bf7vpOxkSzk`
3. Re-list to confirm: `aws s3api list-object-versions --bucket <bucket> --prefix 9f43afec`.
4. Do it after the first real deployment, not before, so it cannot be confused with the deployment's own assets. The deployment's uploads are content-hashed under the same bucket; never bulk-delete the bucket (other projects use it).

**Documentation corrected.** `cdk diff` is no longer recommended anywhere: `DEPLOYMENT_RUNBOOK.md` section 8 (rewritten: use `cdk synth` and read the template), `DEPLOYMENT.md`, `PRE_DEPLOYMENT_REVIEW.md`, `SECURITY_OPERATIONS.md`, `DEVELOPMENT.md`, `DEPLOYMENT_APPROVAL_PACKAGE.md`. The `npm run diff` script in `infrastructure/package.json` now prints a refusal and exits 1. The remaining mentions of `cdk diff` (PHASE8 audit, PROJECT_STATUS, phase8-audit.json) are the incident record. `cdk diff --method=template` is documented to use the lookup role and compare templates directly, but I did not test whether it publishes anything [U], so it is not recommended either.

## 2. Lambda concurrency

Full plan: [LAMBDA_QUOTA_PLAN.md](LAMBDA_QUOTA_PLAN.md). Summary: account quota **10**, unreserved 10, no function reserved, no pending request [V]. A reservation needs `quota - reservations >= 100` [A], so **even `reservedConcurrency=10` would be rejected today**. Recommendation: request **1000**, then reserve 10 (FloodLens guaranteed and capped at 10 simultaneous executions; other projects keep 990). When exhausted: HTTP 429 from the Function URL, a `Throttles` alarm (already in the stack [S]). Not submitted; not assigned; `reservedConcurrency` still unset.

## 3. Cost protection

Full steps: [AWS_COST_PROTECTION_SETUP.md](AWS_COST_PROTECTION_SETUP.md). Account-wide $25 and FloodLens-specific Amazon Location $15 budgets with 50/80/100% actual and 80/100% forecast alerts; Cost Anomaly monitor exists but its subscription is unverified [U]. **Budgets are not caps.** No budget, alarm or subscription was created. Four other budgets already exist [V].

## 4. Key lifecycle and alarm configuration

| Requirement | Status |
|---|---|
| Expiry required | yes: the CDK app aborts without `locationKeyExpireTime` [S, tests] |
| At most 90 days | yes: `MAX_KEY_LIFETIME_DAYS = 90`; rejects later dates (test: "rejects malformed, impossible, expired and too-distant expiries"; accepts exactly the maximum) [V] |
| Invalid dates rejected | yes: ISO-8601 with a time zone required; impossible calendar dates (for example 30 February, which JavaScript's `Date` would silently roll over) are rejected by an explicit check; past dates rejected [V] |
| Alarm e-mail | required and validated; the owner supplies it; no default and I did not invent one [V] |
| SNS confirmation | the subscription is always created and stays `PendingConfirmation` until the recipient clicks the link; unconfirmed means **no alarm e-mail is ever delivered** (gate 15) [S, AWS] |
| Rotation / extension | same key name: redeploy with a new expiry updates the same key in place (extends it; value unchanged). New key value: rename the key (replacement). The old key cannot be deleted for 90 days after deactivation [V: CLI model]; whether CloudFormation cleanup handles that is [U]. `SECURITY_OPERATIONS.md` 2.3 |
| Emergency restriction | `SECURITY_OPERATIONS.md` 2.5 and `POST_DEPLOYMENT_VERIFICATION.md` (Phase 9 table): update expiry or referer restriction with `--force-update`; do not delete |
| Reminder | rotate by day 75-80; set a calendar entry at deployment time (no automation exists) |

No key was created, rotated or modified (`list-keys` returned none [V]).

## 5. Git state

Branch `main` at `d2c67a2` (only commit, equals `origin/main`); uncommitted Phase 6-9 work; see [RELEASE_COMMIT_CHECKLIST.md](RELEASE_COMMIT_CHECKLIST.md) for the file-by-file inventory by phase, exclusions and the commit checklist. Protected paths unchanged [V]. Nothing was staged or committed.

New finding fixed in Phase 9: the bundle tool refuses a dirty tree and counts **untracked** files, and `reports/releases/2026-10-30-rc1/*` were untracked, so the planned "clean" bundle would have been refused. `.gitignore` now ignores `reports/releases/` entirely.

## 6. Release bundle

RC1 is untouched and remains a dirty-tree **draft**; it will not verify against the Phase 9 tree (source files changed) and is superseded. The clean sequence (commit, clean tree, record the hash, new ID, create without `--allow-dirty`, verify, extract-and-compare the archive, checksums, copy off-machine, record the location) is in the checklist, section 4. Not executed.

## 7. Local regression (all executed 2026-10-30, offline)

| Check | Result |
|---|---|
| Geospatial tests | **174 passed**, 0 failed, 0 skipped |
| API tests | **189 passed, 1 skipped** (a case-sensitive-filesystem test, skipped on Windows), **2 warnings** (Starlette `httpx` TestClient deprecation; Mangum `asyncio.get_event_loop` deprecation) |
| Frontend tests | **83 passed** (8 files), 0 skipped |
| Infrastructure tests | **32 passed** |
| Ruff (`geospatial scripts services`) | All checks passed |
| TypeScript (web, infrastructure) | no errors |
| Production web build | succeeded |
| Schema drift | "schemas match models" |
| Manifest validation | 15 of 15 `ok` |
| Data baseline | **UNCHANGED** (135 raw/processed + 630 `data/api` files) |
| Release integrity | exit 0; 1 LOW finding (CRLF in `schemas/`, hashes compared after normalisation); report written to the scratchpad, not the tracked file |
| Secret scan | 216 non-ignored files scanned for AWS keys, private keys, Location keys, Earthdata/GitHub tokens, JWT-shaped strings: **0 hits** |
| CDK synth (offline, placeholder e-mail and expiry) | succeeded; **37 resources, 0 API Gateway** |
| Warnings seen | `cdk`: "80 feature flags are not configured" (informational notice) |

Not run: browser tests, any test against AWS, the cost model, `cdk diff`/`deploy`/`bootstrap`. Commands were chosen to be local only: the synth ran with the credential files disabled.

## 8. IAM

[DEPLOYMENT_IAM_REQUIREMENTS.md](DEPLOYMENT_IAM_REQUIREMENTS.md). Key points: the user `AI-agent` has AdministratorAccess; the bootstrap `cfn-exec-role` also has AdministratorAccess and the deploy role can pass only that role, so the stack runs as an administrator regardless of who deploys [V]. Recommend a dedicated MFA-protected or short-lived deployer that can only assume the three bootstrap roles, and avoiding long-term admin keys. Nothing was created.

## 9. Protected-data integrity

`git diff HEAD` touches no raw, processed or API data, manifest, frozen schema, fixture or exposure code [V]. Rainfall scoring and exposure outputs unchanged; observed rainfall is not in exposure scoring; no `randomError` in application logic (the repo guard test passes). No Docker requirement added.

## 10. Remaining risks

| ID | Severity | Risk |
|---|---|---|
| P9-01 | HIGH | Lambda quota 10: no reservation possible; shared with other projects (P8-01, unchanged until the owner requests an increase) |
| P9-02 | HIGH | Tile spend is uncapped; budgets only notify (P8-02) |
| P9-03 | MEDIUM | Uncommitted tree, no clean bundle (P8-03); rc1 draft superseded |
| P9-04 | MEDIUM | Stack executes as administrator via the shared `cfn-exec-role`; deployer is a long-term admin user |
| P9-05 | MEDIUM | Function URL/OAC, CSP, tiles, alarms, ARM64 and 429 behaviour unverified in AWS (`POST_DEPLOYMENT_VERIFICATION.md`) |
| P9-06 | MEDIUM | Anomaly subscription and the Location budget's service-name filter unconfirmed [U] |
| P9-07 | LOW | Historical sections of `SECURITY_OPERATIONS.md`/`RELEASE_READINESS.md` still describe the removed API Gateway under a "superseded" banner |
| P9-08 | LOW | I-1 object remains (inert, 37 KB) |
| P9-09 | LOW | Line-ending handling (CRLF) could change source hashes across OSes [A] |
| P9-10 | INFORMATIONAL | Bootstrap v32 present, no rerun |

No BLOCKER.

## 11. What I changed in Phase 9

Documents: this file, `LAMBDA_QUOTA_PLAN.md`, `AWS_COST_PROTECTION_SETUP.md`, `DEPLOYMENT_IAM_REQUIREMENTS.md`, `RELEASE_COMMIT_CHECKLIST.md`; extended `POST_DEPLOYMENT_VERIFICATION.md`; corrected `cdk diff` guidance in six documents. Code/config: `.gitignore` (ignore `reports/releases/`) and the `diff` npm script. Nothing else in source, data or AWS.

## 12. Exact owner actions, in order

| # | Action | Mine/Owner | Notes |
|---|---|---|---|
| 1 | Review Phases 6-9 diff; commit (and push if you choose) | owner | `RELEASE_COMMIT_CHECKLIST.md` section 3 |
| 2 | Request Lambda quota 1000 in Service Quotas; wait for approval | owner | `LAMBDA_QUOTA_PLAN.md` section 3 |
| 3 | Create the $25 account and $15 Location budgets; confirm the Billing service name; confirm the Anomaly Detection subscription | owner | `AWS_COST_PROTECTION_SETUP.md` |
| 4 | Choose the alarm e-mail and an expiry at most 90 days ahead (suggest 85); set a day-75 reminder | owner | I invented neither |
| 5 | Decide the deployer identity (short-lived, MFA, bootstrap-role-only) | owner | `DEPLOYMENT_IAM_REQUIREMENTS.md` |
| 6 | Ask me to generate the clean bundle, or run the sequence yourself; copy it off the laptop and record the location | owner / me on request | `RELEASE_COMMIT_CHECKLIST.md` section 4 |
| 7 | Decide the I-1 object (leave it, or delete the one version) | owner | section 1 |
| 8 | Send written deployment approval naming commit, bundle ID, e-mail, expiry and whether `reservedConcurrency=10` is included | owner | bootstrap: **not required** |
| 9 | After deployment, run `POST_DEPLOYMENT_VERIFICATION.md` in order | whoever deploys | |

## 13. Ready for deployment approval?

**No.** Items 1-5 and 8 are open. When 1-6 are done and the answers are in hand, the project can be re-reviewed for deployment approval; a clean bundle and the quota decision are the gating items. **Everything marked [AWS] remains unverified until a deployment exists.**
