# ADR-0006: No Docker for local development

Status: Accepted (developer requirement, 8 GB RAM machine)

- Local work uses Python venv + native Node.js. No Docker Desktop, WSL or `docker` commands.
- Rasterio/GeoPandas/Shapely/pyproj install from Windows binary wheels with GDAL bundled;
  WhiteboxTools runs as a native executable (verified, v2.4.0).
- Lambda packaging: build in GitHub Actions (Linux runner) or via cross-platform `pip --platform
  manylinux2014_x86_64 --only-binary=:all:`. Geospatial wheels exceed Lambda's 250 MB unzipped limit
  when combined, so the API (which only reads prebuilt artifacts, ADR-0001) must not import
  rasterio/geopandas; the heavy engine runs offline. This is the one place a container could become
  genuinely necessary (container-image Lambda), and only if the API later needs GDAL at runtime.
- Dockerfiles, if added, are optional deployment artifacts and never required by `make` targets.
