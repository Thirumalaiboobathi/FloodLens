"""Exposure engine tests on SYNTHETIC rasters/vectors (not real terrain or rainfall)."""

import json

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from pydantic import ValidationError
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, box

from floodlens_geo.exposure import engine, inputs, outputs
from floodlens_geo.exposure.config import ExposureConfig, Transforms, Weights
from floodlens_geo.metadata.integrity import check_manifests, sha256

T = Transforms()
NORM = engine.Normalisation(twi=(4.0, 14.0), log10_sca=(1.5, 5.5))


def grid(path, arr, *, crs="EPSG:32644", cell=30.0, x0=400000.0, y0=1450000.0, nodata=-9999.0):
    h, w = arr.shape
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1, dtype="float32",
                       crs=crs, transform=from_origin(x0, y0, cell, cell), nodata=nodata) as d:
        d.write(np.where(np.isfinite(arr), arr, nodata).astype("float32"), 1)
    return path


def layers(n=6, **over):
    base = {
        "hand": np.full((n, n), 2.0), "twi": np.full((n, n), 9.0), "slope": np.full((n, n), 1.0),
        "flow_accumulation": np.full((n, n), 1000.0), "elevation": np.full((n, n), 5.0),
        "water_distance": np.full((n, n), 200.0),
    }
    base.update(over)
    return base


# ---- normalisation ----------------------------------------------------------------------


def test_scores_are_bounded_and_monotone():
    hand = np.array([[0.0, 1.0, 5.0, 50.0]])
    s = engine.indicator_scores(layers_one(hand=hand), T, NORM)
    h = s["hand"][0]
    assert h[0] == 1.0 and np.all(np.diff(h) < 0) and h.min() > 0
    for name, arr in s.items():
        if arr is not None:
            assert np.nanmin(arr) >= 0 and np.nanmax(arr) <= 1, name


def layers_one(**over):
    base = {k: np.full((1, 4), v) for k, v in
            {"hand": 2.0, "twi": 9.0, "slope": 1.0, "flow_accumulation": 1000.0, "elevation": 5.0,
             "water_distance": 200.0}.items()}
    base.update(over)
    return base


def test_slope_twi_flow_transforms():
    s = engine.indicator_scores(layers_one(
        slope=np.array([[0.0, 2.5, 5.0, 40.0]]),
        twi=np.array([[0.0, 4.0, 9.0, 99.0]]),
        flow_accumulation=np.array([[0.0, 10 ** 1.5, 10 ** 3.5, 1e9]])), T, NORM)
    assert s["slope"][0].tolist() == [1.0, 0.5, 0.0, 0.0]
    assert s["twi"][0].tolist() == [0.0, 0.0, 0.5, 1.0]  # clipped to the robust bounds
    assert np.isnan(s["flow_accumulation"][0, 0])  # SCA 0 has no log: missing, not "lowest"
    assert s["flow_accumulation"][0, 1] == 0.0 and s["flow_accumulation"][0, 3] == 1.0


def test_robust_bounds_constant_layer_and_empty():
    lo, hi = engine.robust_bounds(np.full(10, 3.0), (2, 98))
    assert hi > lo
    with pytest.raises(engine.GridError):
        engine.robust_bounds(np.full(4, np.nan), (2, 98))


def test_derive_normalisation_ignores_nonpositive_sca():
    twi = np.linspace(0, 10, 101)
    sca = np.concatenate([[0.0, -1.0], 10 ** np.linspace(1, 5, 99)])
    n = engine.derive_normalisation(twi, sca, T)
    assert n.twi[0] == pytest.approx(0.2) and 1.0 < n.log10_sca[0] < 1.2


# ---- weights, missing inputs, nodata -----------------------------------------------------------


def test_weight_renormalisation_is_scale_invariant():
    sc = engine.indicator_scores(layers(), T, NORM)
    a, _ = engine.combine(sc, Weights(), 0.0)
    w2 = Weights(**{k: v * 7 for k, v in Weights().as_dict().items()})
    b, _ = engine.combine(sc, w2, 0.0)
    assert np.allclose(a, b)


def test_missing_input_renormalises_and_lowers_completeness():
    full = engine.indicator_scores(layers(), T, NORM, rainfall_mm=100.0)
    i_full, c_full = engine.combine(full, Weights(), 0.0)
    assert np.allclose(c_full, 1.0)
    no_rain = engine.indicator_scores(layers(), T, NORM)  # rainfall layer absent
    i, c = engine.combine(no_rain, Weights(), 0.0)
    assert np.allclose(c, 0.9)
    terrain = [k for k in no_rain if k != "rainfall" and no_rain[k] is not None]
    wt = Weights().as_dict()
    expected = sum(wt[k] * no_rain[k][0, 0] for k in terrain) / sum(wt[k] for k in terrain)
    assert i[0, 0] == pytest.approx(expected)  # weights renormalised over what is present
    assert (i_full >= i).all()  # adding rainfall forcing never lowers the index


def test_rainfall_amplifies_but_is_bounded_and_not_a_plain_average():
    sc = engine.indicator_scores(layers(hand=np.full((6, 6), 0.0), twi=np.full((6, 6), 99.0),
                                        slope=np.zeros((6, 6)), elevation=np.zeros((6, 6)),
                                        water_distance=np.zeros((6, 6)),
                                        flow_accumulation=np.full((6, 6), 1e9)),
                                 T, NORM, rainfall_mm=10_000.0)
    index, _ = engine.combine(sc, Weights(), 0.0)
    assert np.allclose(index, 1.0)  # most exposed terrain stays at 1, never pulled down by rain
    low = engine.indicator_scores(layers(hand=np.full((6, 6), 80.0), elevation=np.full((6, 6), 90.0),
                                         water_distance=np.full((6, 6), 9e3)), T, NORM, 0.0, 0.0)
    hi = engine.indicator_scores(layers(hand=np.full((6, 6), 80.0), elevation=np.full((6, 6), 90.0),
                                        water_distance=np.full((6, 6), 9e3)), T, NORM, 400.0, 400.0)
    a, b = engine.combine(low, Weights(), 0)[0], engine.combine(hi, Weights(), 0)[0]
    assert (b > a).all() and (b <= 1).all()


def test_nodata_in_one_input_drops_only_that_input_for_that_cell():
    hand = np.full((6, 6), 2.0)
    hand[1, 1] = np.nan
    sc = engine.indicator_scores(layers(hand=hand), T, NORM)
    index, comp = engine.combine(sc, Weights(), 0.0)
    assert comp[1, 1] == pytest.approx(0.6)  # hand (0.30) and rainfall (0.10) absent
    assert comp[0, 0] == pytest.approx(0.9)  # rainfall layer was not supplied at all
    assert np.isfinite(index[1, 1]) and index[1, 1] != index[0, 0]


def test_cells_below_min_completeness_are_not_scored():
    hand = np.full((3, 3), np.nan)
    sc = engine.indicator_scores(layers(n=3, hand=hand, twi=np.full((3, 3), np.nan)), T, NORM)
    index, comp = engine.combine(sc, Weights(), 0.9)
    assert np.isnan(index).all() and (comp < 0.9).all()


def test_no_inputs_raises_and_bad_weights_rejected():
    with pytest.raises(engine.GridError):
        engine.combine(dict.fromkeys(engine.INPUTS), Weights(), 0.5)
    with pytest.raises(ValidationError):
        Weights(hand=-1)
    with pytest.raises(ValidationError):
        Weights(**dict.fromkeys(engine.INPUTS, 0.0))
    with pytest.raises(ValidationError):
        Transforms(robust_percentiles=(90, 10))


def test_zero_weight_input_is_ignored_and_explanation_sums_to_index():
    sc = engine.indicator_scores(layers(), T, NORM, rainfall_mm=150.0)
    w = Weights(**{**Weights().as_dict(), "slope": 0.0})
    index, comp = engine.combine(sc, w, 0.0)
    assert np.allclose(comp, 1.0)  # a zero-weight input is not part of the denominator
    exp = engine.explain(sc, w, 0, 0)
    assert "slope" not in exp and "rainfall" in exp
    assert sum(v["contribution"] for v in exp.values()) == pytest.approx(index[0, 0])


# ---- hand / rainfall / scenarios -----------------------------------------------------------------


def test_rainfall_score_and_reach_widen_with_depth():
    assert engine.reach_factor(None, T) == 1.0 == engine.reach_factor(100.0, T)
    assert engine.reach_factor(400.0, T) == pytest.approx(2.0)
    lo = engine.indicator_scores(layers(), T, NORM, 50.0, 50.0)
    hi = engine.indicator_scores(layers(), T, NORM, 300.0, 300.0)
    for k in ("rainfall", "hand", "water_distance"):
        assert np.all(hi[k] > lo[k]), k  # same terrain, more rain: larger exposure score
    assert np.array_equal(hi["slope"], lo["slope"])  # terrain-only inputs unchanged


def test_hand_score_far_above_stream_is_low():
    s = engine.indicator_scores(layers(hand=np.full((6, 6), 40.0)), T, NORM)
    assert s["hand"][0, 0] < 0.001


def test_scenarios_end_to_end_monotone_and_class_thresholds(tmp_path):
    n = 40
    xx = np.mgrid[0:n, 0:n][1]
    ref = grid(tmp_path / "elev.tif", 2.0 + xx * 0.5)
    f = {
        "elevation": ref,
        "slope": grid(tmp_path / "slope.tif", 0.5 + xx * 0.05),
        "twi": grid(tmp_path / "twi.tif", 12.0 - xx * 0.2),
        "flow_accumulation": grid(tmp_path / "sca.tif", 10 ** (2 + 3 * (1 - xx / n))),
        "hand": grid(tmp_path / "hand.tif", xx * 0.4),
        "water_distance": grid(tmp_path / "wd.tif", xx * 30.0),
    }
    cfg = ExposureConfig()
    norm = engine.derive_normalisation(engine.read_masked(f["twi"]),
                                       engine.read_masked(f["flow_accumulation"]), cfg.transforms)
    kw = {"files": f, "ref_path": ref, "out_dir": tmp_path, "cfg": cfg, "norm": norm,
          "exclude": None}
    base, _, _ = engine.run_scenario("baseline", rainfall=None, reach_mm=None, thresholds=None, **kw)
    thr = engine.quantile_thresholds(engine.read_decimated(base, 1), cfg.n_classes)
    assert len(thr) == 4 and thr == sorted(thr)
    b, bc, _ = engine.run_scenario("baseline", rainfall=None, reach_mm=None, thresholds=thr, **kw)
    r, rc, rcomp = engine.run_scenario("rain200", rainfall=200.0, reach_mm=200.0,
                                       thresholds=thr, **kw)
    bi, ri = engine.read_masked(b), engine.read_masked(r)
    assert np.isfinite(bi).all() and (ri >= bi - 1e-9).all()  # more rain never lowers the score
    cb, cr = engine.read_masked(bc), engine.read_masked(rc)
    assert (cr >= cb).all() and cr.max() == cfg.n_classes
    assert cb[:, :3].min() >= cb[:, -3:].max()  # low x = low ground = higher class
    assert engine.read_masked(rcomp).min() == pytest.approx(1.0)
    assert engine.classify(np.array([np.nan, 0.0, 1.0]), [0.3, 0.6]).tolist() == [0, 1, 3]


def test_exclude_mask_and_unscored_terrainless_cells(tmp_path):
    ref = grid(tmp_path / "e.tif", np.full((8, 8), 3.0))
    hand = np.full((8, 8), 1.0)
    hand[:, 4:] = np.nan
    f = {"elevation": grid(tmp_path / "e2.tif", np.where(np.arange(8) < 4, 3.0, np.nan)[None, :]
                           * np.ones((8, 1))), "hand": grid(tmp_path / "h.tif", hand)}
    ex = grid(tmp_path / "x.tif", np.where(np.arange(8)[:, None] == 0, 1.0, 0.0) * np.ones((1, 8)),
              nodata=255)
    norm = engine.Normalisation((0, 1), (0, 1))
    idx, _, _ = engine.run_scenario("t", f, ref, tmp_path, ExposureConfig(), norm, 500.0, 500.0,
                                    None, exclude=ex)
    a = engine.read_masked(idx)
    assert np.isnan(a[0]).all()  # excluded row (permanent water)
    assert np.isnan(a[1:, 4:]).all()  # no terrain at all: rainfall alone never creates a score
    assert np.isfinite(a[1:, :4]).all()


# ---- grids: resolution, CRS, missing --------------------------------------------------------------


def test_align_coarser_raster_to_fine_grid(tmp_path):
    ref = grid(tmp_path / "ref.tif", np.zeros((40, 40)))  # 30 m
    coarse = grid(tmp_path / "c.tif", np.full((4, 4), 7.0), cell=300.0)  # 300 m, same extent
    a = engine.align_to(coarse, ref, rasterio.enums.Resampling.nearest)
    assert a.shape == (40, 40) and np.allclose(a, 7.0)
    assert not engine.same_grid(coarse, ref) and engine.same_grid(ref, ref)


def test_align_across_crs_places_values_correctly(tmp_path):
    from pyproj import Transformer
    ref = grid(tmp_path / "ref.tif", np.zeros((60, 60)))
    lon0, lat0 = Transformer.from_crs(32644, 4326, always_xy=True).transform(400000, 1450000)
    # 0.002 deg cells (~220 m); the western half = 1, eastern half = 2
    arr = np.ones((10, 10))
    arr[:, 5:] = 2.0
    wgs = tmp_path / "w.tif"
    with rasterio.open(wgs, "w", driver="GTiff", height=10, width=10, count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_origin(lon0 - 0.0005, lat0 + 0.0005, 0.002, 0.002),
                       nodata=-9999) as d:
        d.write(arr.astype("float32"), 1)
    a = engine.align_to(wgs, ref, rasterio.enums.Resampling.nearest)
    assert set(np.unique(a[np.isfinite(a)])) == {1.0, 2.0}
    west, east = np.where(a == 1.0)[1], np.where(a == 2.0)[1]
    assert west.mean() < east.mean()  # orientation survives the reprojection


def test_align_rejects_missing_crs_and_no_overlap(tmp_path):
    ref = grid(tmp_path / "ref.tif", np.zeros((10, 10)))
    far = grid(tmp_path / "far.tif", np.ones((10, 10)), x0=900000.0)
    with pytest.raises(engine.GridError, match="overlap"):
        engine.align_to(far, ref)
    nocrs = tmp_path / "n.tif"
    with rasterio.open(nocrs, "w", driver="GTiff", height=4, width=4, count=1, dtype="float32",
                       transform=from_origin(0, 4, 1, 1)) as d:
        d.write(np.ones((4, 4), "float32"), 1)
    with pytest.raises(engine.GridError, match="CRS"):
        engine.align_to(nocrs, ref)


def test_water_distance_and_rasterize():
    water = np.zeros((5, 5), bool)
    water[2, 2] = True
    d = inputs.water_distance(water, 30.0)
    assert d[2, 2] == 0 and d[2, 3] == 30 and d[2, 4] == 60 and d[0, 0] == pytest.approx(30 * 8 ** 0.5)
    assert np.isnan(inputs.water_distance(np.zeros((3, 3), bool), 30.0)).all()
    m = inputs.rasterize_geometries([box(0, 0, 60, 30)], (4, 4), from_origin(0, 120, 30, 30))
    assert m.sum() == 2 and not inputs.rasterize_geometries([], (2, 2), from_origin(0, 2, 1, 1)).any()


# ---- vector products -----------------------------------------------------------------------------


@pytest.fixture
def product(tmp_path):
    n = 30
    idx = np.tile(np.linspace(0.1, 0.9, n), (n, 1))
    idx[:, :2] = np.nan
    ref = grid(tmp_path / "i.tif", idx)
    cls = engine.classify(idx, [0.3, 0.5, 0.7, 0.8])
    cp = tmp_path / "c.tif"
    with rasterio.open(ref) as r:
        prof = r.profile
    prof.update(dtype="uint8", nodata=0)
    with rasterio.open(cp, "w", **prof) as d:
        d.write(cls, 1)
    comp = grid(tmp_path / "m.tif", np.where(np.isfinite(idx), 0.9, np.nan))
    return ref, cp, comp


def test_class_polygons_valid_simplified_and_in_wgs84(product):
    _, cp, _ = product
    labels = {k: f"c{k}" for k in range(1, 6)}
    g = outputs.class_polygons(cp, 30.0, 1, labels)
    assert g.crs.to_epsg() == 4326 and g.geometry.is_valid.all() and not g.geometry.is_empty.any()
    assert set(g.exposure_class) == {1, 2, 3, 4, 5}
    assert g.area_km2.sum() == pytest.approx(28 * 30 * 900 / 1e6, rel=0.02)  # nodata cols excluded
    assert "validated" not in " ".join(g.label)


def test_road_and_facility_exposure(product):
    ref, cp, _ = product
    with rasterio.open(ref) as r:
        x0, y0 = r.transform.c, r.transform.f
    roads = gpd.GeoDataFrame(
        {"osm_id": [1, 2, 3]},
        geometry=[LineString([(x0 + 100, y0 - 450), (x0 + 200, y0 - 450)]),   # west: low index
                  LineString([(x0 + 700, y0 - 450), (x0 + 850, y0 - 450)]),   # east: high index
                  LineString([(x0 - 5000, y0), (x0 - 4000, y0)])],            # off-grid
        crs="EPSG:32644")
    rd = outputs.road_exposure(roads, ref, cp, 5)
    assert rd.exp_mean[1] > rd.exp_mean[0]
    assert rd.exp_class_max[1] >= 4 and rd.exp_class_max[2] == 0 and np.isnan(rd.exp_mean[2])
    assert rd.length_m[1] == pytest.approx(150.0)
    pts = gpd.GeoDataFrame({"osm_id": [10, 11]},
                           geometry=[Point(x0 + 800, y0 - 300), Point(x0 - 9000, y0)], crs="EPSG:32644")
    fe = outputs.facility_exposure(pts, ref, cp, None, None)
    assert fe.exp_class[0] >= 4 and fe.exp_class[1] == 0


def test_region_summary_shares_sum_to_one(product):
    ref, cp, comp = product
    with rasterio.open(ref) as r:
        region = box(r.transform.c, r.transform.f - 30 * 30, r.transform.c + 30 * 30, r.transform.f)
    s = outputs.region_summary(region, cp, ref, comp, 5)
    assert sum(s["share_by_class"].values()) == pytest.approx(1.0, abs=1e-3)
    assert s["mean_completeness"] == pytest.approx(0.9)
    assert s["scored_land_area_km2"] < s["region_area_km2"]  # nodata columns are not counted


def test_geojson_export_is_wgs84_and_precision_limited(product, tmp_path):
    _, cp, _ = product
    g = outputs.class_polygons(cp, 0.0, 1, {k: str(k) for k in range(1, 6)})
    p = tmp_path / "o" / "p.geojson"
    outputs.to_wgs84_geojson(g, p)
    d = json.loads(p.read_text())
    lon, lat = d["features"][0]["geometry"]["coordinates"][0][0]
    assert 78 < lon < 82 and 12 < lat < 14 and len(str(lon).split(".")[1]) <= 6


# ---- manifest integrity -----------------------------------------------------------------------------


def test_manifest_integrity_detects_tampering_and_missing_parents(tmp_path):
    root = tmp_path
    (root / "data" / "manifests").mkdir(parents=True)
    f = root / "out.tif"
    f.write_bytes(b"abc")
    man = {"dataset_id": "x", "derived_from": ["parent"], "products": {"a": {
        "file": "out.tif", "sha256": sha256(f)}}}
    parent = {"dataset_id": "parent"}
    (root / "data/manifests/x.json").write_text(json.dumps(man))
    (root / "data/manifests/parent.json").write_text(json.dumps(parent))
    assert check_manifests(root / "data/manifests", root) == []
    f.write_bytes(b"tampered")
    assert any("checksum" in p for p in check_manifests(root / "data/manifests", root))
    (root / "data/manifests/parent.json").unlink()
    assert any("no manifest" in p for p in check_manifests(root / "data/manifests", root))
    (root / "data/manifests/z.json").write_text(json.dumps({"dataset_id": "z", "kind": "fixture"}))
    assert any("fixtures" in p for p in check_manifests(root / "data/manifests", root))


def test_polygon_generalisation_removes_speckle_but_keeps_classes(tmp_path):
    rng = np.random.default_rng(0)
    cls = np.where(np.arange(60)[None, :] < 30, 1, 5) * np.ones((60, 1), dtype=int)
    speckle = rng.random((60, 60)) < 0.05
    cls = np.where(speckle, 3, cls).astype("uint8")
    p = tmp_path / "c.tif"
    grid(p, cls.astype("float32"), nodata=0)
    with rasterio.open(p) as r:
        prof = r.profile
    prof.update(dtype="uint8", nodata=0)
    with rasterio.open(p, "w", **prof) as d:
        d.write(cls, 1)
    labels = {k: str(k) for k in range(1, 6)}
    raw = outputs.class_polygons(p, 0.0, 1, labels, smooth_cells=1)
    smooth = outputs.class_polygons(p, 0.0, 1, labels, smooth_cells=5)
    assert len(smooth) < len(raw) / 5 and smooth.geometry.is_valid.all()
    assert set(smooth.exposure_class) == {1, 5}
