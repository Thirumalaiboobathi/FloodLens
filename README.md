# FloodLens

*See the Risk. Understand the Impact. Find the Safer Way.*

India flood-exposure screening (Chennai pilot). It is a **screening** tool: it shows relative, terrain-based exposure classes and observed rainfall accumulation. It is not a flood forecast, not a flood-depth model and not an emergency system. Rainfall is "rainfall accumulation", never depth, and nothing is live or real-time.

## Layout

| Path | What |
|---|---|
| `geospatial/` | offline processing engine (terrain, rainfall, exposure); unit-tested |
| `data/`, `schemas/` | datasets, manifests and JSON schemas (protected: do not edit by hand) |
| `services/api/` | read-only FastAPI app; served on Lambda by Mangum |
| `apps/web/` | React 19 + Vite + MapLibre front end |
| `infrastructure/` | AWS CDK (TypeScript): API Gateway HTTP API, Lambda, Amazon Location key, alarms |
| `amplify.yml` | AWS Amplify Hosting build for `apps/web` |
| `scripts/` | data, packaging and release tooling |
| `docs/` | architecture, API, methodology and deployment documents |

## Architecture (current target, not yet deployed)

Browser → **AWS Amplify Hosting** (static app) and → **API Gateway HTTP API** (`GET`/`HEAD /v1/{proxy+}`, CORS for the Amplify origin, throttled) → **Lambda** (Python 3.13, arm64, FastAPI + Mangum, data artifacts packaged inside). Map tiles come from Amazon Location with a restricted, expiring browser key. Region `ap-south-1`. Details and deployment steps: [docs/deployment/AMPLIFY_MIGRATION.md](docs/deployment/AMPLIFY_MIGRATION.md). Earlier CloudFront-based documents are marked superseded.

## Local development

```
python -m venv .venv && .venv/Scripts/python.exe -m pip install -e services/api   # see services/api/pyproject.toml
cd services/api && ../../.venv/Scripts/python.exe -m uvicorn floodlens_api.main:app     # API on :8000 (needs data/api)
cd apps/web && npm ci && npm run dev                                                    # app on :5173, /v1 proxied to the API
```

Leave `VITE_API_BASE_URL` unset locally. Environment variables are listed in `apps/web/.env.example`; every `VITE_*` value is public in the built bundle, so never put a secret there.

## Tests

```
make test-geo test-api test-infra          # or: cd apps/web && npm test
cd apps/web && npm run typecheck && npm run build
.venv/Scripts/python.exe -m ruff check geospatial scripts services
```

## Deploy

Nothing is deployed from a laptop without the owner's written approval. Read [docs/deployment/AMPLIFY_MIGRATION.md](docs/deployment/AMPLIFY_MIGRATION.md) first (sequence, environment variables, CORS, the browser map key, rollback, cost). `cdk diff` is intentionally disabled for this project.
