# ADR-0002: Toolchain and dependency choices

Status: Accepted (2026-10-30)

Verified on the dev machine (Windows 11): Node 24.13, npm 10.2, pnpm 10.30, Python 3.13.4,
Docker 27.5, AWS CLI 2.34 (authenticated, default region ap-south-1). Prebuilt wheels were
confirmed available for rasterio, geopandas, shapely, pyproj, scipy, numpy, xarray, networkx,
fastapi, pydantic, mangum, whitebox on Python 3.13/Windows. GDAL CLI and CDK CLI are not installed
globally; CDK is pinned as a project devDependency and run through `npx`.

- Python ≥3.12 for the engine; Lambda runtime Python 3.12 (wheels built for manylinux via Docker
  bundling in CDK, not assumed from the local machine).
- Node workspace managed by npm (already present); frontend React 19 + Vite + TS.
- Python venv at `.venv/` (git-ignored).
