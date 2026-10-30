"""Deterministic discovery and validation of processed IMERG rainfall datasets.

A dataset is *available* only if its derived manifest, raw manifest, GeoTIFF and checksums are
consistent and its stream/product/version identity agrees everywhere. Anything else becomes an
explicit :class:`Rejection`; nothing is selected by file name order and nothing is guessed.

Streams are never merged: ``historical_final`` is GPM_3IMERGDF (Final Run), ``recent_late`` is
GPM_3IMERGDL (Late Run). Product versions (V07B, V07C, ...) are read from each granule file name
and cross-checked against the manifest; they are never assumed.

Rainfall values are satellite-estimated rainfall accumulation, never flood depth. The delivered daily
error-estimate field is not read, copied or exposed here (docs/analysis/IMERG_RANDOMERROR.md).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import rasterio

STREAMS: dict[str, dict[str, str]] = {
    "historical_final": {"product": "GPM_3IMERGDF", "run_type": "final", "raw_dir": "data/raw/imerg",
                         "label": "Historical Final Run"},
    "recent_late": {"product": "GPM_3IMERGDL", "run_type": "late", "raw_dir": "data/raw/imerg-late",
                    "label": "Recent Late Run"},
}
PROCESSED_DIR = "data/processed/rainfall"
NATIVE_RESOLUTION_DEG = 0.1
STAT_TOLERANCE_MM = 1e-3

_GRANULE = re.compile(
    r"^3B-DAY(?P<late>-L)?\.MS\.MRG\.3IMERG\.(?P<date>\d{8})-S000000-E235959\.(?P<ver>V\d{2}[A-Z])\.nc4$")
_DATASET_ID = re.compile(
    r"^imerg-v07-(?P<late>late-)?total-(?P<region>[a-z0-9]+)-(?P<s>\d{4}-\d{2}-\d{2})_(?P<e>\d{4}-\d{2}-\d{2})$")
_PRODUCT = re.compile(r"^(?P<product>GPM_3IMERGD[FL])\.07\b")
_PERIOD = re.compile(r"^(?P<s>\d{4}-\d{2}-\d{2})/(?P<e>\d{4}-\d{2}-\d{2})\b")


class Reject(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class Rejection:
    dataset_id: str
    code: str
    detail: str


@dataclass
class RainfallRecord:
    scenario_id: str
    dataset_id: str
    raw_dataset_id: str
    region_id: str
    rainfall_stream: str
    run_type: str
    product_identifier: str
    product_versions: list[str]
    observation_start: str
    observation_end: str
    n_days: int
    retrieval_timestamp: str | None
    processed_file: str
    processed_sha256: str
    grid: dict
    stats: dict
    cells: list[dict]
    daily: list[dict]
    granules: list[dict]
    raw_verification: str
    source: dict
    limitations: list[str]
    processing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_granule(name: str) -> dict:
    m = _GRANULE.match(name)
    if not m:
        raise Reject("granule_name_invalid", f"unrecognised IMERG daily granule name: {name}")
    d = datetime.strptime(m["date"], "%Y%m%d").date()
    late = bool(m["late"])
    return {"file": name, "date": d.isoformat(), "product_version": m["ver"],
            "run_type": "late" if late else "final",
            "product": "GPM_3IMERGDL" if late else "GPM_3IMERGDF"}


def stream_for_product(product: str) -> str:
    for sid, spec in STREAMS.items():
        if spec["product"] == product:
            return sid
    raise Reject("product_unknown", f"unsupported product {product}")


def _load(p: Path) -> dict:
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Reject("manifest_unreadable", f"{p.name}: {exc}") from exc
    if not isinstance(doc, dict):
        raise Reject("manifest_unreadable", f"{p.name}: not a JSON object")
    return doc


def _safe_child(root: Path, rel: str, subdir: str) -> Path:
    """Resolve ``rel`` under root/subdir; refuse anything that escapes it."""
    base = (root / subdir).resolve()
    p = (root / rel).resolve()
    if base != p and base not in p.parents:
        raise Reject("path_outside_store", f"{rel} is outside {subdir}")
    return p


def _iso(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError as exc:
        raise Reject("period_invalid", f"bad date {s!r}") from exc


def _verify_raw(raw: dict, stream: str, granule_names: list[str], root: Path, region: str) -> str:
    """Hash raw granules that are present. Returns 'verified', 'partial' or 'not_present'."""
    listed: dict[str, str] = {}
    for rel, digest in (raw.get("granule_files") or {}).items():
        listed[rel] = digest
    by_name = raw.get("granule_checksums_sha256") or {}
    if not listed:
        base = f"{STREAMS[stream]['raw_dir']}/{region}"
        listed = {f"{base}/{n}": by_name[n] for n in granule_names if n in by_name}
    names_in_raw = {Path(r).name for r in listed}
    if names_in_raw != set(granule_names):
        raise Reject("raw_granule_mismatch",
                     "raw manifest granules differ from the derived manifest granules")
    present = 0
    for rel, digest in listed.items():
        p = _safe_child(root, rel, STREAMS[stream]["raw_dir"])
        if not p.is_file():
            continue
        present += 1
        if sha256_file(p) != digest:
            raise Reject("raw_checksum_mismatch", f"checksum mismatch for raw granule {Path(rel).name}")
    if present == 0:
        return "not_present"
    return "verified" if present == len(listed) else "partial"


def _read_grid(p: Path) -> tuple[dict, list[dict], dict]:
    try:
        with rasterio.open(p) as r:
            if r.count != 1:
                raise Reject("processed_unreadable", f"expected 1 band, found {r.count}")
            if r.crs is None or r.crs.to_epsg() != 4326:
                raise Reject("processed_unreadable", "processed rainfall GeoTIFF is not EPSG:4326")
            rx, ry = abs(r.res[0]), abs(r.res[1])
            if abs(rx - NATIVE_RESOLUTION_DEG) > 1e-6 or abs(ry - NATIVE_RESOLUTION_DEG) > 1e-6:
                raise Reject("resolution_mismatch", f"cell size {rx}x{ry}, expected 0.1 degree native")
            arr = r.read(1).astype("float64")
            nodata = r.nodata
            tr, w, h, b = r.transform, r.width, r.height, r.bounds
    except rasterio.errors.RasterioError as exc:
        raise Reject("processed_unreadable", f"cannot open GeoTIFF: {exc}") from exc
    bad = ~np.isfinite(arr)
    if nodata is not None:
        bad |= arr == nodata
    cells = []
    for row in range(h):
        for col in range(w):
            west, north = tr.c + col * tr.a, tr.f + row * tr.e
            v = None if bad[row, col] else float(arr[row, col])
            cells.append({"row": row, "col": col, "west": round(west, 6), "south": round(north - ry, 6),
                          "east": round(west + rx, 6), "north": round(north, 6),
                          "accumulation_mm": None if v is None else round(v, 4)})
    valid = arr[~bad]
    stats = {"valid_cell_count": int(valid.size), "missing_cell_count": int(bad.sum()),
             "missing_percentage": round(100.0 * float(bad.sum()) / arr.size, 2)}
    if valid.size:
        stats.update(min_mm=round(float(valid.min()), 3), mean_mm=round(float(valid.mean()), 3),
                     median_mm=round(float(np.median(valid)), 3), max_mm=round(float(valid.max()), 3))
    else:
        stats.update(min_mm=None, mean_mm=None, median_mm=None, max_mm=None)
    grid = {"width": w, "height": h, "cell_size_deg": round(rx, 6), "nodata": nodata,
            "bounds_wgs84": {"west": round(b.left, 6), "south": round(b.bottom, 6),
                             "east": round(b.right, 6), "north": round(b.top, 6)}}
    return grid, cells, stats


def _processing(manifest: dict) -> list[str]:
    cfg = manifest.get("processing_configuration") or {}
    labels = (("accumulation", "Accumulation"), ("missing_data", "Missing data"),
              ("window", "Spatial window"), ("grid_validation", "Grid validation"))
    return [f"{label}: {cfg[k]}" for k, label in labels if isinstance(cfg.get(k), str)]


def build_record(manifest: dict, manifests: dict[str, dict], root: Path,
                 region: str | None = None) -> RainfallRecord:
    """Validate one derived rainfall manifest. Raises :class:`Reject` with a stable code."""
    did = manifest.get("dataset_id", "")
    m = _DATASET_ID.match(did)
    if not m:
        raise Reject("dataset_id_unrecognised", f"{did!r} does not follow the rainfall naming scheme")
    if region and m["region"] != region:
        raise Reject("region_mismatch", f"dataset is for {m['region']}, not {region}")
    pm = _PRODUCT.match((manifest.get("processing_configuration") or {}).get("product", ""))
    if not pm:
        raise Reject("product_missing", "processing_configuration.product is missing or unsupported")
    stream = stream_for_product(pm["product"])
    spec = STREAMS[stream]
    if bool(m["late"]) != (spec["run_type"] == "late"):
        raise Reject("product_stream_mismatch",
                     f"dataset id says {'late' if m['late'] else 'final'} but product is {pm['product']}")
    if manifest.get("run_type", spec["run_type"]) != spec["run_type"]:
        raise Reject("product_stream_mismatch",
                     f"run_type {manifest.get('run_type')!r} contradicts {pm['product']}")
    if manifest.get("processing_status") != "processed":
        raise Reject("not_processed", f"processing_status is {manifest.get('processing_status')!r}")

    pr = _PERIOD.match(manifest.get("observation_period") or "")
    if not pr:
        raise Reject("period_invalid", "observation_period is missing or unparsable")
    start, end = _iso(pr["s"]), _iso(pr["e"])
    if (start.isoformat(), end.isoformat()) != (m["s"], m["e"]):
        raise Reject("period_mismatch", "observation_period disagrees with the dataset id")
    n_days = (end - start).days + 1
    if n_days < 1:
        raise Reject("period_invalid", "observation end precedes start")

    gnames = manifest.get("granules") or []
    granules = [parse_granule(g) for g in gnames]
    if len(granules) != n_days or [g["date"] for g in granules] != [
            (start + timedelta(days=i)).isoformat() for i in range(n_days)]:
        raise Reject("granule_dates_not_contiguous",
                     "granule dates do not cover the observation period exactly once, in order")
    for g in granules:
        if g["product"] != pm["product"] or g["run_type"] != spec["run_type"]:
            raise Reject("product_stream_mismatch", f"granule {g['file']} belongs to {g['product']}")
    versions = sorted({g["product_version"] for g in granules})
    declared = manifest.get("product_versions")
    if declared is not None and sorted(declared) != versions:
        raise Reject("version_mismatch", f"manifest product_versions {declared} != granule names {versions}")
    stated = re.search(r"\((V\d{2}[A-Z])\)", manifest["processing_configuration"]["product"])
    if stated and versions != [stated[1]]:
        raise Reject("version_mismatch", f"configuration says {stated[1]} but granules are {versions}")

    parents = manifest.get("derived_from") or []
    if len(parents) != 1 or parents[0] not in manifests:
        raise Reject("raw_manifest_missing", f"expected exactly one raw parent manifest, got {parents}")
    raw = manifests[parents[0]]
    raw_status = _verify_raw(raw, stream, gnames, root, m["region"])

    rel = manifest.get("output_file") or manifest.get("file") or (manifest.get("validation") or {}).get("path")
    if not rel:
        raise Reject("processed_file_missing", "manifest names no processed GeoTIFF")
    p = _safe_child(root, rel, PROCESSED_DIR)
    if not p.is_file():
        raise Reject("processed_file_missing", f"{rel} does not exist")
    digest = sha256_file(p)
    if digest != manifest.get("checksum_sha256"):
        raise Reject("processed_checksum_mismatch", f"{Path(rel).name} does not match its manifest checksum")
    grid, cells, stats = _read_grid(p)
    val = manifest.get("validation") or {}
    for key, mine in (("minimum", "min_mm"), ("maximum", "max_mm")):
        if key in val and stats[mine] is not None and abs(val[key] - stats[mine]) > STAT_TOLERANCE_MM * 10:
            raise Reject("statistics_mismatch", f"manifest {key} {val[key]} != raster {stats[mine]}")

    warnings = []
    if raw_status != "verified":
        warnings.append(f"raw granules {raw_status.replace('_', ' ')}; processed GeoTIFF checksum verified")
    daily = []
    for d in manifest.get("daily_statistics") or []:
        a = d.get("aoi_mm_per_day") or {}
        gi = next((g for g in granules if g["file"] == d.get("granule")), None)
        if gi is None:
            raise Reject("daily_statistics_mismatch", f"daily statistic for unknown granule {d.get('granule')}")
        daily.append({"date": d["date"], "product_version": gi["product_version"],
                      "granule": d["granule"], "min_mm": a.get("min"), "mean_mm": a.get("mean"),
                      "median_mm": a.get("median"), "max_mm": a.get("max"),
                      "aoi_missing_cells": d.get("aoi_missing_cells"),
                      "global_missing_percent": d.get("global_missing_percent")})
    return RainfallRecord(
        scenario_id=f"obs_{'late' if spec['run_type'] == 'late' else 'final'}_{start:%Y%m%d}_{end:%Y%m%d}",
        dataset_id=did, raw_dataset_id=parents[0], region_id=m["region"], rainfall_stream=stream,
        run_type=spec["run_type"], product_identifier=pm["product"], product_versions=versions,
        observation_start=start.isoformat(), observation_end=end.isoformat(), n_days=n_days,
        retrieval_timestamp=manifest.get("acquisition_timestamp"), processed_file=rel,
        processed_sha256=digest, grid=grid, stats=stats, cells=cells, daily=daily, granules=granules,
        raw_verification=raw_status,
        source={"provider": manifest.get("provider"), "source_url": manifest.get("source_url"),
                "attribution": manifest.get("attribution"), "license": manifest.get("license")},
        limitations=list(manifest.get("limitations") or []), processing=_processing(manifest),
        warnings=warnings)


def discover(man_dir: Path, root: Path, region: str) -> tuple[list[RainfallRecord], list[Rejection]]:
    """All valid datasets for ``region`` (sorted by stream, then period) and every rejection."""
    docs: dict[str, dict] = {}
    rejections: list[Rejection] = []
    for p in sorted(man_dir.glob("*.json")):
        try:
            d = _load(p)
        except Reject as exc:
            if p.name.startswith(("imerg-", "nasa-gpm-imerg-")):
                rejections.append(Rejection(p.stem, exc.code, exc.detail))
            continue
        if isinstance(d.get("dataset_id"), str):
            docs[d["dataset_id"]] = d
    records: list[RainfallRecord] = []
    seen: dict[str, str] = {}
    for did, d in docs.items():
        if not did.startswith("imerg-") or d.get("kind") != "derived":
            continue  # raw manifests are reached through derived_from; other datasets are not rainfall
        idm = _DATASET_ID.match(did)
        if idm is None:
            rejections.append(Rejection(did, "dataset_id_unrecognised", "not a rainfall dataset id"))
            continue
        if idm["region"] != region:
            continue
        try:
            rec = build_record(d, docs, root, region)
        except Reject as exc:
            rejections.append(Rejection(did, exc.code, exc.detail))
            continue
        if rec.scenario_id in seen:
            rejections.append(Rejection(did, "duplicate_scenario",
                                        f"same stream and period as {seen[rec.scenario_id]}"))
            continue
        seen[rec.scenario_id] = did
        records.append(rec)
    records.sort(key=lambda r: (r.rainfall_stream, r.observation_start, r.observation_end))
    return records, rejections


def stream_status(records: list[RainfallRecord], rejections: list[Rejection]) -> dict[str, dict]:
    """Per stream: available / pending (only rejected candidates) / unavailable (none at all)."""
    out = {}
    for sid, spec in STREAMS.items():
        ids = [r.scenario_id for r in records if r.rainfall_stream == sid]
        rej = [x for x in rejections if _stream_hint(x.dataset_id) == sid]
        out[sid] = {"stream": sid, "label": spec["label"], "product_identifier": spec["product"],
                    "status": "available" if ids else ("pending" if rej else "unavailable"),
                    "scenario_ids": ids,
                    "rejected": [{"dataset_id": x.dataset_id, "code": x.code} for x in rej]}
    return out


def _stream_hint(dataset_id: str) -> str | None:
    m = _DATASET_ID.match(dataset_id)
    if not m:
        return None
    return "recent_late" if m["late"] else "historical_final"


__all__ = ["STREAMS", "RainfallRecord", "Reject", "Rejection", "build_record", "discover",
           "parse_granule", "sha256_file", "stream_status"]
