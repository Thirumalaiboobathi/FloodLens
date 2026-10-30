"""Ingest NASA GPM IMERG V07 daily rainfall (Final or Late Run) for a pilot region.

Usage:
  python scripts/ingest_imerg.py probe                      # auth + one small request
  python scripts/ingest_imerg.py run --start 2023-12-03 --end 2023-12-05          # Final Run
  python scripts/ingest_imerg.py run --run late --latest 7                         # Late Run

Final and Late data are separate streams: raw files go to data/raw/imerg/ (Final) and
data/raw/imerg-late/ (Late), outputs and manifests carry the run in their names, and existing
files are never overwritten. The Earthdata token is read only from the EARTHDATA_TOKEN
environment variable and is never written anywhere.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
from floodlens_geo.ingestion import imerg
from floodlens_geo.ingestion.imerg import Run
from floodlens_geo.metadata.manifest import (
    DatasetKind,
    DatasetManifest,
    ProcessingStatus,
)
from floodlens_geo.raster.validate import validate_raster

ROOT = Path(__file__).resolve().parents[1]
REGIONS = {"chennai": (80.05, 12.85, 80.35, 13.25)}  # same AOI as the DEM
PROBE_DAY = date(2023, 12, 3)  # Cyclone Michaung heavy-rain period, Chennai
GES_DISC = "https://data.gesdisc.earthdata.nasa.gov/data/GPM_L3/"
COMMON_LIMITATIONS = [
    "Satellite-based estimate on a ~10 km (0.1 degree) grid; not a rain-gauge measurement",
    "Daily totals cover 00:00-23:59:59 UTC (05:30 IST to 05:29 IST next day)",
    "Rainfall accumulation, not flood depth; not a forecast and not an official warning",
]
_CITE = (
    "Huffman, G.J., E.F. Stocker, D.T. Bolvin, E.J. Nelkin, Jackson Tan (2023), GPM IMERG {run} "
    "Precipitation L3 1 day 0.1 degree x 0.1 degree V07, Greenbelt, MD, Goddard Earth Sciences "
    "Data and Information Services Center (GES DISC)."
)
# Per-run metadata. File/id naming for Final is unchanged (backward compatible); Late is distinct
# so globs for Final products (e.g. in run_exposure.py) can never pick up Late outputs.
RUNS = {
    Run.FINAL: {
        "raw_dir": "imerg",
        "stem": "imerg-v07-total",
        "raw_stem": "nasa-gpm-imerg-v07-daily-raw",
        "label": "Final",
        "concept_id": "C2723754864-GES_DISC",
        "doi": "10.5067/GPM/IMERGDF/DAY/07",
        "attribution": _CITE.format(run="Final"),
        "limitations": [
            ("Final Run is gauge-adjusted at monthly scale; daily values carry substantial error "
             "in intense convective/cyclonic rain"),
            "Historical observations only",
        ],
        "kind": "historical research stream",
    },
    Run.LATE: {
        "raw_dir": "imerg-late",
        "stem": "imerg-v07-late-total",
        "raw_stem": "nasa-gpm-imerg-v07-late-daily-raw",
        "label": "Late",
        "concept_id": "C2723754859-GES_DISC",
        "doi": "10.5067/GPM/IMERGDL/DAY/07",
        "attribution": _CITE.format(run="Late"),
        "limitations": [
            ("Late Run is a near-real-time provisional estimate (~14 h latency) without the monthly "
             "gauge adjustment of Final Run; it can differ materially from Final for the same day "
             "and is not interchangeable with it"),
            "Recent satellite rainfall: may be revised or replaced by Final Run later",
        ],
        "kind": "recent satellite rainfall stream",
    },
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _token() -> str:
    try:
        return imerg.get_token()
    except imerg.AuthenticationError as e:
        print(f"AUTH: {e}", file=sys.stderr)
        raise SystemExit(2) from None


def _report(e: imerg.ImergError) -> None:
    print(f"FAILED ({type(e).__name__}): {e}", file=sys.stderr)
    raise SystemExit(1)


def daily_stats(f: imerg.DailyField, bbox) -> dict:
    """Per-day statistics over the AOI window (mm/day, satellite estimate)."""
    arr, _ = imerg.clip_to_bbox(f.data, f, bbox)
    ok = np.isfinite(arr)
    v = arr[ok]
    return {
        "date": f.day.isoformat(),
        "granule": f.source_file,
        "global_grid": list(f.data.shape),
        "global_missing_percent": round(float(100 * np.isnan(f.data).mean()), 4),
        "global_valid_cells": int(np.isfinite(f.data).sum()),
        "aoi_cells": int(arr.size),
        "aoi_missing_cells": int((~ok).sum()),
        "aoi_nodata_percent": round(float(100 * (~ok).mean()), 2),
        "aoi_mm_per_day": {k: round(float(fn(v)), 2) for k, fn in
                           (("min", np.min), ("mean", np.mean), ("median", np.median),
                            ("max", np.max))} if v.size else None,
    }


def probe(region: str) -> None:
    token = _token()
    raw = ROOT / "data" / "raw" / "imerg" / region
    try:
        granules = imerg.find_granules(PROBE_DAY, PROBE_DAY)
        print(f"CMR ok (public): {len(granules)} granule(s) for {PROBE_DAY}")
        if not granules:
            raise SystemExit("no granule found")
        p = imerg.download_granule(granules[0], raw, token)
        f = imerg.read_daily(p)
        arr, _ = imerg.clip_to_bbox(f.data, f, REGIONS[region])
    except imerg.ImergError as e:
        _report(e)
    print(f"download ok: {p.name} ({p.stat().st_size} bytes)")
    print(f"AOI cells {arr.shape}, daily mean rate mm/day min={arr.min():.1f} max={arr.max():.1f}")


def granule_provenance(g: imerg.Granule, p: Path, cov: imerg.FileCoverage, stats: dict,
                       quality: dict, retrieved: tuple[datetime, str], cfg: dict) -> dict:
    """Everything needed to trace one observation day back to NASA, with no credentials."""
    return {
        "observation_date": g.day.isoformat(),
        "utc_coverage": {"start": cov.start.isoformat(), "stop": cov.stop.isoformat()},
        "dataset_id": f"{imerg.SHORT_NAMES[g.run]}.{imerg.VERSION}",
        "collection_concept_id": cfg["concept_id"],
        "doi": cfg["doi"],
        "run_type": g.run.value,
        "product_version": g.version,
        "granule_id": g.granule_id,
        "granule_file": p.name,
        "source_url": g.url,
        "cmr_updated": g.updated,
        "retrieved_at": retrieved[0].isoformat(),
        "retrieval_basis": retrieved[1],
        "size_bytes": p.stat().st_size,
        "sha256": sha256(p),
        "native_resolution_deg": imerg.RESOLUTION_DEG,
        "crs": "EPSG:4326",
        "variable": imerg.VARIABLE,
        "units": imerg.EXPECTED_UNITS,
        "global_valid_cells": stats["global_valid_cells"],
        "global_missing_percent": stats["global_missing_percent"],
        "aoi_mm_per_day": stats["aoi_mm_per_day"],
        "quality": quality,
    }


def ingest(region: str, run_type: Run, granules: list[imerg.Granule]) -> None:
    token = _token()
    cfg = RUNS[run_type]
    bbox = REGIONS[region]
    raw = ROOT / "data" / "raw" / cfg["raw_dir"] / region
    start, end = granules[0].day, granules[-1].day
    try:
        paths, retrieved = [], []
        for g in granules:
            dest = raw / g.filename
            existed = dest.exists() and dest.stat().st_size > 0
            paths.append(imerg.download_granule(g, raw, token))
            if existed:
                retrieved.append((datetime.fromtimestamp(dest.stat().st_mtime, UTC), "file_mtime"))
            else:
                retrieved.append((datetime.now(UTC), "download"))
        covs = [imerg.verify_internal_date(p, g.day) for p, g in zip(paths, granules, strict=True)]
        fields = [imerg.read_daily(p) for p in paths]
        for g, f in zip(granules, fields, strict=True):
            if f.day != g.day:
                raise imerg.ImergDataError(f"{f.source_file}: day {f.day} != catalogue day {g.day}")
        total, ref = imerg.accumulate(fields)
        arr, tr = imerg.clip_to_bbox(total, ref, bbox)
        daily = [daily_stats(f, bbox) for f in fields]
        quality = [imerg.quality_stats(p, f, bbox) for p, f in zip(paths, fields, strict=True)]
    except imerg.ImergError as e:
        _report(e)

    period = f"{start.isoformat()}/{end.isoformat()} (UTC days)"
    out = ROOT / "data" / "processed" / "rainfall" / f"{cfg['stem']}-{region}-{start}_{end}.tif"
    man_dir = ROOT / "data" / "manifests"
    raw_id = f"{cfg['raw_stem']}-{region}-{start}_{end}"
    der_id = f"{cfg['stem']}-{region}-{start}_{end}"
    for target in (out, man_dir / f"{raw_id}.json", man_dir / f"{der_id}.json"):
        if target.exists():
            raise SystemExit(f"refusing to overwrite existing {target.relative_to(ROOT).as_posix()}")
    versions = sorted({g.version for g in granules})
    product = imerg.SHORT_NAMES[run_type]
    version_text = f"{product}.{imerg.VERSION} ({'+'.join(versions)})"
    tags = {
        "SOURCE": f"NASA GPM IMERG V07 {cfg['label']} Run daily ({product})",
        "RUN_TYPE": run_type.value,
        "PRODUCT_VERSIONS": ",".join(versions),
        "UNITS": "mm (accumulated over the observation period)",
        "OBSERVATION_PERIOD": period,
        "DATA_KIND": "observation-derived (satellite precipitation estimate)",
    }
    imerg.write_geotiff(out, arr, tr, tags)
    report = validate_raster(out, expected_epsg=4326, min_valid_fraction=0.99)

    limitations = [*COMMON_LIMITATIONS, *cfg["limitations"]]
    if len(versions) > 1:
        limitations.append(
            f"Period spans product versions {', '.join(versions)}; algorithm versions differ"
        )
    limitations.append(
        "Clipped to the AOI bounding box, not the official Chennai boundary "
        "(boundary dataset pending, ADR-0003)"
    )
    common = {
        "provider": "NASA GES DISC / GPM",
        "license": "NASA open data (free, attribution requested); see GES DISC data policy",
        "attribution": cfg["attribution"],
        "acquisition_timestamp": datetime.now(UTC),
        "observation_period": period,
        "coordinate_reference_system": "EPSG:4326",
        "geographical_coverage": f"{region.title()} AOI bbox {bbox} (whole 0.1 degree cells)",
        "limitations": limitations,
    }
    source_url = f"{GES_DISC}{product}.{imerg.VERSION}/"
    provenance = [
        granule_provenance(g, p, c, s, q, r, cfg)
        for g, p, c, s, q, r in zip(granules, paths, covs, daily, quality, retrieved, strict=True)
    ]

    raw_m = DatasetManifest(
        dataset_id=raw_id,
        dataset_name=f"GPM IMERG V07 {cfg['label']} daily granules, {region.title()}, {period}",
        source_url=source_url,
        spatial_resolution="0.1 degree (~10 km), global granules, mm/day",
        processing_version=version_text,
        processing_status=ProcessingStatus.DOWNLOADED,
        **common,
    )
    doc = json.loads(raw_m.model_dump_json())
    doc["granule_checksums_sha256"] = {p.name: pr["sha256"] for p, pr in zip(paths, provenance, strict=True)}
    doc["granule_files"] = {p.relative_to(ROOT).as_posix(): pr["sha256"]
                            for p, pr in zip(paths, provenance, strict=True)}
    doc["run_type"] = run_type.value
    doc["product_versions"] = versions
    doc["daily_provenance"] = provenance
    doc["data_quality_notes"] = [imerg.RANDOM_ERROR_NOTE]
    (man_dir / f"{raw_id}.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")

    der_m = DatasetManifest(
        dataset_id=der_id,
        dataset_name=f"Accumulated IMERG {cfg['label']} rainfall (mm), {region.title()}, {period}",
        kind=DatasetKind.DERIVED,
        derived_from=[raw_id],
        source_url=source_url,
        spatial_resolution="0.1 degree (~10 km), units: mm accumulated; nodata -9999",
        processing_version="floodlens-geo imerg 0.2.0",
        processing_status=ProcessingStatus.PROCESSED,
        checksum_sha256=sha256(out),
        **common,
    )
    doc = json.loads(der_m.model_dump_json())
    doc["validation"] = {**report.__dict__, "path": out.relative_to(ROOT).as_posix()}
    doc["daily_statistics"] = daily
    doc["granules"] = [d["granule"] for d in daily]
    doc["run_type"] = run_type.value
    doc["product_versions"] = versions
    doc["stream"] = cfg["kind"]
    doc["daily_provenance"] = provenance
    doc["data_quality_notes"] = [imerg.RANDOM_ERROR_NOTE]
    doc["processing_configuration"] = {
        "product": f"{product}.{imerg.VERSION} ({'+'.join(versions)}), "
                   f"variable '{imerg.VARIABLE}', units {imerg.EXPECTED_UNITS}",
        "run_type": run_type.value,
        "accumulation": "sum of consecutive UTC daily totals; a cell missing on any day stays missing",
        "missing_data": "fill value -9999.9 -> NaN; zero rainfall is a valid observation, not missing",
        "aoi_bbox_wgs84": list(bbox),
        "window": "whole 0.1 degree cells intersecting the bbox; no resampling or interpolation",
        "grid_validation": "check_axis: dtype-aware tolerance, monotonic, regular, bounded",
        "internal_date_check": "FileHeader Start/StopGranuleDateTime and time axis match file name",
        "granule_selection": ("latest complete UTC days via CMR, discovered at run time"
                              if run_type is Run.LATE else "explicit date range via CMR"),
    }
    doc["data_kind"] = (
        "satellite precipitation estimate (not gauge observations); rainfall accumulation, "
        "not flood depth"
    )
    doc["file"] = out.relative_to(ROOT).as_posix()
    doc["output_file"] = doc["file"]
    (man_dir / f"{der_id}.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"wrote {out.name} {arr.shape} total mm min={report.minimum:.1f} max={report.maximum:.1f}")
    print("manifests:", raw_id, der_id)


def run(region: str, run_type: Run, start: date | None, end: date | None, latest: int | None) -> None:
    try:
        if latest:
            granules = imerg.find_latest_complete(latest, run=run_type)
        else:
            granules = imerg.find_granules(start, end, run=run_type)
            want = (end - start).days + 1
            if len(granules) != want:
                raise SystemExit(f"expected {want} daily granules, CMR returned {len(granules)}")
    except imerg.ImergError as e:
        _report(e)
    print(f"{run_type.value}: {len(granules)} granule(s) {granules[0].day}..{granules[-1].day} "
          f"versions {sorted({g.version for g in granules})}")
    ingest(region, run_type, granules)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("probe", "run"):
        sp = sub.add_parser(name)
        sp.add_argument("--region", default="chennai", choices=list(REGIONS))
        if name == "run":
            sp.add_argument("--run", dest="run_type", default="final", choices=["final", "late"])
            sp.add_argument("--start", type=date.fromisoformat)
            sp.add_argument("--end", type=date.fromisoformat)
            sp.add_argument("--latest", type=int, metavar="N",
                            help="latest N complete UTC days, discovered dynamically")
    a = ap.parse_args()
    if a.cmd == "probe":
        probe(a.region)
    else:
        if a.latest:
            if a.start or a.end:
                ap.error("--latest cannot be combined with --start/--end")
        elif not (a.start and a.end):
            ap.error("give --start and --end, or --latest N")
        run(a.region, Run(a.run_type), a.start, a.end, a.latest)
