"""IMERG tests. All data here is SYNTHETIC test fixture data, not rainfall observations."""

from datetime import date

import numpy as np
import pytest
import rasterio
import requests
import xarray as xr

from floodlens_geo.ingestion import imerg
from floodlens_geo.ingestion.imerg import (
    AuthenticationError,
    AuthorizationError,
    DownloadError,
    Granule,
    ImergDataError,
    RateLimitError,
)

SECRET = "SECRET-TOKEN-VALUE-123"


def make_nc(path, *, units="mm/day", fill=-9999.9, value=None, south_up=False, lat0=12.05):
    """Synthetic file mimicking V07 daily layout: precipitation(time, lon, lat)."""
    lon = np.round(np.arange(79.05, 81.0, 0.1), 2)  # centres
    lat = np.round(np.arange(lat0, 14.0, 0.1), 2)
    a = np.arange(lon.size * lat.size, dtype="float64").reshape(lon.size, lat.size) / 10
    if value is not None:
        a = np.full_like(a, value)
    a[0, 0] = fill
    if not south_up:
        lat = lat[::-1]
        a = a[:, ::-1]
    ds = xr.Dataset(
        {"precipitation": (("time", "lon", "lat"), a[None], {"units": units, "_FillValue": fill})},
        coords={"time": [0], "lon": lon, "lat": lat},
    )
    ds.to_netcdf(path, engine="h5netcdf")


def name(d):
    return f"3B-DAY.MS.MRG.3IMERG.{d}-S000000-E235959.V07B.nc4"


def test_token_must_come_from_env():
    with pytest.raises(AuthenticationError) as e:
        imerg.get_token({})
    assert "EARTHDATA_TOKEN" in str(e.value)
    assert imerg.get_token({"EARTHDATA_TOKEN": f" {SECRET} "}) == SECRET


def test_read_daily_units_fill_and_orientation(tmp_path):
    p = tmp_path / name("20231203")
    make_nc(p, south_up=True)
    f = imerg.read_daily(p)
    assert f.day == date(2023, 12, 3)
    assert f.res == pytest.approx(0.1)
    assert np.isnan(f.data).sum() == 1  # the fill value became NaN, not -9999.9 rain
    assert np.nanmin(f.data) >= 0
    # north-up: first row is the northernmost latitude (13.95 centre -> 14.0 edge)
    assert f.north == pytest.approx(14.0)
    assert f.west == pytest.approx(79.0)


def test_read_daily_rejects_wrong_units(tmp_path):
    p = tmp_path / name("20231203")
    make_nc(p, units="mm/hr")
    with pytest.raises(ImergDataError, match="units"):
        imerg.read_daily(p)


def test_accumulate_and_clip_and_write(tmp_path):
    for d in ("20231203", "20231204"):
        make_nc(tmp_path / name(d), value=10.0)
    fields = [imerg.read_daily(tmp_path / name(d)) for d in ("20231203", "20231204")]
    total, ref = imerg.accumulate(fields)
    assert np.nanmax(total) == pytest.approx(20.0)
    assert np.isnan(total).sum() == 1  # a cell missing on a day stays missing
    arr, tr = imerg.clip_to_bbox(total, ref, (80.05, 12.85, 80.35, 13.25))
    assert arr.shape == (5, 4)  # cells 80.0-80.4 x 12.8-13.3
    assert tr.a == pytest.approx(0.1)
    assert tr.c == pytest.approx(80.0) and tr.f == pytest.approx(13.3)
    out = tmp_path / "o.tif"
    imerg.write_geotiff(out, arr, tr, {"UNITS": "mm"})
    with rasterio.open(out) as r:
        assert r.crs.to_epsg() == 4326 and r.nodata == imerg.OUT_NODATA
        assert r.read(1).max() == pytest.approx(20.0)


def test_accumulate_rejects_gap(tmp_path):
    for d in ("20231203", "20231205"):
        make_nc(tmp_path / name(d), value=1.0)
    fields = [imerg.read_daily(tmp_path / name(d)) for d in ("20231203", "20231205")]
    with pytest.raises(ImergDataError, match="consecutive"):
        imerg.accumulate(fields)


def test_clip_outside_grid(tmp_path):
    make_nc(tmp_path / name("20231203"), value=1.0)
    f = imerg.read_daily(tmp_path / name("20231203"))
    with pytest.raises(ImergDataError, match="outside"):
        imerg.clip_to_bbox(f.data, f, (10.0, 0.0, 11.0, 1.0))


# ---- HTTP behaviour with a scripted fake session (no network) -------------------------


class Resp:
    def __init__(self, code, body=b"", headers=None):
        self.status_code, self._b, self.headers = code, body, headers or {}

    def iter_content(self, n):
        yield self._b

    def close(self):
        pass


class Sess:
    def __init__(self, *items):
        self.items, self.calls = list(items), []

    def get(self, url, headers=None, **kw):
        self.calls.append((url, dict(headers or {})))
        it = self.items.pop(0)
        if isinstance(it, Exception):
            raise it
        return it


G = Granule(date(2023, 12, 3), "t", "https://data.gesdisc.earthdata.nasa.gov/data/x/f.nc4")


def dl(tmp_path, *items, **kw):
    s = Sess(*items)
    try:
        return imerg.download_granule(G, tmp_path, SECRET, s, sleep=lambda _: None, **kw), s
    except imerg.ImergError as e:
        e.sess = s
        raise


@pytest.mark.parametrize(
    ("code", "exc"), [(401, AuthenticationError), (403, AuthorizationError), (404, DownloadError)]
)
def test_http_errors_fail_fast_without_leaking_token(tmp_path, code, exc):
    with pytest.raises(exc) as e:
        dl(tmp_path, Resp(code))
    assert SECRET not in str(e.value)
    assert len(e.value.sess.calls) == 1  # no pointless retries on auth errors


def test_429_retries_then_succeeds(tmp_path):
    p, s = dl(tmp_path, Resp(429, headers={"Retry-After": "1"}), Resp(200, b"data"))
    assert p.read_bytes() == b"data" and len(s.calls) == 2


def test_429_exhausted(tmp_path):
    with pytest.raises(RateLimitError):
        dl(tmp_path, *[Resp(429)] * 3, retries=2)


def test_network_failure_retried_and_reported_without_detail(tmp_path):
    boom = requests.ConnectionError(f"proxy said Bearer {SECRET}")
    with pytest.raises(DownloadError) as e:
        dl(tmp_path, boom, boom, retries=1)
    assert SECRET not in str(e.value) and "ConnectionError" in str(e.value)


def test_html_login_page_is_auth_failure(tmp_path):
    with pytest.raises(AuthenticationError):
        dl(tmp_path, Resp(200, b"<html>", {"Content-Type": "text/html"}))


def test_token_not_sent_to_untrusted_redirect_host(tmp_path):
    redir = Resp(302, headers={"Location": "https://evil.example.com/f"})
    _, s = dl(tmp_path, redir, Resp(200, b"ok"))
    assert s.calls[0][1] == {"Authorization": f"Bearer {SECRET}"}
    assert s.calls[1][1] == {}


def test_no_plain_http_redirect(tmp_path):
    with pytest.raises(DownloadError, match="non-https"):
        dl(tmp_path, Resp(302, headers={"Location": "http://data.gesdisc.earthdata.nasa.gov/f"}))


# --- grid-axis validation (regression: real V07 lon is float32, steps 0.099991..0.100006) ---

def _f32_lon():
    return (np.arange(3600) * 0.1 - 179.95).astype("float32")  # as stored by NASA


def test_axis_exact_spacing_float64():
    first, inc = imerg.check_axis(np.arange(-89.95, 90, 0.1).round(2), "lat", 90.0)
    assert inc and first == pytest.approx(-89.95, abs=1e-9)


def test_axis_accepts_float32_quantisation_of_real_lon():
    lon = _f32_lon()
    steps = np.diff(lon.astype("float64"))
    assert abs(steps.min() - 0.0999908447265625) < 1e-12  # the value that used to be rejected
    first, inc = imerg.check_axis(lon, "lon", 180.0)
    assert inc and first == pytest.approx(-179.95, abs=1e-6)


def test_axis_decreasing_is_reported():
    _, inc = imerg.check_axis(np.arange(13.95, 12.0, -0.1).round(2), "lat", 90.0)
    assert not inc


def test_axis_rejects_wrong_spacing():
    with pytest.raises(ImergDataError, match="spacing"):
        imerg.check_axis(np.arange(0, 30, 0.09), "lon", 180.0)


def test_axis_rejects_float32_with_wrong_spacing():
    with pytest.raises(ImergDataError, match="spacing"):
        imerg.check_axis((np.arange(3600) * 0.1001 - 179.95).astype("float32"), "lon", 180.0)


def test_axis_rejects_non_monotonic_and_irregular():
    x = np.arange(0, 10, 0.1).round(2)
    x[40], x[41] = x[41], x[40]
    with pytest.raises(ImergDataError, match="monotonic"):
        imerg.check_axis(x, "lat", 90.0)
    y = np.arange(0, 10, 0.1).round(2)
    y[50:] += 0.05  # one gap of 0.15
    with pytest.raises(ImergDataError, match="spacing"):
        imerg.check_axis(y, "lat", 90.0)


@pytest.mark.parametrize("bad", [np.array([1.0]), np.array([0.0, 0.1, np.nan]),
                                 np.array([0, 1, 2]), np.zeros((2, 2))])
def test_axis_rejects_missing_or_invalid(bad):
    with pytest.raises(ImergDataError):
        imerg.check_axis(bad, "lon", 180.0)


def test_axis_rejects_out_of_range():
    with pytest.raises(ImergDataError, match="outside"):
        imerg.check_axis(np.arange(89.5, 91, 0.1).round(2), "lat", 90.0)


def test_read_daily_accepts_float32_lon(tmp_path):
    p = tmp_path / name("20231203")
    make_nc(p)
    with xr.open_dataset(p, engine="h5netcdf", mask_and_scale=False) as ds:
        ds = ds.load()
    ds = ds.assign_coords(lon=ds["lon"].astype("float32"))
    p.unlink()
    ds.to_netcdf(p, engine="h5netcdf")
    f = imerg.read_daily(p)
    assert f.res == 0.1 and f.west == pytest.approx(79.0, abs=1e-6)


def test_read_daily_rejects_bad_spacing(tmp_path):
    p = tmp_path / name("20231203")
    make_nc(p)
    with xr.open_dataset(p, engine="h5netcdf", mask_and_scale=False) as ds:
        ds = ds.load()
    ds = ds.assign_coords(lon=ds["lon"] * 0.9)
    p.unlink()
    ds.to_netcdf(p, engine="h5netcdf")
    with pytest.raises(ImergDataError, match="spacing"):
        imerg.read_daily(p)
