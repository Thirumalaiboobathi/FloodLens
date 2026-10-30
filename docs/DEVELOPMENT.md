# Local development (Windows, Git Bash, VS Code) — no Docker required

All commands run from the repository root in Git Bash. Nothing here starts Docker.

## One-time setup
```bash
python -m venv .venv                      # Python 3.12+ (3.13 works locally)
source .venv/Scripts/activate             # activate (each new shell)
pip install -e "geospatial[dev]"          # rasterio/geopandas/shapely/pyproj ship Windows wheels
```
GDAL is bundled inside the rasterio/pyogrio wheels, so no separate GDAL install is needed.
WhiteboxTools (hydrology) is a native `.exe` downloaded into the venv by the `whitebox` package.

## Everyday commands
| Task | Command |
|---|---|
| Activate venv | `source .venv/Scripts/activate` |
| Tests (geospatial) | `make test-geo` or `python -m pytest geospatial -q` |
| Ruff | `python -m ruff check geospatial scripts` |
| Validate manifests/checksums | `python scripts/validate_data.py` |
| Ingest DEM | `python scripts/download_data.py dem --region chennai` |
| Ingest boundaries / OSM | `python scripts/ingest_vector.py boundaries` / `python scripts/ingest_vector.py osm` |
| Ingest rainfall (IMERG) | `python scripts/ingest_imerg.py probe` then `run --start 2023-12-03 --end 2023-12-05` |
| Exposure engine (all scenarios) | `python scripts/run_exposure.py --region chennai` (~75-130 s, ~450 MB peak) |
| Negative-cell / DEM quality check | `python scripts/investigate_negatives.py chennai` |
| Terrain derivatives | `python scripts/process_terrain.py --region chennai` |
| FastAPI backend *(Phase 3, not built yet)* | `cd services/api && uvicorn app.main:app --reload` |
| React frontend *(Phase 4, not built yet)* | `cd apps/web && npm install && npm run dev` |
| Frontend build *(Phase 4)* | `cd apps/web && npm run build` |
| CDK synth/diff *(Phase 5)* | `cd infrastructure && npx cdk synth` (do not use `cdk diff`: it writes to AWS) |
| CDK deploy *(Phase 7)* | `npx cdk deploy` — **only after explicit approval** of account, region, resources and cost |

## Earthdata token (IMERG) — keep it out of files and chat
The token is read only from the `EARTHDATA_TOKEN` environment variable.

Session-only (recommended). In Git Bash, type the token invisibly, then start VS Code from that shell
so the integrated terminal and the agent inherit it:
```bash
read -rs -p "Earthdata token: " EARTHDATA_TOKEN; echo; export EARTHDATA_TOKEN
code .
```
It disappears when the shell closes. Persistent alternative (stored unencrypted in your Windows
user environment; restart VS Code afterwards): in PowerShell
```powershell
[Environment]::SetEnvironmentVariable('EARTHDATA_TOKEN', (Read-Host 'token'), 'User')
```
Check without printing it: `python -c "import os;print(bool(os.environ.get('EARTHDATA_TOKEN')))"`.
Never put it in `.env`, scripts, commands that echo it, or commits. Tokens expire after 60 days;
regenerate at https://urs.earthdata.nasa.gov/ (Generate Token) and approve the "NASA GESDISC DATA ARCHIVE"
application under Authorized Apps if you get HTTP 403.

## Memory discipline (8 GB machine)
- Raster work reads only the AOI: `merge(..., bounds=...)` windows the source tiles; a full
  Chennai run peaks around 200 MB in Python (terrain script measured: 8 s).
- Large rasters must use `rasterio` windows/decimated reads (`validate_raster` already samples).
- GeoJSON of all Chennai roads is ~37 MB / 94k features; load with `pyogrio` column/bbox filters when possible.
- Big India-wide inputs (46 MB boundary files) are read once and filtered; don't keep them in memory.

## Containers
Docker is **not** needed for any local step. The Lambda deployment package can be built later
either with CDK's Docker bundling (CI only, GitHub Actions) or locally without Docker by
`pip install --platform manylinux2014_x86_64 --only-binary=:all: --target ...` into a zip; the choice
is recorded in ADR-0006. No Dockerfile has been created and no container command has been run.

## API (native Windows, no Docker)
```
.venv/Scripts/python.exe scripts/build_api_artifacts.py --region chennai
.venv/Scripts/python.exe scripts/export_schemas.py [--check]
cd services/api && ../../.venv/Scripts/python.exe -m uvicorn floodlens_api.main:app --reload   # /docs
cd services/api && ../../.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe scripts/run_sensitivity.py --region chennai
```


## Web client and rainfall layers (Phase 3)
    # one-time
    cd apps/web && npm install
    # publish API artifacts (validates and publishes rainfall; needs data/processed/exposure)
    .venv/Scripts/python.exe scripts/build_api_artifacts.py --region chennai
    # run API (port 8000) and the web app (port 5173, proxies /v1 to the API)
    cd services/api && ../../.venv/Scripts/python.exe -m uvicorn floodlens_api.main:app --port 8000
    cd apps/web && npm run dev
    # checks
    cd apps/web && npm run typecheck && npm test && npm run build
    cd services/api && ../../.venv/Scripts/python.exe -m pytest -q
    cd geospatial && PYTHONPATH=. ../.venv/Scripts/python.exe -m pytest -q
    .venv/Scripts/python.exe scripts/export_schemas.py --check     # schema drift
`FLOODLENS_API` overrides the proxy target; `VITE_API_BASE` and `VITE_MAP_STYLE_URL` are build-time only
and must never hold secrets. When new rainfall is ingested, rerun the build script; the frontend needs no
change because it lists whatever the API publishes.

## Phase 4: basemap, water, packaging, CDK
    # water artifacts are built by the same script as everything else
    .venv/Scripts/python.exe scripts/build_api_artifacts.py --region chennai
    # optional basemap in local development: untracked apps/web/.env.local (see apps/web/.env.example); never commit a key
    # Lambda package (no Docker; cross-installs arm64 wheels) and CDK checks. None of these touch AWS.
    .venv/Scripts/python.exe scripts/package_lambda.py
    cd infrastructure && npm install && npm test && npx tsc --noEmit && npx cdk synth --quiet
Deployment is documented in `docs/deployment/DEPLOYMENT.md` and requires explicit approval; do not run `cdk bootstrap` or `cdk deploy` without it.
`mangum` is only needed to import `floodlens_api.lambda_handler` (`pip install mangum`, the `lambda` extra).
