# FloodLens release artifacts

Written for: the project owner and whoever deploys. Phase 7, 2026-10-30. Nothing here is uploaded anywhere.

## Why this exists

Git tracks the code, but the Lambda package is built from **Git-ignored** inputs: `data/api/` (630 published artifacts, 94 MB), the pip-installed ARM64
wheels, and `apps/web/dist`. Git alone therefore cannot reproduce what would be deployed. A *release bundle* records, with SHA-256 checksums, exactly which
bytes make up a candidate release so that a deployed package can be compared with the intended Git commit and data inventory afterwards.

## Commands (all local; no AWS call, no upload)

```bash
# after: build_api_artifacts.py, npm run build (apps/web), package_lambda.py, tests
.venv/Scripts/python.exe scripts/create_release_bundle.py create --release-id 2026-10-30-rc1 [--allow-dirty] [--test-results tests.json] [--quick] [--no-archive]
.venv/Scripts/python.exe scripts/create_release_bundle.py verify --release-id 2026-10-30-rc1 [--quick]
```

* `create` refuses to overwrite an existing bundle (choose a new id), refuses a dirty working tree unless `--allow-dirty` (the uncommitted files are then listed in
  the manifest), refuses symbolic links and any path resolving outside the project, rejects duplicate paths (case-insensitive), fails if a required input is missing,
  and **writes nothing if a validation check fails**. `--quick` rechecks only `data/api` instead of re-hashing the 800 MB of raw data.
* `verify` recomputes every hash from disk and compares it with the manifest, `checksums.sha256` and the archive; it reports missing, extra and changed files, a
  changed `HEAD`, and any difference from the recorded data inventory. Exit code 1 on any difference. Tamper detection was exercised (an altered manifest copy was rejected).

## What a bundle contains (`reports/releases/<id>/`)

| File | Content | Tracked by Git? |
|---|---|---|
| `release-manifest.json` | release id, UTC time, Git commit/branch/`origin/main`, working-tree state and uncommitted files, tool versions (Python, Node, npm, aws-cdk-lib), per-file path/size/SHA-256 for five inventories (below), package metadata hashes, pointer to the data baseline | yes (small) |
| `checksums.sha256` | `sha256sum`-style lines for every inventoried file, the archive and the manifest | yes |
| `validation.json` | 15 automated checks and the test summary supplied with `--test-results`, plus what is *not* verified here | yes |
| `data-api.tar.gz` | deterministic archive of `data/api` (sorted, fixed mtime/owner) so the published artifacts can be restored if they are lost | **no** (the whole `reports/releases/` folder is ignored since Phase 9: untracked bundle files would otherwise make the tree dirty and block the next clean bundle); store it durably yourself |

Inventories: `source_tree` (everything Git tracks or would track except `docs/`, `reports/` and top-level `*.md`, so that this write-up can cite hashes; text hashed
with LF endings), `dataset_manifests` (`data/manifests`), `api_artifacts` (`data/api`), `lambda_package` (`infrastructure/build/lambda`, 1,040 files), `web_build` (`apps/web/dist`).
Excluded by design: `.env*`, `*.pem`/`*.key`, `.venv`, `node_modules`, `__pycache__`, raw downloads (covered by the data baseline instead).

## Checks run by `create` (validation.json)

Lambda native modules are AArch64 and CPython 3.13 Linux; no Windows binaries; no GeoPandas/Rasterio/Fiona/Shapely/PyProj/GDAL; the CDK handler string resolves to a
`handler` in the package; package code equals `services/api/floodlens_api`; packaged artifacts equal `data/api`; required artifacts present; installed versions equal the
12 pins in `requirements-lambda.txt`; unzipped size under 250 MB; web build has `index.html` and no `config.json`; **no secrets** (AWS access keys, private keys, Earthdata token,
a Location key literal, `.env`/`.pem`/`.key` files) in source, web build or package; the IMERG random-error field is absent from the API code, frontend source and
published artifacts; `schemas/`, `data/fixtures/`, `data/manifests/` and `geospatial/` are identical to `HEAD`; and `data/raw`, `data/processed`, `data/api` still match the Phase 6
hash inventory (`scripts/data_baseline.py`).

## Current bundle: `2026-10-30-rc1`

Made at commit `d2c67a2eb08818bcfcbdf7a81990d08cc4a8ba66` with a **dirty** tree (Phase 6 and 7 work is uncommitted, so the commit alone does *not* identify the source; `source_tree` does).

| Inventory | Files | Bytes | SHA-256 of the sorted path+hash listing |
|---|---|---|---|
| source_tree | 177 | 1,150,367 | `286c92637d09d0f82b28bc1fe0a316a25bedb1ea98f77c846edfbb7bcc929df6` |
| dataset_manifests | 15 | 108,610 | `bff5e9986de472a6a62894c604ed5af229f5895fac229409a8da205a05e6361d` |
| api_artifacts | 630 | 93,934,306 | `60c24975555371d15dcfdb74821d8e4fb97a880a69d6884d40632292848fddbb` |
| lambda_package | 1,040 | 103,361,262 | `8b33da52a445042b193019adca6e9b1c7beba67a66c4386e85c3a350b7968c6f` |
| web_build | 7 | 1,872,298 | `75a3fd5befb546c3d82bf9d7cb060968f978a80cfbd64ef79457c5cb6244c5fa` |

`data-api.tar.gz`: 13,464,329 bytes, SHA-256 `c6abb1d0dc97b95be2bf73743d70c8e32ed0fec91fe6ccf872a5cf35152c02c8`.
`release-manifest.json` SHA-256 `d8dcd29e1c989448fbc4634d47d44569a3dd81f24e252a2e5916e2fa5b355ae4`. Synthesized template (made with a placeholder e-mail and a 2026-12-29 expiry, **not** deployment values) `45a1cc356fb93b0e89a5cbdb2d6a38046d4c710a0e7cc12d151e9f76e7738588`, recorded for reference only.

## How to use it

1. **Before deploying:** `verify` must print `OK`. If any source, package or web file changed since, create a new bundle under a new id (never edit an old one).
2. **After the owner commits:** the Phase 7 working tree should be committed first, then a bundle recreated from a clean tree (no `--allow-dirty`); that bundle's commit hash then identifies the source.
3. **Which package was deployed?** CDK uploads `infrastructure/build/lambda` as an asset whose hash appears in the synthesized template (`cdk.out/FloodLens.assets.json`). Compare the deployed code's checksum
   (`aws lambda get-function --query Configuration.CodeSha256`, a base64 SHA-256 of the *zip*) against a zip built from the bundle's package listing, or simply redeploy only from a verified bundle. The zip's hash is not
   stored in the manifest because zip files are not byte-reproducible across tools [U].
4. **Durable copy:** copy the whole `reports/releases/<id>/` folder, including `data-api.tar.gz`, to storage outside this laptop (the owner's choice). Do not put it in a public location: it contains the published
   artifacts, not secrets, but there is no reason to publish it.

## Limits

* Hashes prove "the same bytes as when recorded", not that the data were correct (that is the scientific validation, documented separately).
* `data/raw` and `data/processed` are covered by `phase6-git-baseline.json` (per-file hashes recorded 2026-10-30); the earlier history is unknowable (single root commit).
* Pip wheels are fetched without hash pinning (P6-13); the bundle records what was installed, it does not stop a different wheel being fetched next time.
