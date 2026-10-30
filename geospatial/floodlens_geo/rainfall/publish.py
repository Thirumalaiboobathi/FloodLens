"""Turn validated :class:`RainfallRecord` objects into the API artifact documents (plain dicts).

Time-independent only: freshness depends on the request time and is added by the API. The records
come from ``catalog.discover`` so nothing here can publish an unvalidated dataset. The delivered
daily error-estimate field is not in the record and cannot reach these documents.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from floodlens_geo.rainfall.catalog import STREAMS, RainfallRecord

STATISTIC_SCOPE = (
    "Statistics are over the valid 0.1 degree IMERG cells covering the analysis box, for the stated "
    "UTC observation period, at native resolution. They are not point or neighbourhood values.")
COVERAGE_CAVEAT = (
    "A small sample of coarse satellite grid cells over the analysis box. It describes those cells, "
    "not neighbourhood-level rainfall, and says nothing about a point inside a cell.")


def version_label(versions: list[str]) -> str:
    return versions[0] if len(versions) == 1 else "mixed: " + ", ".join(versions)


def _utc(d: str, end: bool) -> str:
    t = time(23, 59, 59) if end else time(0, 0, 0)
    return datetime.combine(date.fromisoformat(d), t, UTC).isoformat().replace("+00:00", "Z")


def cell_state(v: float | None) -> str:
    return "nodata" if v is None else "zero" if v == 0 else "rain"


def publish_docs(rec: RainfallRecord) -> dict[str, dict]:
    spec = STREAMS[rec.rainfall_stream]
    g, st = rec.grid, rec.stats
    label = (f"{spec['label']}: {rec.observation_start} to {rec.observation_end} "
             f"({version_label(rec.product_versions)})")
    meta = {
        "scenario_id": rec.scenario_id, "region_id": rec.region_id, "label": label,
        "rainfall_stream": rec.rainfall_stream, "run_type": rec.run_type,
        "product_identifier": rec.product_identifier,
        "product_version": version_label(rec.product_versions), "product_versions": rec.product_versions,
        "observation_start": rec.observation_start, "observation_end": rec.observation_end,
        "observation_start_utc": _utc(rec.observation_start, False),
        "observation_end_utc": _utc(rec.observation_end, True), "n_days": rec.n_days,
        "retrieval_timestamp": rec.retrieval_timestamp,
        "native_resolution": "0.1 degree (~10 km) IMERG grid, not resampled",
        "native_resolution_deg": g["cell_size_deg"],
        "units": f"mm (rainfall accumulation over {rec.n_days} UTC day{'s' if rec.n_days != 1 else ''})",
        "spatial_coverage": {
            "bbox": g["bounds_wgs84"], "cells_x": g["width"], "cells_y": g["height"],
            "description": (f"{g['width']} x {g['height']} whole IMERG cells intersecting the "
                            f"{rec.region_id.title()} analysis box")},
        "valid_cell_count": st["valid_cell_count"], "missing_cell_count": st["missing_cell_count"],
        "missing_percentage": st["missing_percentage"], "source": rec.source,
        "provenance_reference": {"derived_dataset_id": rec.dataset_id,
                                 "raw_dataset_id": rec.raw_dataset_id,
                                 "raw_verification": rec.raw_verification},
        "processing_status": "published",
        "raster": {"kind": "geotiff", "width": g["width"], "height": g["height"],
                   "cell_size_deg": g["cell_size_deg"], "nodata": g["nodata"],
                   "sha256": rec.processed_sha256,
                   "href": f"/v1/regions/{rec.region_id}/rainfall/{rec.scenario_id}/raster"},
        "limitations": rec.limitations,
    }
    mean = st["mean_mm"]
    summary = {
        "scenario_id": rec.scenario_id, "region_id": rec.region_id,
        "rainfall_stream": rec.rainfall_stream, "observation_start": rec.observation_start,
        "observation_end": rec.observation_end, "n_days": rec.n_days,
        "accumulation_mm": {k: st[k] for k in ("min_mm", "mean_mm", "median_mm", "max_mm",
                                               "valid_cell_count", "missing_cell_count",
                                               "missing_percentage")},
        "mean_daily_rate_mm_per_day": None if mean is None else round(mean / rec.n_days, 3),
        "daily": [{k: d[k] for k in ("date", "product_version", "min_mm", "mean_mm", "median_mm",
                                     "max_mm", "aoi_missing_cells", "global_missing_percent")}
                  for d in rec.daily],
        "statistic_scope": STATISTIC_SCOPE, "coverage_caveat": COVERAGE_CAVEAT}
    provenance = {
        "scenario_id": rec.scenario_id, "region_id": rec.region_id,
        "rainfall_stream": rec.rainfall_stream, "product_identifier": rec.product_identifier,
        "product_versions": rec.product_versions, "derived_dataset_id": rec.dataset_id,
        "raw_dataset_id": rec.raw_dataset_id, "raw_verification": rec.raw_verification,
        "processed_sha256": rec.processed_sha256, "retrieval_timestamp": rec.retrieval_timestamp,
        "source": rec.source,
        "granules": [{"file": x["file"], "date": x["date"], "product_version": x["product_version"]}
                     for x in rec.granules],
        "processing": rec.processing, "limitations": rec.limitations, "warnings": rec.warnings}
    cells = {
        "type": "FeatureCollection", "scenario_id": rec.scenario_id, "region_id": rec.region_id,
        "rainfall_stream": rec.rainfall_stream, "native_resolution_deg": g["cell_size_deg"],
        "units": "mm", "resampled": False,
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [c["west"], c["south"]], [c["east"], c["south"]], [c["east"], c["north"]],
                [c["west"], c["north"]], [c["west"], c["south"]]]]},
            "properties": {"row": c["row"], "col": c["col"],
                           "state": cell_state(c["accumulation_mm"]),
                           "accumulation_mm": c["accumulation_mm"]}} for c in rec.cells]}
    return {"meta": meta, "summary": summary, "provenance": provenance, "cells": cells}
