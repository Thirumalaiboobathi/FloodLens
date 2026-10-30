# Release commit checklist and clean-bundle sequence

Written for: the project owner. 2026-10-30. **Nothing was committed, staged, pushed or rewritten.** The tree is `main` at `d2c67a2` (the only commit, equal to `origin/main`) plus uncommitted Phase 6-9 work. Labels: **[V]** verified here · **[A]** assumption.

## 1. Inventory of changes since the first commit

`git status`: 18 modified, 21 untracked (39 entries; 527 insertions and 130 deletions across the modified files, not counting the untracked ones). Nothing is staged. Git cannot tell the phases apart because there is only one commit, so the phase column is attributed from this project's session records [A].

### Modified (18)

| File | Phase | Purpose |
|---|---|---|
| `.gitignore` | 7, 9 | ignore `reports/releases/` (Phase 9: the whole folder, so bundle files never make the tree dirty) |
| `Makefile` | 7 | `synth` takes `ALARM_EMAIL`/`KEY_EXPIRE`; `release-bundle` and `verify-release` targets |
| `PROJECT_STATUS.md`, `TASKS.md` | 6-9 | status logs |
| `docs/API.md` | 7 | HEAD support, Function URL |
| `docs/DEVELOPMENT.md` | 9 | `cdk diff` no longer recommended |
| `docs/architecture/ARCHITECTURE.md` | 7 | new request path |
| `docs/deployment/COST_MODEL.md` | 7 | API Gateway removed |
| `docs/deployment/DEPLOYMENT.md` | 6, 7, 9 | procedure; `cdk diff` removed in 9 |
| `docs/deployment/POST_DEPLOYMENT_CHECKLIST.md` | 6, 7 | gate H added |
| `docs/deployment/PRE_DEPLOYMENT_REVIEW.md` | 6, 7, 9 | Phase 7 notes; `cdk diff` corrected in 9 |
| `infrastructure/bin/floodlens.ts` | 7 | required context, removed throttle settings abort |
| `infrastructure/lib/floodlens-stack.ts` | 7 | Function URL, OAC, mandatory expiry/e-mail |
| `infrastructure/package.json` | 9 | the `diff` npm script now refuses to run |
| `infrastructure/test/floodlens-stack.test.ts` | 7 | 32 tests |
| `scripts/release_integrity.py` | 6 | integrity audit |
| `services/api/floodlens_api/lambda_handler.py` | 7 | docstring only |
| `services/api/floodlens_api/main.py` | 7 | explicit HEAD support |

### Untracked, to be committed (21)

| File | Phase |
|---|---|
| `scripts/create_release_bundle.py`, `scripts/data_baseline.py` | 7, 6 |
| `services/api/tests/test_head_and_function_url.py`, `services/api/tests/test_release_bundle.py` | 7 |
| `docs/deployment/DEPLOYMENT_RUNBOOK.md`, `RELEASE_READINESS.md`, `SECURITY_OPERATIONS.md` | 6 (7-9 edits) |
| `docs/deployment/RELEASE_ARTIFACTS.md`, `PHASE7_IMPLEMENTATION_REPORT.md` | 7 |
| `docs/deployment/PHASE8_RELEASE_AUDIT.md`, `BOOTSTRAP_APPROVAL_PACKAGE.md`, `DEPLOYMENT_APPROVAL_PACKAGE.md`, `POST_DEPLOYMENT_VERIFICATION.md` | 8 (verification file extended in 9) |
| `docs/deployment/PHASE9_READINESS.md`, `AWS_COST_PROTECTION_SETUP.md`, `LAMBDA_QUOTA_PLAN.md`, `DEPLOYMENT_IAM_REQUIREMENTS.md`, `RELEASE_COMMIT_CHECKLIST.md` | 9 |
| `reports/pre-deployment/phase6-git-baseline.json` | 6 |
| `reports/pre-deployment/phase8-audit.json`, `phase9-audit.json` | 8, 9 |

(Grouped rows: 2 scripts, 2 tests, 14 documents, 3 reports = 21. `git status -uall` is authoritative.)

### Generated and temporary

| Item | Tracked? | Action |
|---|---|---|
| `reports/releases/2026-10-30-rc1/` (manifest, checksums, validation, 13 MB archive) | **ignored** since Phase 9 | **Do not commit.** A draft from a dirty tree; superseded. Keep it until the clean bundle exists, then archive or delete as you wish |
| `infrastructure/build/`, `infrastructure/cdk.out/`, `apps/web/dist/`, `data/api/`, `data/raw`, `data/processed`, `.venv/` | ignored [V] | not in the commit |
| `reports/pre-deployment/release-integrity.json` | tracked, unchanged in Phase 9 (the audit was written elsewhere) | leave |
| Scratch scripts and logs | outside the repository (session scratchpad) | nothing to exclude |

### Files that should not be in the commit

None of the 39 entries needs excluding. Check before `git add` that you do not stage: `*.tar.gz`, `cdk.out/`, `.env*`, anything under `data/`. `git add -A` is safe only because those are ignored; a path-by-path `git add` is safer.

## 2. Protected files [V]

* `git diff --name-only HEAD` lists **no** path under `data/`, `geospatial/`, `schemas/`, test fixtures or any manifest. The 135 raw/processed files and 630 `data/api` files hash to the recorded baseline (`scripts/data_baseline.py --verify`: **UNCHANGED**).
* Exposure code and rainfall scoring untouched (`geospatial/` has no diff).
* Line endings: Git prints "LF will be replaced by CRLF" for several files (Windows `autocrlf`). Commit as is; the integrity audit already normalises CRLF for `schemas/` (a LOW finding). [A] If you ever build the release on another OS, the `source_tree` hash may differ unless line endings are normalised the same way.

## 3. Commit-readiness checklist (owner)

| # | Step | Command / check |
|---|---|---|
| 1 | Read the Phase 8 audit, the approval packages and this checklist | `docs/deployment/` |
| 2 | Re-run the local tests you care about | `PHASE9_READINESS.md` section 7 lists the counts I obtained |
| 3 | Inspect the diff | `git diff HEAD --stat` and `git status -uall` |
| 4 | Confirm no secrets | `git diff HEAD | grep -nE "AKIA|ASIA|PRIVATE KEY|v1\.public\."` returns nothing (my scan of 216 files found none) |
| 5 | Confirm protected paths untouched | `git diff --name-only HEAD -- data geospatial schemas` returns nothing |
| 6 | Stage explicitly | `git add <files>`; `git status` shows only intended paths |
| 7 | Commit (suggested message below) | `git commit` |
| 8 | Push when you decide | your choice; the bundle does not need a push, but keep `origin/main` equal to what you deploy |

Suggested message (adapt freely): `Phases 6-9: Function URL + OAC architecture, explicit HEAD, mandatory key expiry and alarm e-mail, release bundle tooling, deployment approval documents`.

## 4. Clean release-bundle sequence (do not regenerate rc1)

Run only after the commit. No step uploads to AWS.

| # | Step | Command | Expected |
|---|---|---|---|
| 1 | Review and commit (section 3) | | |
| 2 | Clean tree | `git status --porcelain` | **no output** (the `reports/releases/` ignore rule is what makes this true with rc1 still on disk) |
| 3 | Record the commit | `git rev-parse HEAD > ../release-commit.txt` (or note it) | 40-character hash |
| 4 | New unique ID | e.g. `2026-11-02-rc2` (date plus counter; must not exist) | the tool refuses to overwrite |
| 5 | Rebuild inputs from the commit | `.venv/Scripts/python.exe scripts/package_lambda.py` then `(cd apps/web && npm ci && npm run build)` | Lambda package and web build |
| 6 | Create without `--allow-dirty` | `.venv/Scripts/python.exe scripts/create_release_bundle.py create --release-id <id> --test-results <tests.json>` | `commit <hash> (clean)`; refuses if dirty |
| 7 | Verify | `.venv/Scripts/python.exe scripts/create_release_bundle.py verify --release-id <id>` | `OK` (15 checks) |
| 8 | Check the archive independently | extract `data-api.tar.gz` into a new empty folder; the file list must equal `data/api` (630 files, `.json` and `.tif` only) | as in Phase 8 |
| 9 | Check every checksum | `cd reports/releases/<id> && sha256sum -c checksums.sha256` (or rely on `verify`, which does it) | all `OK` |
| 10 | Copy the whole folder off the laptop | to storage you choose (not public: it holds the published artifacts) | record the location and the archive SHA-256 |
| 11 | Record | commit hash, release ID, archive SHA-256, storage location in `DEPLOYMENT_RUNBOOK.md` section 17 | |

**Linking bundle to commit and data:** `release-manifest.json` stores the exact commit (`git.commit`), the clean-tree flag, and listing hashes for the source tree, dataset manifests (15), `data/api` (630 files), the Lambda package and the web build. The data inventory is the same one `data_baseline.py` checks. If the manifest says `working_tree_clean: true` and `git.commit` equals the deployed commit, the bundle identifies the source.

Do not edit tracked files between steps 2 and 6: any change makes the tree dirty (and changes the `source_tree` hash). Paths excluded from `source_tree`: `docs/`, `reports/` and top-level `*.md`.

## 5. Approval gates this checklist does not cover

Quota, budgets, alarm e-mail, key expiry, IAM and written deployment approval are in `PHASE9_READINESS.md` section 12 and `DEPLOYMENT_APPROVAL_PACKAGE.md`.
