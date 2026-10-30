"""Late Run / multi-run IMERG tests. All data here is SYNTHETIC test fixture data, not rainfall."""

from __future__ import annotations

import fnmatch
import hashlib
import importlib.util
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from floodlens_geo.ingestion import imerg
from floodlens_geo.ingestion.imerg import Granule, ImergDataError, Run
from floodlens_geo.metadata.integrity import check_manifests
from floodlens_geo.metadata.manifest import load_all

SECRET = "SECRET-TOKEN-VALUE-123"
BBOX = (80.05, 12.85, 80.35, 13.25)
CODES = {Run.FINAL: "", Run.LATE: "-L", Run.EARLY: "-E"}
SPEC = importlib.util.spec_from_file_location(
    "ingest_imerg_script", Path(__file__).resolve().parents[2] / "scripts" / "ingest_imerg.py"
)
script = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(script)


def fname(d: date, run: Run = Run.LATE, ver: str = "V07C") -> str:
    return f"3B-DAY{CODES[run]}.MS.MRG.3IMERG.{d:%Y%m%d}-S000000-E235959.{ver}.nc4"


def entry(d: date, run: Run = Run.LATE, ver: str = "V07C", t_end: str = "23:59:59.999Z") -> dict:
    name = fname(d, run, ver)
    return {
        "title": name.removesuffix(".nc4"),
        "time_start": f"{d}T00:00:00.000Z",
        "time_end": f"{d}T{t_end}",
        "updated": f"{d + timedelta(days=1)}T14:29:00.000Z",
        "granule_size": 27.2,
        "links": [
            {"rel": "http://esipfed.org/ns/fedsearch/1.1/data#",
             "href": f"https://data.gesdisc.earthdata.nasa.gov/data/GPM_L3/X.07/{d:%Y/%m}/{name}"},
            {"rel": "http://esipfed.org/ns/fedsearch/1.1/metadata#", "href": "https://x/y.xml"},
        ],
    }


class FakeResp:
    status_code = 200

    def __init__(self, batch):
        self._batch = batch

    def json(self):
        return {"feed": {"entry": self._batch}}


class FakeCMR:
    """Pages a fixed entry list exactly like CMR page_size/page_num."""

    def __init__(self, entries):
        self.entries = entries
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None, **kw):
        assert "Authorization" not in kw.get("headers", {})
        self.calls.append(dict(params))
        n, p = params["page_size"], params["page_num"]
        return FakeResp(self.entries[(p - 1) * n : p * n])


def make_file(path: Path, d: date, *, rain=0.5, overrides=None, header=True, stop="23:59:59.999Z",
              start="00:00:00.000Z", time_day=None):
    """Synthetic daily file with the quality fields; overrides = {var: {(lon, lat): value}}."""
    lon = np.round(np.arange(79.05, 81.0, 0.1), 2)
    lat = np.round(np.arange(13.95, 12.0, -0.1), 2)  # north-up like the real files
    shape = (1, lon.size, lat.size)
    v = {
        "precipitation": np.full(shape, rain, "float32"),
        "precipitation_cnt": np.full(shape, 40, "int8"),
        "precipitation_cnt_cond": np.full(shape, 10, "int8"),
        "randomError": np.full(shape, 2.0, "float32"),
        "randomError_cnt": np.full(shape, 48, "int8"),
        "probabilityLiquidPrecipitation": np.full(shape, 100, "int8"),
    }
    v["precipitation"][0, 0, 0] = -9999.9
    v["randomError"][0, 0, 0] = 0.0  # like the real product: 0, not fill, where precipitation is fill
    v["randomError_cnt"][0, 0, 0] = 0
    for var, cells in (overrides or {}).items():
        for (x, y), val in cells.items():
            v[var][0, int(np.argmin(abs(lon - x))), int(np.argmin(abs(lat - y)))] = val
    attrs = {"precipitation": {"units": "mm/day", "_FillValue": np.float32(-9999.9)},
             "precipitation_cnt": {"units": "count"},
             "precipitation_cnt_cond": {"units": "count"},
             "randomError": {"units": "mm/day", "_FillValue": np.float32(-9999.9)},
             "randomError_cnt": {"units": "count"},
             "probabilityLiquidPrecipitation": {"units": "percent"}}
    ds = xr.Dataset(
        {k: (("time", "lon", "lat"), a, attrs[k]) for k, a in v.items()},
        coords={"time": [np.datetime64(time_day or d.isoformat(), "ns")], "lon": lon, "lat": lat},
    )
    if header:
        ds.attrs["FileHeader"] = (f"StartGranuleDateTime={d}T{start};\n"
                                  f"StopGranuleDateTime={d}T{stop}")
    ds.to_netcdf(path, engine="h5netcdf")


# ---- filename parsing and run identification ------------------------------------------------


@pytest.mark.parametrize(
    ("run", "ver"),
    [(Run.FINAL, "V07B"), (Run.LATE, "V07B"), (Run.LATE, "V07C"), (Run.EARLY, "V07C"),
     (Run.FINAL, "V07C")],
)
def test_parse_granule_name_run_and_version(run, ver):
    p = imerg.parse_granule_name(fname(date(2026, 10, 28), run, ver))
    assert (p.run, p.version, p.day) == (run, ver, date(2026, 10, 28))


def test_parse_accepts_url_and_future_minor_versions():
    url = "https://h/data/GPM_L3/x.07/2026/10/" + fname(date(2026, 10, 28), Run.LATE, "V07D")
    assert imerg.parse_granule_name(url).version == "V07D"


@pytest.mark.parametrize(
    "bad",
    ["3B-DAY.MS.MRG.3IMERG.20231203-S000000-E235959.V07B.nc", "random.nc4",
     "3B-HHR-L.MS.MRG.3IMERG.20231203-S000000-E235959.V07B.nc4",
     "3B-DAY-L.MS.MRG.3IMERG.20231303-S000000-E235959.V07B.nc4",
     "3B-DAY-X.MS.MRG.3IMERG.20231203-S000000-E235959.V07B.nc4"],
)
def test_parse_rejects_non_daily_names(bad):
    with pytest.raises(ImergDataError):
        imerg.parse_granule_name(bad)


def test_collection_short_names_distinguish_runs():
    assert imerg.SHORT_NAMES == {Run.FINAL: "GPM_3IMERGDF", Run.LATE: "GPM_3IMERGDL",
                                 Run.EARLY: "GPM_3IMERGDE"}
    assert imerg.SHORT_NAME == "GPM_3IMERGDF"  # backward-compatible alias


# ---- CMR discovery -----------------------------------------------------------------------------


def test_find_granules_pagination_and_query():
    days = [date(2026, 10, 20) + timedelta(days=i) for i in range(5)]
    cmr = FakeCMR([entry(d) for d in days])
    out = imerg.find_granules(days[0], days[-1], cmr, run=Run.LATE, page_size=2)
    assert [g.day for g in out] == days
    assert [c["page_num"] for c in cmr.calls] == [1, 2, 3]
    assert {c["short_name"] for c in cmr.calls} == {"GPM_3IMERGDL"}
    assert {c["version"] for c in cmr.calls} == {"07"}


def test_find_granules_pagination_exact_multiple_fetches_extra_page():
    days = [date(2026, 10, 20) + timedelta(days=i) for i in range(4)]
    cmr = FakeCMR([entry(d) for d in days])
    assert len(imerg.find_granules(days[0], days[-1], cmr, run=Run.LATE, page_size=2)) == 4
    assert len(cmr.calls) == 3  # 2 + 2 + empty page


def test_find_granules_mixed_versions_keep_per_granule_version():
    cmr = FakeCMR([entry(date(2026, 3, 2), ver="V07B"), entry(date(2026, 3, 3), ver="V07C")])
    out = imerg.find_granules(date(2026, 3, 2), date(2026, 3, 3), cmr, run=Run.LATE)
    assert [g.version for g in out] == ["V07B", "V07C"]
    assert all(g.run is Run.LATE and g.granule_id.endswith(g.version) for g in out)


def test_find_granules_final_default_is_backward_compatible():
    d = date(2023, 12, 3)
    cmr = FakeCMR([entry(d, Run.FINAL, "V07B")])
    (g,) = imerg.find_granules(d, d, cmr)
    assert cmr.calls[0]["short_name"] == "GPM_3IMERGDF"
    assert (g.run, g.version, g.day) == (Run.FINAL, "V07B", d)
    assert Granule(d, "t", "https://data.gesdisc.earthdata.nasa.gov/x/f.nc4").run is Run.FINAL


def test_runs_are_never_mixed_in_a_query():
    d = date(2023, 12, 3)
    with pytest.raises(ImergDataError, match="mix processing streams"):
        imerg.find_granules(d, d, FakeCMR([entry(d, Run.FINAL, "V07B")]), run=Run.LATE)
    with pytest.raises(ImergDataError, match="mix processing streams"):
        imerg.find_granules(d, d, FakeCMR([entry(d, Run.LATE, "V07B")]), run=Run.FINAL)


def test_cmr_time_must_match_filename_date():
    e = entry(date(2026, 10, 28))
    e["time_start"] = "2026-10-27T00:00:00.000Z"
    with pytest.raises(ImergDataError, match="does not match"):
        imerg.find_granules(date(2026, 10, 27), date(2026, 10, 28), FakeCMR([e]), run=Run.LATE)


def test_discovery_needs_no_token(monkeypatch):
    monkeypatch.delenv("EARTHDATA_TOKEN", raising=False)
    d = date(2026, 10, 28)
    assert imerg.find_granules(d, d, FakeCMR([entry(d)]), run=Run.LATE)  # public CMR
    with pytest.raises(imerg.AuthenticationError, match="EARTHDATA_TOKEN"):
        imerg.get_token()


# ---- latest complete UTC day ----------------------------------------------------------------

NOW = datetime(2026, 10, 30, 4, 34, tzinfo=UTC)


def window(last: date, n: int):
    return [last - timedelta(days=k) for k in range(n - 1, -1, -1)]


def test_latest_complete_skips_incomplete_and_unfinished_days():
    entries = [entry(d) for d in window(date(2026, 10, 28), 12)]
    entries.append(entry(date(2026, 10, 29), t_end="11:59:59.999Z"))  # published but partial
    entries.append(entry(date(2026, 10, 30), t_end="03:59:59.999Z"))  # today, not finished
    got = imerg.find_latest_complete(7, FakeCMR(entries), run=Run.LATE, now=NOW, page_size=5)
    assert [g.day for g in got] == window(date(2026, 10, 28), 7)
    assert all(imerg.is_complete_day(g) for g in got)


def test_latest_complete_rejects_a_full_looking_granule_for_an_unfinished_day():
    # CMR claims a full-day extent for "today": the day has not ended, so it cannot be complete
    entries = [entry(d) for d in window(date(2026, 10, 30), 8)]
    got = imerg.find_latest_complete(7, FakeCMR(entries), run=Run.LATE, now=NOW)
    assert got[-1].day == date(2026, 10, 29)


def test_latest_complete_discovers_dynamically_when_a_new_day_appears():
    entries = [entry(d) for d in window(date(2026, 10, 28), 8)]
    first = imerg.find_latest_complete(7, FakeCMR(entries), run=Run.LATE, now=NOW)
    entries.append(entry(date(2026, 10, 29)))
    later = imerg.find_latest_complete(7, FakeCMR(entries), run=Run.LATE, now=NOW)
    assert (first[-1].day, later[-1].day) == (date(2026, 10, 28), date(2026, 10, 29))


def test_latest_complete_mixed_versions_across_the_seam():
    days = window(date(2026, 3, 5), 7)
    entries = [entry(d, ver="V07B" if d < date(2026, 3, 3) else "V07C") for d in days]
    got = imerg.find_latest_complete(
        7, FakeCMR(entries), run=Run.LATE, now=datetime(2026, 3, 6, 5, tzinfo=UTC)
    )
    assert sorted({g.version for g in got}) == ["V07B", "V07C"]


def test_latest_complete_gap_and_duplicate_raise():
    days = window(date(2026, 10, 28), 7)
    gap = [entry(d) for d in days if d != date(2026, 10, 25)]
    with pytest.raises(ImergDataError, match="2026-10-25"):
        imerg.find_latest_complete(7, FakeCMR(gap), run=Run.LATE, now=NOW)
    dup = [entry(d) for d in days] + [entry(days[-1], ver="V07B")]
    with pytest.raises(ImergDataError, match="more than one"):
        imerg.find_latest_complete(7, FakeCMR(dup), run=Run.LATE, now=NOW)


def test_latest_complete_nothing_complete():
    with pytest.raises(ImergDataError, match="no complete"):
        imerg.find_latest_complete(
            3, FakeCMR([entry(date(2026, 10, 29), t_end="06:00:00.000Z")]), run=Run.LATE, now=NOW
        )


@pytest.mark.parametrize(
    ("t_end", "ok"),
    [("23:59:59.999Z", True), ("23:59:59.000Z", True), ("23:59:58.000Z", False),
     ("12:00:00.000Z", False)],
)
def test_incomplete_granule_rejection(t_end, ok):
    d = date(2026, 10, 28)
    g = imerg.find_granules(d, d, FakeCMR([entry(d, t_end=t_end)]), run=Run.LATE)[0]
    assert imerg.is_complete_day(g) is ok


def test_late_start_is_incomplete():
    d = date(2026, 10, 28)
    e = entry(d)
    e["time_start"] = f"{d}T06:00:00.000Z"
    g = imerg.find_granules(d, d, FakeCMR([e]), run=Run.LATE)[0]
    assert not imerg.is_complete_day(g)


# ---- internal file date vs filename ---------------------------------------------------------


def test_internal_date_matches(tmp_path):
    d = date(2026, 10, 28)
    p = tmp_path / fname(d)
    make_file(p, d)
    cov = imerg.verify_internal_date(p, d)
    assert cov.start == datetime(2026, 10, 28, tzinfo=UTC) and cov.stop.second == 59


def test_internal_date_mismatch_with_filename(tmp_path):
    p = tmp_path / fname(date(2026, 10, 28))
    make_file(p, date(2026, 10, 27))  # content is another day
    with pytest.raises(ImergDataError, match="internal start"):
        imerg.verify_internal_date(p, date(2026, 10, 28))


def test_internal_time_axis_mismatch(tmp_path):
    d = date(2026, 10, 28)
    p = tmp_path / fname(d)
    make_file(p, d, time_day="2026-10-27")
    with pytest.raises(ImergDataError, match="time axis"):
        imerg.verify_internal_date(p, d)


def test_internal_partial_day_and_missing_header(tmp_path):
    d = date(2026, 10, 28)
    p = tmp_path / fname(d)
    make_file(p, d, stop="11:59:59.999Z")
    with pytest.raises(ImergDataError, match="end of day"):
        imerg.verify_internal_date(p, d)
    q = tmp_path / fname(d, ver="V07B")
    make_file(q, d, header=False)
    with pytest.raises(ImergDataError, match="FileHeader"):
        imerg.verify_internal_date(q, d)


# ---- quality-field statistics ---------------------------------------------------------------


def test_quality_stats_separate_valid_zero_from_missing(tmp_path):
    d = date(2026, 10, 28)
    p = tmp_path / fname(d)
    inside = (80.15, 13.05)
    make_file(p, d, rain=0.0, overrides={
        "precipitation": {inside: -9999.9, (80.25, 13.05): 7.0},
        "precipitation_cnt": {(80.15, 12.95): 0, (80.25, 13.05): 12},
        "randomError": {(80.25, 13.05): 9.5},
        "probabilityLiquidPrecipitation": {(80.25, 13.05): 55},
    })
    f = imerg.read_daily(p)
    q = imerg.quality_stats(p, f, BBOX)
    rain = q["precipitation"]
    assert rain["aoi_cells"] == 20
    assert rain["missing_cells"] == 1  # only the fill value is "missing"
    assert rain["valid_cells"] == 19
    assert rain["zero_rain_cells"] == 18  # valid zeros are not missing
    assert rain["wet_cells"] == 1
    assert rain["zero_rain_with_no_retrievals"] == 1  # flagged, not dropped
    assert q["precipitation_cnt"]["min"] == 0 and q["precipitation_cnt"]["max"] == 40
    assert q["randomError"]["max"] == 9.5 and q["randomError"]["units"] == "mm/day"
    assert q["probabilityLiquidPrecipitation"]["min"] == 55
    assert q["probabilityLiquidPrecipitation"]["units"] == "percent"
    assert q["precipitation_cnt_cond"]["mean"] == 10


def test_all_zero_day_is_valid_not_missing(tmp_path):
    d = date(2026, 10, 28)
    p = tmp_path / fname(d)
    make_file(p, d, rain=0.0)
    f = imerg.read_daily(p)
    arr, _ = imerg.clip_to_bbox(f.data, f, BBOX)
    assert np.isfinite(arr).all() and arr.max() == 0.0
    stats = script.daily_stats(f, BBOX)
    assert stats["aoi_missing_cells"] == 0 and stats["aoi_mm_per_day"]["max"] == 0.0
    assert stats["global_valid_cells"] == f.data.size - 1  # just the one fill cell


def test_quality_stats_requires_the_variables(tmp_path):
    d = date(2026, 10, 28)
    p = tmp_path / fname(d)
    make_file(p, d)
    f = imerg.read_daily(p)
    q = tmp_path / "bad.nc4"
    xr.open_dataset(p, engine="h5netcdf").drop_vars("randomError").to_netcdf(q, engine="h5netcdf")
    with pytest.raises(ImergDataError, match="randomError"):
        imerg.quality_stats(q, f, BBOX)


# ---- randomError: as-delivered, unverified, masked, never summed or surfaced -----------------


def _qs(tmp_path, d=date(2026, 10, 28), run=Run.LATE, ver="V07C", **kw):
    p = tmp_path / fname(d, run, ver)
    make_file(p, d, **kw)
    return imerg.quality_stats(p, imerg.read_daily(p), BBOX)


def test_random_error_excluded_where_precipitation_is_fill(tmp_path):
    cell = (80.15, 13.05)
    # hostile: a non-zero error and a non-zero count under a fill precipitation cell
    q = _qs(tmp_path, rain=1.0, overrides={"precipitation": {cell: -9999.9},
                                           "randomError": {cell: 5000.0}})
    err = q["randomError"]
    assert err["excluded_cells"] == 1 and err["valid_cells"] == 19
    assert err["max"] == 2.0  # 5000 never entered the statistics
    assert q["precipitation"]["missing_cells"] == 1


def test_random_error_excluded_where_count_is_zero(tmp_path):
    cell = (80.15, 13.05)
    q = _qs(tmp_path, rain=1.0, overrides={"randomError_cnt": {cell: 0},
                                           "randomError": {cell: 0.0}})
    err = q["randomError"]
    assert err["excluded_cells"] == 1 and err["valid_cells"] == 19 and err["min"] == 2.0
    assert q["precipitation"]["valid_cells"] == 20  # precipitation itself is still valid there


def test_valid_zero_precipitation_is_preserved_and_keeps_its_error(tmp_path):
    q = _qs(tmp_path, rain=0.0)
    assert q["precipitation"]["valid_cells"] == 20 and q["precipitation"]["zero_rain_cells"] == 20
    assert q["precipitation"]["missing_cells"] == 0
    assert q["randomError"]["valid_cells"] == 20 and q["randomError"]["excluded_cells"] == 0
    assert q["randomError_cnt"]["min"] == 48


def test_random_error_values_are_preserved_and_labelled_unverified(tmp_path):
    q = _qs(tmp_path, rain=1.0, overrides={"randomError": {(80.25, 13.05): 3944.688}})
    err = q["randomError"]
    assert err["max"] == 3944.688 and err["min"] == 2.0 and err["units"] == "mm/day"  # as delivered
    assert err["status"] == imerg.RANDOM_ERROR_STATUS == "as_delivered_unverified"
    assert "not a validated uncertainty" in err["note"] and "do not" in err["note"].lower()
    hyp = err["hypothesis"]
    assert hyp["status"] == "unverified_research_hypothesis"
    assert hyp["formula"].startswith("sqrt(24 * V / N)") and "not confirmed" in hyp["assumption"]
    assert hyp["max"] == round(float(np.sqrt(24 * 3944.688 / 48)), 3)
    assert hyp["units"] != err["units"]  # never presented as the stored unit


@pytest.mark.parametrize(
    ("run", "ver"), [(Run.FINAL, "V07B"), (Run.LATE, "V07B"), (Run.LATE, "V07C")]
)
def test_random_error_handling_is_identical_across_runs_and_versions(tmp_path, run, ver):
    cell = (80.15, 13.05)
    q = _qs(tmp_path, run=run, ver=ver, rain=0.5,
            overrides={"precipitation": {cell: -9999.9}, "randomError_cnt": {(80.25, 13.05): 0}})
    err = q["randomError"]
    assert err["excluded_cells"] == 2 and err["valid_cells"] == 18
    assert err["status"] == "as_delivered_unverified"
    assert err["hypothesis"]["status"] == "unverified_research_hypothesis"
    assert q["precipitation"]["missing_cells"] == 1 and q["precipitation"]["valid_cells"] == 19


def test_quality_requires_random_error_cnt(tmp_path):
    d = date(2026, 10, 28)
    p = tmp_path / fname(d)
    make_file(p, d)
    q = tmp_path / "nocnt.nc4"
    xr.open_dataset(p, engine="h5netcdf").drop_vars("randomError_cnt").to_netcdf(q, engine="h5netcdf")
    with pytest.raises(ImergDataError, match="randomError_cnt"):
        imerg.quality_stats(q, imerg.read_daily(p), BBOX)


def test_random_error_never_enters_rainfall_totals(tmp_path):
    days = [date(2026, 10, 27), date(2026, 10, 28)]
    fields = []
    for d in days:
        p = tmp_path / fname(d)
        make_file(p, d, rain=3.0, overrides={"randomError": {(80.15, 13.05): 1e6}})
        fields.append(imerg.read_daily(p))
    total, _ = imerg.accumulate(fields)
    ok = np.isfinite(total)
    assert np.allclose(total[ok], 6.0)  # sum of precipitation only; the 1e6 errors are absent
    assert imerg.VARIABLE == "precipitation"


def test_random_error_not_summed_in_output_manifests_or_raster(project):
    gs = granules([date(2026, 10, 27), date(2026, 10, 28)], Run.LATE)
    script.ingest("chennai", Run.LATE, gs)
    man_dir = project / "data" / "manifests"
    for m in man_dir.glob("*.json"):
        doc = json.loads(m.read_text(encoding="utf-8"))
        assert doc["data_quality_notes"] == [imerg.RANDOM_ERROR_NOTE]
        for key in doc:
            if key not in ("daily_provenance", "data_quality_notes"):
                assert "randomError" not in json.dumps(doc[key]), key
        # only per-day, never a multi-day sum or merged statistic
        assert all("randomError" in d["quality"] and len(d["quality"]) == 6
                   for d in doc["daily_provenance"])
    tif = next((project / "data" / "processed" / "rainfall").glob("*.tif"))
    import rasterio

    with rasterio.open(tif) as r:
        assert r.count == 1 and "randomError" not in str(r.descriptions) + str(r.tags())


def test_random_error_is_kept_out_of_api_frontend_schemas_and_scores():
    root = Path(__file__).resolve().parents[2]
    allowed = {"geospatial/floodlens_geo/ingestion/imerg.py", "scripts/annotate_random_error.py",
               "scripts/annotate_random_error_investigation.py"}
    roots = ["geospatial/floodlens_geo", "services", "apps/web/src", "schemas", "scripts", "infrastructure"]
    hits = []
    for r in roots:
        for p in (root / r).rglob("*"):
            if (p.is_file() and p.suffix in {".py", ".ts", ".tsx", ".js", ".json", ".css"}
                    and "node_modules" not in p.parts and "__pycache__" not in p.parts
                    and "/tests/" not in p.as_posix()):
                rel = p.relative_to(root).as_posix()
                if rel not in allowed and "randomError" in p.read_text(encoding="utf-8", errors="ignore"):
                    hits.append(rel)
    assert hits == []


def test_annotate_script_is_additive_and_idempotent(project):
    ann = importlib.util.module_from_spec(spec := importlib.util.spec_from_file_location(
        "annotate_random_error", Path(__file__).resolve().parents[2] / "scripts" / "annotate_random_error.py"))
    spec.loader.exec_module(ann)
    gs = granules([date(2026, 10, 27), date(2026, 10, 28)], Run.LATE)
    script.ingest("chennai", Run.LATE, gs)
    raw = next((project / "data" / "manifests").glob("nasa-gpm-imerg-v07-late-daily-raw-*.json"))
    doc = json.loads(raw.read_text(encoding="utf-8"))
    # emulate a manifest written before the annotation existed
    del doc["data_quality_notes"]
    for d in doc["daily_provenance"]:
        for k in ("status", "note", "excluded_cells", "hypothesis"):
            del d["quality"]["randomError"][k]
        del d["quality"]["randomError_cnt"]
    new = ann.annotate(doc, root=project)
    assert ann.annotate(new, root=project) == new  # idempotent

    def flatten(o, pre=""):
        if isinstance(o, dict):
            for k, v in o.items():
                yield from flatten(v, f"{pre}/{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                yield from flatten(v, f"{pre}/{i}")
        else:
            yield pre, o

    before, after = dict(flatten(doc)), dict(flatten(new))
    assert all(after[k] == v for k, v in before.items())  # no original value changed or removed
    assert any("hypothesis" in k for k in set(after) - set(before))
    assert after["/data_quality_notes/0"] == imerg.RANDOM_ERROR_NOTE


# ---- manifests, separation of runs ----------------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "data" / "manifests").mkdir(parents=True)
    monkeypatch.setattr(script, "ROOT", tmp_path)
    monkeypatch.setenv("EARTHDATA_TOKEN", SECRET)

    def fake_download(g, dest, token, *a, **kw):
        assert token == SECRET
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / g.filename
        if not out.exists():
            make_file(out, g.day, rain=1.0 + g.day.day / 10)
        return out

    monkeypatch.setattr(imerg, "download_granule", fake_download)
    return tmp_path


def granules(days, run, ver_of=lambda d: "V07C"):
    cmr = FakeCMR([entry(d, run, ver_of(d)) for d in days])
    return imerg.find_granules(days[0], days[-1], cmr, run=run)


def test_late_ingest_writes_separate_files_and_full_provenance(project):
    days = [date(2026, 3, 1) + timedelta(days=i) for i in range(4)]  # spans V07B -> V07C
    gs = granules(days, Run.LATE, lambda d: "V07B" if d < date(2026, 3, 3) else "V07C")
    script.ingest("chennai", Run.LATE, gs)

    raw_dir = project / "data" / "raw" / "imerg-late" / "chennai"
    assert sorted(p.name for p in raw_dir.glob("*.nc4")) == sorted(g.filename for g in gs)
    assert not (project / "data" / "raw" / "imerg").exists()
    tif = project / "data" / "processed" / "rainfall" / "imerg-v07-late-total-chennai-2026-03-01_2026-03-04.tif"
    assert tif.exists()

    man = json.loads((project / "data/manifests/imerg-v07-late-total-chennai-2026-03-01_2026-03-04.json")
                     .read_text(encoding="utf-8"))
    assert man["run_type"] == "late" and man["product_versions"] == ["V07B", "V07C"]
    assert man["observation_period"] == "2026-03-01/2026-03-04 (UTC days)"
    assert "Period spans product versions" in " ".join(man["limitations"])
    assert man["processing_configuration"]["missing_data"].startswith("fill value")
    assert man["file"] == tif.relative_to(project).as_posix()
    assert len(man["daily_provenance"]) == 4
    first, last = man["daily_provenance"][0], man["daily_provenance"][-1]
    for key in ("observation_date", "utc_coverage", "dataset_id", "collection_concept_id", "doi",
                "run_type", "product_version", "granule_id", "source_url", "retrieved_at",
                "retrieval_basis", "sha256", "size_bytes", "native_resolution_deg", "crs",
                "variable", "units", "global_valid_cells", "global_missing_percent", "quality",
                "cmr_updated"):
        assert key in first, key
    assert (first["product_version"], last["product_version"]) == ("V07B", "V07C")
    assert first["dataset_id"] == "GPM_3IMERGDL.07" and first["run_type"] == "late"
    assert first["sha256"] == hashlib.sha256((raw_dir / gs[0].filename).read_bytes()).hexdigest()
    assert first["utc_coverage"]["start"].startswith("2026-03-01T00:00:00")
    for k in ("precipitation", "precipitation_cnt", "randomError", "probabilityLiquidPrecipitation"):
        assert k in first["quality"]
    assert SECRET not in json.dumps(man)
    for m in (project / "data" / "manifests").glob("*.json"):
        assert SECRET not in m.read_text(encoding="utf-8")

    # manifests validate and the integrity check (derived file + every granule checksum) passes
    assert len(load_all(project / "data" / "manifests")) == 2
    assert check_manifests(project / "data" / "manifests", project) == []


def test_integrity_detects_a_tampered_granule(project):
    gs = granules([date(2026, 10, 27), date(2026, 10, 28)], Run.LATE)
    script.ingest("chennai", Run.LATE, gs)
    victim = next((project / "data" / "raw" / "imerg-late" / "chennai").glob("*.nc4"))
    victim.write_bytes(victim.read_bytes() + b"x")
    problems = check_manifests(project / "data" / "manifests", project)
    assert any("granule" in p and victim.name in p for p in problems)


def test_final_and_late_for_the_same_dates_never_mix(project):
    days = [date(2023, 12, 3), date(2023, 12, 4)]
    script.ingest("chennai", Run.FINAL, granules(days, Run.FINAL, lambda d: "V07B"))
    rain = project / "data" / "processed" / "rainfall"
    final_tif = rain / "imerg-v07-total-chennai-2023-12-03_2023-12-04.tif"
    before = hashlib.sha256(final_tif.read_bytes()).hexdigest()
    final_manifest = project / "data/manifests/imerg-v07-total-chennai-2023-12-03_2023-12-04.json"
    final_text = final_manifest.read_text(encoding="utf-8")

    script.ingest("chennai", Run.LATE, granules(days, Run.LATE, lambda d: "V07B"))

    assert hashlib.sha256(final_tif.read_bytes()).hexdigest() == before  # Final untouched
    assert final_manifest.read_text(encoding="utf-8") == final_text
    late_tif = rain / "imerg-v07-late-total-chennai-2023-12-03_2023-12-04.tif"
    assert late_tif.exists() and late_tif != final_tif
    assert {p.name for p in (project / "data/raw/imerg/chennai").glob("*.nc4")}.isdisjoint(
        {p.name for p in (project / "data/raw/imerg-late/chennai").glob("*.nc4")})
    # the pattern run_exposure.py uses for Final rainfall must not match Late outputs
    assert fnmatch.fnmatch(final_tif.name, "imerg-v07-total-chennai-*.tif")
    assert not fnmatch.fnmatch(late_tif.name, "imerg-v07-total-chennai-*.tif")
    assert check_manifests(project / "data" / "manifests", project) == []


def test_final_naming_is_unchanged_for_backward_compatibility(project):
    days = [date(2023, 12, 3), date(2023, 12, 4), date(2023, 12, 5)]
    script.ingest("chennai", Run.FINAL, granules(days, Run.FINAL, lambda d: "V07B"))
    names = {p.name for p in (project / "data" / "manifests").glob("*.json")}
    assert names == {"imerg-v07-total-chennai-2023-12-03_2023-12-05.json",
                     "nasa-gpm-imerg-v07-daily-raw-chennai-2023-12-03_2023-12-05.json"}
    doc = json.loads((project / "data/manifests/imerg-v07-total-chennai-2023-12-03_2023-12-05.json")
                     .read_text(encoding="utf-8"))
    assert doc["run_type"] == "final" and doc["processing_status"] == "processed"
    assert doc["daily_statistics"][0]["date"] == "2023-12-03"  # key set the API/docs already use
    assert doc["granules"][0].endswith("V07B.nc4")
    assert (project / "data/raw/imerg/chennai").is_dir()


def test_ingest_refuses_to_overwrite(project):
    gs = granules([date(2026, 10, 27), date(2026, 10, 28)], Run.LATE)
    script.ingest("chennai", Run.LATE, gs)
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        script.ingest("chennai", Run.LATE, gs)


def test_ingest_without_token_fails_before_any_download(project, monkeypatch):
    monkeypatch.delenv("EARTHDATA_TOKEN", raising=False)
    gs = granules([date(2026, 10, 27), date(2026, 10, 28)], Run.LATE)
    with pytest.raises(SystemExit) as e:
        script.ingest("chennai", Run.LATE, gs)
    assert e.value.code == 2
    assert not (project / "data" / "raw").exists()


def test_ingest_rejects_file_whose_content_is_another_day(project, monkeypatch):
    def bad_download(g, dest, token, *a, **kw):
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / g.filename
        make_file(out, g.day - timedelta(days=1))
        return out

    monkeypatch.setattr(imerg, "download_granule", bad_download)
    gs = granules([date(2026, 10, 27), date(2026, 10, 28)], Run.LATE)
    with pytest.raises(SystemExit):
        script.ingest("chennai", Run.LATE, gs)
    assert not list((project / "data" / "manifests").glob("*.json"))


def test_run_configuration_keeps_streams_apart():
    final, late = script.RUNS[Run.FINAL], script.RUNS[Run.LATE]
    assert final["raw_dir"] == "imerg" and late["raw_dir"] == "imerg-late"
    assert late["stem"] != final["stem"] and not late["stem"].startswith(final["stem"])
    assert "Late" in late["attribution"] and "Final" in final["attribution"]
    assert Run.EARLY not in script.RUNS  # Early is identified, never ingested

