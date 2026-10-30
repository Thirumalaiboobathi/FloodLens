"""Rainfall discovery: dated manifests, stream separation, versions and every rejection path.

Trees are built in tmp_path from tiny synthetic rasters; they test the logic only and are never
listed as datasets.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from floodlens_geo.rainfall.catalog import STREAMS, discover, parse_granule, stream_status


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def make(root: Path, stream: str, start: str, days: int, version: str = "V07C",
         values=None, region: str = "chennai", raw_present: bool = True) -> str:
    """Create raw + derived manifests, granules and a 2x2 GeoTIFF; return the derived dataset id."""
    late = stream == "recent_late"
    spec = STREAMS[stream]
    d0 = date.fromisoformat(start)
    dates = [d0 + timedelta(days=i) for i in range(days)]
    end = dates[-1]
    tag = f"{d0.isoformat()}_{end.isoformat()}"
    pre = "imerg-v07-late-total" if late else "imerg-v07-total"
    rawpre = "nasa-gpm-imerg-v07-late-daily-raw" if late else "nasa-gpm-imerg-v07-daily-raw"
    names = [f"3B-DAY{'-L' if late else ''}.MS.MRG.3IMERG.{d:%Y%m%d}-S000000-E235959.{version}.nc4"
             for d in dates]
    rawdir = root / spec["raw_dir"] / region
    rawdir.mkdir(parents=True, exist_ok=True)
    sums = {}
    for n in names:
        content = n.encode()
        if raw_present:
            (rawdir / n).write_bytes(content)
        sums[n] = sha(content)
    vals = np.array(values if values is not None else [[1.5, 0.0], [3.0, 4.5]], dtype="float32")
    out = root / "data" / "processed" / "rainfall" / f"{pre}-{region}-{tag}.tif"
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", driver="GTiff", height=2, width=2, count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_origin(80.1, 13.1, 0.1, 0.1), nodata=-9999.0) as w:
        w.write(vals, 1)
    man = root / "data" / "manifests"
    man.mkdir(parents=True, exist_ok=True)
    raw_id = f"{rawpre}-{region}-{tag}"
    (man / f"{raw_id}.json").write_text(json.dumps({
        "dataset_id": raw_id, "kind": "source", "granule_checksums_sha256": sums, "derived_from": []}))
    ok = vals[vals != -9999.0]
    did = f"{pre}-{region}-{tag}"
    (man / f"{did}.json").write_text(json.dumps({
        "dataset_id": did, "kind": "derived", "provider": "NASA GES DISC / GPM",
        "source_url": "https://example.invalid/", "attribution": "a", "license": "l",
        "acquisition_timestamp": "2026-10-30T00:00:00Z", "processing_status": "processed",
        "observation_period": f"{d0.isoformat()}/{end.isoformat()} (UTC days)",
        "derived_from": [raw_id], "checksum_sha256": sha(out.read_bytes()),
        "output_file": out.relative_to(root).as_posix(), "granules": names,
        "processing_configuration": {"product": f"{spec['product']}.07 ({version}), variable 'precipitation'"},
        "validation": {"minimum": float(ok.min()), "maximum": float(ok.max())},
        "limitations": ["satellite estimate"], **({"run_type": "late"} if late else {})}))
    return did


def found(root, region="chennai"):
    return discover(root / "data" / "manifests", root, region)


def codes(rej):
    return {r.code for r in rej}


def test_dated_manifest_is_discovered_with_identity(tmp_path):
    make(tmp_path, "recent_late", "2026-10-22", 7)
    recs, rej = found(tmp_path)
    assert not rej and len(recs) == 1
    r = recs[0]
    assert (r.rainfall_stream, r.run_type, r.product_identifier) == ("recent_late", "late", "GPM_3IMERGDL")
    assert (r.observation_start, r.observation_end, r.n_days) == ("2026-10-22", "2026-10-28", 7)
    assert r.product_versions == ["V07C"] and r.scenario_id == "obs_late_20261022_20261028"
    assert r.stats["valid_cell_count"] == 4 and r.raw_verification == "verified"


def test_multiple_observation_periods_are_all_returned_and_ordered(tmp_path):
    make(tmp_path, "recent_late", "2026-10-22", 7)
    make(tmp_path, "recent_late", "2026-11-05", 7)
    make(tmp_path, "historical_final", "2023-12-03", 3, version="V07B")
    recs, rej = found(tmp_path)
    assert not rej
    assert [r.scenario_id for r in recs] == [
        "obs_final_20231203_20231205", "obs_late_20261022_20261028", "obs_late_20261105_20261111"]


def test_final_and_late_are_separate_streams_even_for_the_same_dates(tmp_path):
    make(tmp_path, "historical_final", "2026-10-22", 3, version="V07B", values=[[9, 9], [9, 9]])
    make(tmp_path, "recent_late", "2026-10-22", 3, values=[[1, 1], [1, 1]])
    recs, _ = found(tmp_path)
    by = {r.rainfall_stream: r for r in recs}
    assert set(by) == {"historical_final", "recent_late"}
    assert by["historical_final"].stats["mean_mm"] == 9.0 and by["recent_late"].stats["mean_mm"] == 1.0
    assert by["historical_final"].product_identifier != by["recent_late"].product_identifier
    assert by["historical_final"].scenario_id != by["recent_late"].scenario_id


@pytest.mark.parametrize("version", ["V07B", "V07C", "V07D"])
def test_version_is_read_from_granules_not_assumed(tmp_path, version):
    make(tmp_path, "recent_late", "2026-10-22", 2, version=version)
    recs, rej = found(tmp_path)
    assert not rej and recs[0].product_versions == [version]


def test_mixed_versions_within_one_period_are_reported(tmp_path):
    did = make(tmp_path, "recent_late", "2026-10-22", 2, version="V07B")
    p = tmp_path / "data" / "manifests" / f"{did}.json"
    m = json.loads(p.read_text())
    m["granules"][1] = m["granules"][1].replace("V07B", "V07C")
    p.write_text(json.dumps(m))
    recs, rej = found(tmp_path)
    assert not recs and codes(rej) == {"version_mismatch"}  # declared V07B vs names {B, C}


def test_no_manifests_means_nothing_is_available(tmp_path):
    (tmp_path / "data" / "manifests").mkdir(parents=True)
    recs, rej = found(tmp_path)
    st = stream_status(recs, rej)
    assert recs == [] and rej == []
    assert {s["status"] for s in st.values()} == {"unavailable"}


def test_missing_raw_manifest_is_rejected(tmp_path):
    did = make(tmp_path, "recent_late", "2026-10-22", 2)
    (tmp_path / "data" / "manifests" / "nasa-gpm-imerg-v07-late-daily-raw-chennai-2026-10-22_2026-10-23.json").unlink()
    recs, rej = found(tmp_path)
    assert not recs and rej[0].code == "raw_manifest_missing" and rej[0].dataset_id == did
    assert stream_status(recs, rej)["recent_late"]["status"] == "pending"


def test_invalid_processed_checksum_is_rejected(tmp_path):
    make(tmp_path, "historical_final", "2023-12-03", 3, version="V07B")
    tif = next((tmp_path / "data" / "processed" / "rainfall").glob("*.tif"))
    tif.write_bytes(tif.read_bytes() + b"\0")
    recs, rej = found(tmp_path)
    assert not recs and codes(rej) == {"processed_checksum_mismatch"}


def test_missing_geotiff_is_rejected(tmp_path):
    make(tmp_path, "recent_late", "2026-10-22", 2)
    next((tmp_path / "data" / "processed" / "rainfall").glob("*.tif")).unlink()
    recs, rej = found(tmp_path)
    assert not recs and codes(rej) == {"processed_file_missing"}


def test_raw_checksum_mismatch_is_rejected_but_absent_raw_is_a_warning(tmp_path):
    make(tmp_path, "recent_late", "2026-10-22", 2)
    raw = next((tmp_path / "data/raw/imerg-late/chennai").glob("*.nc4"))
    raw.write_bytes(b"tampered")
    recs, rej = found(tmp_path)
    assert not recs and codes(rej) == {"raw_checksum_mismatch"}

    other = tmp_path / "b"
    make(other, "recent_late", "2026-10-22", 2, raw_present=False)
    recs, rej = found(other)
    assert not rej and recs[0].raw_verification == "not_present" and recs[0].warnings


def test_corrupted_geotiff_is_rejected_not_crashed(tmp_path):
    did = make(tmp_path, "recent_late", "2026-10-22", 2)
    tif = next((tmp_path / "data" / "processed" / "rainfall").glob("*.tif"))
    tif.write_bytes(b"not a tiff")
    p = tmp_path / "data" / "manifests" / f"{did}.json"
    m = json.loads(p.read_text())
    m["checksum_sha256"] = sha(b"not a tiff")  # checksum is right, content is garbage
    p.write_text(json.dumps(m))
    recs, rej = found(tmp_path)
    assert not recs and codes(rej) == {"processed_unreadable"}


@pytest.mark.parametrize("mutate,code", [
    (lambda m: m.update(observation_period="2026-10-22/2026-10-25 (UTC days)"), "period_mismatch"),
    (lambda m: m.update(observation_period="garbage"), "period_invalid"),
    (lambda m: m.update(processing_status="downloaded"), "not_processed"),
    (lambda m: m.update(run_type="final"), "product_stream_mismatch"),
    (lambda m: m["processing_configuration"].update(product="GPM_3IMERGDF.07 (V07C)"),
     "product_stream_mismatch"),
    (lambda m: m.update(granules=m["granules"][:1]), "granule_dates_not_contiguous"),
    (lambda m: m.update(granules=["bogus.nc4", m["granules"][1]]), "granule_name_invalid"),
    (lambda m: m.update(validation={"minimum": 99.0, "maximum": 100.0}), "statistics_mismatch"),
    (lambda m: m.update(output_file="../../etc/passwd"), "path_outside_store"),
    (lambda m: m.pop("processing_configuration"), "product_missing"),
])
def test_corrupted_or_contradictory_metadata_is_rejected(tmp_path, mutate, code):
    did = make(tmp_path, "recent_late", "2026-10-22", 2)
    p = tmp_path / "data" / "manifests" / f"{did}.json"
    m = json.loads(p.read_text())
    mutate(m)
    p.write_text(json.dumps(m))
    recs, rej = found(tmp_path)
    assert not recs and codes(rej) == {code}


def test_late_granules_cannot_be_published_as_final(tmp_path):
    did = make(tmp_path, "historical_final", "2023-12-03", 2, version="V07B")
    p = tmp_path / "data" / "manifests" / f"{did}.json"
    m = json.loads(p.read_text())
    m["granules"] = [g.replace("3B-DAY.", "3B-DAY-L.") for g in m["granules"]]
    p.write_text(json.dumps(m))
    recs, rej = found(tmp_path)
    assert not recs and codes(rej) == {"product_stream_mismatch"}


def test_unreadable_manifest_is_reported_and_other_regions_ignored(tmp_path):
    make(tmp_path, "recent_late", "2026-10-22", 2)
    make(tmp_path, "recent_late", "2026-10-22", 2, region="mumbai")
    (tmp_path / "data" / "manifests" / "imerg-v07-total-broken.json").write_text("{not json")
    recs, rej = found(tmp_path)
    assert [r.region_id for r in recs] == ["chennai"]
    assert codes(rej) == {"manifest_unreadable"}


def test_zero_rainfall_is_a_valid_value_and_nodata_is_missing(tmp_path):
    make(tmp_path, "recent_late", "2026-10-22", 2, values=[[0.0, -9999.0], [2.0, 0.0]])
    r = found(tmp_path)[0][0]
    by = {(c["row"], c["col"]): c["accumulation_mm"] for c in r.cells}
    assert by[(0, 0)] == 0.0 and by[(0, 1)] is None and by[(1, 1)] == 0.0
    assert (r.stats["valid_cell_count"], r.stats["missing_cell_count"]) == (3, 1)
    assert r.stats["missing_percentage"] == 25.0 and r.stats["min_mm"] == 0.0


def test_parse_granule_final_vs_late():
    f = parse_granule("3B-DAY.MS.MRG.3IMERG.20231203-S000000-E235959.V07B.nc4")
    lt = parse_granule("3B-DAY-L.MS.MRG.3IMERG.20261022-S000000-E235959.V07C.nc4")
    assert (f["run_type"], f["product"]) == ("final", "GPM_3IMERGDF")
    assert (lt["run_type"], lt["product"], lt["product_version"]) == ("late", "GPM_3IMERGDL", "V07C")


def test_randomerror_never_appears_in_records(tmp_path):
    make(tmp_path, "recent_late", "2026-10-22", 2)
    p = next((tmp_path / "data" / "manifests").glob("imerg-v07-late-total*.json"))
    m = json.loads(p.read_text())
    m["randomError_investigation"] = {"x": 1}
    m["data_quality_notes"] = ["randomError is odd"]
    p.write_text(json.dumps(m))
    rec = found(tmp_path)[0][0]
    assert "randomerror" not in json.dumps(rec.to_dict()).lower()


def test_real_repository_discovery_if_present():
    root = Path(__file__).resolve().parents[2]
    if not (root / "data" / "processed" / "rainfall").is_dir():
        pytest.skip("no processed rainfall")
    recs, rej = discover(root / "data" / "manifests", root, "chennai")
    assert not rej
    streams = {r.rainfall_stream: r for r in recs}
    assert streams["historical_final"].product_versions == ["V07B"]
    assert streams["recent_late"].observation_start == "2026-10-22"
