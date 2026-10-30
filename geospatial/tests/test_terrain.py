"""Terrain tests on analytic SYNTHETIC surfaces (not real terrain)."""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from floodlens_geo.terrain.derivatives import (
    NODATA,
    local_relief,
    ocean_mask,
    prepare_dem,
    run_hydrology,
    slope_degrees,
    utm_epsg,
    water_mask_from_wbm,
)


def write(path, z, *, crs="EPSG:32644", cell=30.0, origin=(0.0, None)):
    h, w = z.shape
    top = origin[1] if origin[1] is not None else h * cell
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1, dtype="float32",
                       crs=crs, transform=from_origin(origin[0], top, cell, cell),
                       nodata=NODATA) as d:
        d.write(z.astype("float32"), 1)


def test_utm_epsg():
    assert utm_epsg(80.2, 13.0) == 32644
    assert utm_epsg(-61.0, -10.0) == 32720


def test_slope_planar_matches_analytic():
    # rises 3 m per 30 m cell eastwards -> tan(theta)=0.1
    z = np.tile(np.arange(20) * 3.0, (20, 1))
    s = slope_degrees(z, 30.0)
    assert np.nanmean(s[1:-1, 1:-1]) == pytest.approx(np.degrees(np.arctan(0.1)), abs=1e-6)
    assert np.isnan(s[0, 0])  # borders are not invented


def test_slope_uses_cell_size_and_masks_nodata():
    z = np.tile(np.arange(10) * 3.0, (10, 1))
    z[5, 5] = NODATA
    s = slope_degrees(z, 90.0)
    assert np.nanmax(s) == pytest.approx(np.degrees(np.arctan(1 / 30)), abs=1e-6)
    assert np.isnan(s[4:7, 4:7]).all()  # neighbours of nodata are unknown, not guessed


def test_local_relief():
    z = np.full((9, 9), 10.0)
    z[4, 4] = 2.0
    r = local_relief(z, 2)
    assert r[4, 4] == 0 and r[4, 5] == 8 and r[0, 0] == 0  # far corner sees no pit


def test_prepare_dem_reprojects_and_clips(tmp_path):
    z = np.add.outer(np.arange(100), np.arange(100)).astype("float32")
    p = tmp_path / "t.tif"
    with rasterio.open(p, "w", driver="GTiff", height=100, width=100, count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_origin(80.0, 13.5, 0.005, 0.005),
                       nodata=-32767) as d:
        d.write(z, 1)
    out = prepare_dem([p], (80.1, 13.1, 80.3, 13.3), tmp_path / "o.tif", buffer_deg=0.0)
    with rasterio.open(out) as r:
        assert r.crs.to_epsg() == 32644 and r.res[0] == pytest.approx(30.0)
        a = r.read(1, masked=True)
        assert 0.9 < a.count() / a.size <= 1.0
        assert a.min() >= 0 and a.max() <= z.max()  # no interpolated overshoot


@pytest.fixture(scope="module")
def valley(tmp_path_factory):
    d = tmp_path_factory.mktemp("valley")
    n = 60
    cols = np.abs(np.arange(n) - n // 2) * 3.0
    z = cols[None, :] + np.arange(n)[::-1][:, None] * 0.5  # V-valley draining south
    write(d / "dem.tif", z)
    try:
        out = run_hydrology(d / "dem.tif", d / "w", stream_threshold_m2=30 * 30 * 20)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"WhiteboxTools unavailable: {e}")
    return out


def read(p):
    with rasterio.open(p) as r:
        return r.read(1, masked=True).filled(np.nan)


def test_hydrology_concentrates_flow_in_valley(valley):
    sca, twi, streams, hand = (read(getattr(valley, k)) for k in ("sca", "twi", "streams", "hand"))
    axis, ridge = 30, 2
    assert np.nanmax(sca) == pytest.approx(sca[-1, axis], rel=0.05)  # outlet on valley axis
    assert sca[40, axis] > 10 * sca[40, ridge]
    assert twi[40, axis] > twi[40, ridge]
    assert streams[40, axis] == 1
    assert hand[40, axis] == 0
    assert hand[40, ridge] > hand[40, 14] > 0  # height above stream rises away from the axis


def test_ocean_mask_keeps_inland_zero_patch():
    z = np.full((10, 10), 5.0)
    z[:, 7:] = 0.0  # sea touching the east edge
    z[2:4, 2:4] = 0.0  # inland patch (e.g. a dry pit), not connected to the edge
    m = ocean_mask(z)
    assert m[:, 7:].all() and not m[2:4, 2:4].any() and m.sum() == 30


def test_water_mask_from_wbm_keeps_land_and_ignores_outside_tile(tmp_path):
    dem = tmp_path / "dem.tif"
    write(dem, np.full((40, 40), 5.0), origin=(400000.0, 1450000.0))
    wbm = np.zeros((20, 20), "uint8")  # a tile covering the western half only (land = 0)
    wbm[:, 15:] = 1  # ocean on the tile's east side
    wbm[2:4, 2:4] = 3  # river cells
    p = tmp_path / "wbm.tif"
    with rasterio.open(p, "w", driver="GTiff", height=20, width=20, count=1, dtype="uint8",
                       crs="EPSG:32644", transform=from_origin(400000.0, 1450000.0, 30.0, 30.0)) as d:
        d.write(wbm, 1)
    m = water_mask_from_wbm([p], dem)
    assert m[:20, 15:20].all() and m[2:4, 2:4].all()
    assert not m[10:20, :15].any() and not m[20:, :].any() and not m[:, 20:].any()
