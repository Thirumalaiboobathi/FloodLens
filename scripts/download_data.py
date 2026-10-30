"""Download pilot-region source data and write manifests.

Usage: python scripts/download_data.py dem --region chennai
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from floodlens_geo.ingestion.dem import discover_tiles, download_tiles
from floodlens_geo.metadata.manifest import DatasetManifest, ProcessingStatus
from floodlens_geo.raster.validate import validate_raster

ROOT = Path(__file__).resolve().parents[1]
# west, south, east, north (EPSG:4326) -- pilot areas of interest
REGIONS = {"chennai": (80.05, 12.85, 80.35, 13.25)}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dem(region: str) -> None:
    bbox = REGIONS[region]
    tiles = discover_tiles(bbox)
    if not tiles:
        raise SystemExit(f"no DEM tiles found for {region}")
    paths = download_tiles(tiles, ROOT / "data" / "raw" / "dem" / region)
    reports = [validate_raster(p, expected_epsg=4326) for p in paths]
    products = sorted({t.product for t in tiles})
    m = DatasetManifest(
        dataset_id=f"copernicus-dem-{region}",
        dataset_name=f"Copernicus DEM ({', '.join(products)}) tiles for {region.title()}",
        source_url="s3://copernicus-dem-30m/ , s3://copernicus-dem-90m/",
        provider="European Space Agency / Copernicus (AWS Open Data Registry)",
        license="Copernicus DEM licence (free, attribution required)",
        attribution="Contains modified Copernicus DEM data (c) DLR e.V. 2010-2014 and "
        "(c) Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the "
        "European Union and ESA; all rights reserved.",
        acquisition_timestamp=datetime.now(UTC),
        spatial_resolution=", ".join(f"{t.product} {t.resolution_m} m ({t.name})" for t in tiles),
        coordinate_reference_system="EPSG:4326",
        processing_version="raw-0.1.0",
        geographical_coverage=f"{region.title()} AOI bbox {bbox}",
        processing_status=ProcessingStatus.DOWNLOADED,
        limitations=[
            "Digital Surface Model (includes buildings/vegetation), not bare-earth terrain",
            "Not suitable for flood depth estimation",
            "Vertical datum EGM2008 heights",
        ],
        checksum_sha256=None,
    )
    out = ROOT / "data" / "manifests" / f"dem-{region}.json"
    doc = json.loads(m.model_dump_json())
    doc["tile_checksums_sha256"] = {p.name: sha256(p) for p in paths}
    doc["validation"] = [r.__dict__ for r in reports]
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    for t, r in zip(tiles, reports, strict=True):
        print(f"{t.name} {t.product} {r.width}x{r.height} valid={r.valid_fraction:.1%} "
              f"elev=[{r.minimum:.1f},{r.maximum:.1f}]")
    print("wrote", out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["dem"])
    ap.add_argument("--region", default="chennai", choices=list(REGIONS))
    a = ap.parse_args()
    dem(a.region)
