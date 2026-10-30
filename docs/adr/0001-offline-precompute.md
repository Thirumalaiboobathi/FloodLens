# ADR-0001: Precompute analytics offline; API serves artifacts

Status: Accepted (2026-10-30)

**Context.** DEM tiles, IMERG and OSM for India are far too large for Lambda request paths.

**Decision.** All raster/hydrology/exposure work runs in the offline `floodlens_geo` pipeline.
Outputs (GeoJSON, COG, PNG overlays, manifests) are published to S3. The API is read-only.

**Consequences.** Cheap, fast, reproducible. Scenario evaluation recomputes only the rainfall
component of the index from stored component scores (no raster access at request time).
