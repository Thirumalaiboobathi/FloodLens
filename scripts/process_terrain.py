"""Terrain derivatives for a pilot region from the downloaded Copernicus DEM.

Usage: python scripts/process_terrain.py --region chennai
Reads only the AOI (+buffer) from the DEM tiles, so peak memory stays small.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import rasterio
from floodlens_geo.metadata.manifest import (
    DatasetKind,
    DatasetManifest,
    ProcessingStatus,
)
from floodlens_geo.raster.validate import validate_raster
from floodlens_geo.terrain.derivatives import (
    NODATA,
    local_relief,
    ocean_mask,
    prepare_dem,
    run_hydrology,
    slope_degrees,
)

ROOT = Path(__file__).resolve().parents[1]
REGIONS = {"chennai": (80.05, 12.85, 80.35, 13.25)}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(region: str) -> None:
    bbox = REGIONS[region]
    tiles = sorted((ROOT / "data" / "raw" / "dem" / region).glob("*.tif"))
    if not tiles:
        raise SystemExit("no DEM tiles; run scripts/download_data.py dem first")
    out = ROOT / "data" / "processed" / "terrain" / region
    dem = prepare_dem(tiles, bbox, out / "dem_utm.tif")
    with rasterio.open(dem) as r:
        z = r.read(1)
        prof = r.profile
        cell = r.res[0]
        print(f"DEM {r.width}x{r.height} {r.crs} cell={cell:.1f} m")

    def save(name: str, arr: np.ndarray) -> Path:
        p = out / f"{name}.tif"
        prof.update(dtype="float32", nodata=NODATA, compress="deflate")
        with rasterio.open(p, "w", **prof) as d:
            d.write(np.where(np.isfinite(arr), arr, NODATA).astype("float32"), 1)
        return p

    sea = ocean_mask(z)
    print(f"ocean cells masked: {sea.sum()} ({sea.mean():.1%})")
    z = np.where(sea, NODATA, z).astype("float32")  # sea is flat 0 m: not terrain
    land = save("dem_land_utm", z)
    slope = save("slope_deg", slope_degrees(z, cell))
    relief = save("local_relief_m", local_relief(z, radius_cells=17))  # ~510 m window
    hyd = run_hydrology(land, out / "whitebox")
    products = {
        "dem_land_utm": land, "slope_deg": slope, "local_relief_m": relief,
        "twi": hyd.twi, "sca_m": hyd.sca, "hand_m": hyd.hand, "streams": hyd.streams,
    }
    now = datetime.now(UTC)
    reports = {}
    for k, p in products.items():
        reports[k] = validate_raster(p, min_valid_fraction=0.01 if k == "streams" else 0.5).__dict__
        r = reports[k]
        print(f"{k:15s} valid={r['valid_fraction']:.1%} range=[{r['minimum']:.2f}, {r['maximum']:.2f}]")
    m = DatasetManifest(
        dataset_id=f"terrain-derivatives-{region}",
        dataset_name=f"Terrain derivatives from Copernicus DEM, {region.title()}",
        kind=DatasetKind.DERIVED,
        derived_from=[f"copernicus-dem-{region}"],
        source_url="s3://copernicus-dem-30m/",
        provider="FloodLens processing of Copernicus DEM (ESA/Copernicus)",
        license="Copernicus DEM licence (free, attribution required)",
        attribution="Contains modified Copernicus DEM data (c) DLR e.V. 2010-2014 and (c) Airbus "
        "Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA.",
        acquisition_timestamp=now,
        spatial_resolution=f"{cell:.0f} m (bilinear resampled from GLO-30), UTM",
        coordinate_reference_system=str(prof["crs"]),
        processing_version="floodlens-geo terrain 0.1.0; WhiteboxTools (breach, D-inf, TWI, HAND)",
        geographical_coverage=f"{region.title()} AOI bbox {bbox} plus 0.02 degree buffer",
        processing_status=ProcessingStatus.PROCESSED,
        limitations=[
            "Surface model: buildings, vegetation and bridges distort slope, flow paths and HAND",
            "30 m cells cannot represent drains, culverts, canals or embankments in urban areas",
            "Chennai is very flat; small vertical errors dominate slope and TWI",
            "Streams are extracted by a drainage-area threshold, not mapped channels",
            "Sea (flat 0 m in the source) is masked as no-data. 597 land-grid cells are < 0 m (247 below -5 m); all lie on Copernicus water-body-mask pixels (lakes, rivers, tidal water), are kept unchanged in the terrain products and are excluded from exposure scoring (docs/analysis/TERRAIN_QUALITY.md)",
            "Indicators of terrain position only; not flood depth or extent",
        ],
        checksum_sha256=sha256(land),
    )
    doc = json.loads(m.model_dump_json())
    doc["products"] = {k: {"file": p.relative_to(ROOT).as_posix(), "sha256": sha256(p)}
                       for k, p in products.items()}
    doc["validation"] = reports
    (ROOT / "data" / "manifests" / f"terrain-derivatives-{region}.json").write_text(
        json.dumps(doc, indent=2), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="chennai", choices=list(REGIONS))
    main(ap.parse_args().region)
