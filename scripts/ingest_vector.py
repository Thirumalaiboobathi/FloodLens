"""Acquire vector data: administrative boundaries and OpenStreetMap features.

Usage:
  python scripts/ingest_vector.py boundaries      # geoBoundaries India ADM1/ADM2 + Chennai district
  python scripts/ingest_vector.py osm --region chennai
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import requests
from floodlens_geo.ingestion import osm
from floodlens_geo.metadata.manifest import (
    DatasetKind,
    DatasetManifest,
    ProcessingStatus,
)

ROOT = Path(__file__).resolve().parents[1]
MAN = ROOT / "data" / "manifests"
REGIONS = {"chennai": (80.05, 12.85, 80.35, 13.25)}
GB = "https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/IND"
LEGAL = (
    "Administrative boundaries are for screening visualisation only. They are not an authoritative "
    "survey and do not represent the official external boundary of India as notified by the "
    "Survey of India; do not use them to depict international boundaries."
)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(m: DatasetManifest, **extra) -> None:
    doc = json.loads(m.model_dump_json())
    doc.update(extra)
    (MAN / f"{m.dataset_id}.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")


def boundaries() -> None:
    raw = ROOT / "data" / "raw" / "boundaries"
    raw.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    specs = {
        "ADM1": ("State / Union Territory", "CC BY 2.5 India (DataMeet maps, via geoBoundaries)", "2011"),
        "ADM2": ("District", "ODbL 1.0 (Pathways Data / lgdirectory.gov.in, via geoBoundaries)", "2021"),
    }
    for lvl, (canon, lic, year) in specs.items():
        for suffix in ("", "_simplified"):
            out = raw / f"IND-{lvl}{suffix}.geojson"
            if not out.exists():
                r = requests.get(f"{GB}/{lvl}/geoBoundaries-IND-{lvl}{suffix}.geojson", timeout=300)
                r.raise_for_status()
                out.write_bytes(r.content)
        full = raw / f"IND-{lvl}.geojson"
        g = gpd.read_file(full)
        assert g.crs.to_epsg() == 4326 and g.geometry.is_valid.all() and not g.geometry.is_empty.any()
        write_manifest(
            DatasetManifest(
                dataset_id=f"geoboundaries-ind-{lvl.lower()}",
                dataset_name=f"India {canon} boundaries (geoBoundaries gbOpen, year {year})",
                source_url=f"{GB}/{lvl}/",
                provider="geoBoundaries (William & Mary geoLab)",
                license=lic,
                attribution="geoBoundaries gbOpen IND " + lvl + "; underlying source see licence field",
                acquisition_timestamp=now,
                observation_period=f"boundary year {year}",
                spatial_resolution="vector; full and simplified geometries",
                coordinate_reference_system="EPSG:4326",
                processing_version="geoBoundaries commit 9469f09",
                geographical_coverage="India",
                processing_status=ProcessingStatus.DOWNLOADED,
                limitations=[
                    LEGAL,
                    (f"{len(g)} units (geoBoundaries metadata states "
                     f"{'36' if lvl == 'ADM1' else '736'}); names/codes may differ from LGD"),
                    "ODbL share-alike applies to derived databases" if lvl == "ADM2"
                    else "CC BY 2.5 IN requires attribution",
                ],
                checksum_sha256=sha256(full),
            ),
            feature_count=len(g),
            file=full.relative_to(ROOT).as_posix(),
        )
        print(lvl, len(g), "units", full.stat().st_size // 1024, "KiB")

    d = gpd.read_file(raw / "IND-ADM2.geojson")
    c = d[d.shapeName == "Chennai"]
    assert len(c) == 1
    out = ROOT / "data" / "processed" / "boundaries" / "chennai-district.geojson"
    out.parent.mkdir(parents=True, exist_ok=True)
    c.to_file(out, driver="GeoJSON")
    area = c.to_crs(32644).area.iloc[0] / 1e6
    write_manifest(
        DatasetManifest(
            dataset_id="chennai-district-boundary",
            dataset_name="Chennai district polygon (pilot boundary)",
            kind=DatasetKind.DERIVED,
            derived_from=["geoboundaries-ind-adm2"],
            source_url=f"{GB}/ADM2/",
            provider="geoBoundaries (William & Mary geoLab)",
            license="ODbL 1.0 (Pathways Data / lgdirectory.gov.in, via geoBoundaries)",
            attribution="geoBoundaries gbOpen IND ADM2; Pathways Data Pvt. Ltd.",
            acquisition_timestamp=now,
            spatial_resolution="vector",
            coordinate_reference_system="EPSG:4326",
            processing_version="floodlens-geo 0.1.0 (filter shapeName == 'Chennai')",
            geographical_coverage="Chennai district, Tamil Nadu",
            processing_status=ProcessingStatus.PROCESSED,
            limitations=[
                LEGAL,
                (f"Polygon area is {area:.0f} km2, smaller than the ~426 km2 usually quoted for "
                 "Chennai district; treat as the city core and verify before reporting areas"),
            ],
            checksum_sha256=sha256(out),
        ),
        area_km2=round(area, 1),
        bounds=[round(x, 5) for x in c.total_bounds],
    )
    print("chennai district", f"{area:.0f} km2", [round(x, 3) for x in c.total_bounds])


def chennai_osm_district() -> None:
    """Alternative Chennai district polygon (OSM relation 7910817) kept beside the geoBoundaries one."""
    out = ROOT / "data" / "processed" / "boundaries" / "chennai-district-osm.geojson"
    g = osm.to_geodataframe(osm.fetch("[out:json][timeout:120];rel(7910817);out geom;"), "district")
    if len(g) != 1 or g.geom_type.iloc[0] != "Polygon":
        raise SystemExit("unexpected geometry for OSM Chennai district relation")
    g[["osm_type", "osm_id", "geometry"]].to_file(out, driver="GeoJSON")
    area = g.to_crs(32644).area.iloc[0] / 1e6
    gb = gpd.read_file(ROOT / "data/processed/boundaries/chennai-district.geojson").to_crs(32644)
    inter = g.to_crs(32644).geometry.iloc[0].intersection(gb.geometry.iloc[0]).area / 1e6
    write_manifest(
        DatasetManifest(
            dataset_id="chennai-district-boundary-osm",
            dataset_name="Chennai district polygon from OpenStreetMap (comparison boundary)",
            source_url="https://www.openstreetmap.org/relation/7910817",
            provider="OpenStreetMap contributors via Overpass API",
            license="ODbL 1.0",
            attribution="(c) OpenStreetMap contributors",
            acquisition_timestamp=datetime.now(UTC),
            spatial_resolution="vector",
            coordinate_reference_system="EPSG:4326",
            processing_version="floodlens-geo osm 0.1.0 (relation 7910817, admin_level 5)",
            geographical_coverage="Chennai district, Tamil Nadu",
            processing_status=ProcessingStatus.PROCESSED,
            limitations=[
                LEGAL,
                (f"{area:.0f} km2; contains {inter:.0f} km2 of the {gb.area.iloc[0] / 1e6:.0f} km2 "
                 "geoBoundaries polygon. May include coastal waters; crowd-sourced, not authoritative"),
            ],
            checksum_sha256=sha256(out),
        ),
        area_km2=round(area, 1),
        overlap_with_geoboundaries_km2=round(inter, 1),
        file=out.relative_to(ROOT).as_posix(),
    )
    print(f"OSM district {area:.0f} km2, overlap with geoBoundaries polygon {inter:.0f} km2")


def osm_extract(region: str) -> None:
    bbox = REGIONS[region]
    raw = ROOT / "data" / "raw" / "osm" / region
    proc = ROOT / "data" / "processed" / "osm" / region
    raw.mkdir(parents=True, exist_ok=True)
    proc.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    now = datetime.now(UTC)
    for name, (selector, mode) in osm.QUERIES.items():
        rp = raw / f"{name}.json"
        if not rp.exists():
            rp.write_text(
                json.dumps(osm.fetch(osm.build_query(selector, mode, bbox), session)),
                encoding="utf-8",
            )
        gdf = osm.to_geodataframe(json.loads(rp.read_text(encoding="utf-8")), name)
        if gdf.empty:
            raise SystemExit(f"{name}: no features returned; refusing to write an empty layer")
        out = proc / f"{name}.geojson"
        gdf.to_file(out, driver="GeoJSON")
        write_manifest(
            DatasetManifest(
                dataset_id=f"osm-{region}-{name}",
                dataset_name=f"OpenStreetMap {name}, {region.title()} AOI",
                source_url="https://overpass-api.de/api/interpreter",
                provider="OpenStreetMap contributors via Overpass API",
                license="ODbL 1.0",
                attribution="(c) OpenStreetMap contributors",
                acquisition_timestamp=now,
                observation_period=f"OSM snapshot at {now:%Y-%m-%d}",
                spatial_resolution="vector",
                coordinate_reference_system="EPSG:4326",
                processing_version="floodlens-geo osm 0.1.0",
                geographical_coverage=f"{region.title()} AOI bbox {bbox}",
                processing_status=ProcessingStatus.PROCESSED,
                limitations=[
                    "Crowd-sourced; completeness and tagging vary and are not validated",
                    "Hospitals layer includes clinics; no capacity or operating status is known",
                    "Road geometry only; no closure, flooding or passability information",
                    "ODbL: derived databases must be shared alike with attribution",
                ],
                checksum_sha256=sha256(out),
            ),
            feature_count=len(gdf),
            file=out.relative_to(ROOT).as_posix(),
            geometry_types=sorted(gdf.geom_type.unique().tolist()),
        )
        print(f"{name}: {len(gdf)} features, {out.stat().st_size // 1024} KiB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("boundaries")
    sub.add_parser("chennai-osm-district")
    sp = sub.add_parser("osm")
    sp.add_argument("--region", default="chennai", choices=list(REGIONS))
    a = ap.parse_args()
    if a.cmd == "boundaries":
        boundaries()
    elif a.cmd == "chennai-osm-district":
        chennai_osm_district()
    else:
        osm_extract(a.region)
