import numpy as np
import pytest

from floodlens_geo.exposure import sensitivity
from floodlens_geo.exposure.config import ExposureConfig


def _layers(seed=0, n=60):
    rng = np.random.default_rng(seed)
    return {
        "elevation": rng.uniform(1, 30, (n, n)), "slope": rng.uniform(0, 6, (n, n)),
        "twi": rng.uniform(5, 18, (n, n)), "flow_accumulation": 10 ** rng.uniform(1.4, 4.5, (n, n)),
        "hand": rng.uniform(0, 12, (n, n)), "water_distance": rng.uniform(0, 1500, (n, n)),
    }


def test_spearman_identity_and_reversal():
    a = np.arange(100.0)
    assert abs(sensitivity.spearman(a, a) - 1.0) < 1e-9
    assert abs(sensitivity.spearman(a, -a) + 1.0) < 1e-9


def test_identical_variant_is_fully_stable():
    lay = _layers()
    land = np.ones((60, 60), bool)
    cfg = ExposureConfig()
    base = sensitivity.score_index(lay, cfg, land)
    r = sensitivity.compare(base, base, cfg.n_classes)
    assert r["spearman"] == pytest.approx(1.0) and r["class_unchanged"] == 1.0
    assert r["top_class_overlap_jaccard"] == 1.0


def test_water_cells_are_never_scored():
    lay = _layers()
    land = np.ones((60, 60), bool)
    land[:10] = False
    idx = sensitivity.score_index(lay, ExposureConfig(), land)
    assert np.isnan(idx[:10]).all() and np.isfinite(idx[10:]).all()


def test_run_all_reports_every_experiment_and_area_summaries():
    lay = _layers()
    land = np.ones((60, 60), bool)
    area = np.zeros((60, 60), bool)
    area[:20, :20] = True
    res = sensitivity.run_all(lay, land, ExposureConfig(), {"box": area})
    assert set(res["leave_one_out"]) == set(sensitivity.TERRAIN_WEIGHTS)
    assert set(res["dem_vertical_shift"]) == {"dem_plus1m", "dem_minus1m"}
    for group in ("leave_one_out", "weight_x0.5", "weight_x1.5"):
        for v in res[group].values():
            assert -1 <= v["spearman"] <= 1 and 0 <= v["class_unchanged"] <= 1
            assert 1 <= v["areas"]["box"]["mean_class"] <= 5


def test_removing_dominant_input_changes_classes_more_than_a_minor_one():
    lay = _layers()
    land = np.ones((60, 60), bool)
    res = sensitivity.run_all(lay, land, ExposureConfig())
    assert res["leave_one_out"]["hand"]["class_unchanged"] < \
        res["leave_one_out"]["flow_accumulation"]["class_unchanged"]
