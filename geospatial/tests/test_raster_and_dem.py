import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from floodlens_geo.ingestion.dem import DemTile, candidate_cells, discover_tiles
from floodlens_geo.raster.validate import RasterValidationError, validate_raster


def write_tif(path, arr, *, crs="EPSG:4326", nodata=-9999.0):
    h, w = arr.shape
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1, dtype="float32",
        crs=crs, transform=from_origin(80.0, 14.0, 0.001, 0.001), nodata=nodata,
    ) as dst:
        dst.write(arr.astype("float32"), 1)


def test_validate_ok_counts_nodata_and_nan(tmp_path):
    a = np.full((10, 10), 5.0)
    a[0, :] = -9999.0
    a[1, 0] = np.nan
    p = tmp_path / "a.tif"
    write_tif(p, a)
    r = validate_raster(p, expected_epsg=4326)
    assert r.valid_fraction == pytest.approx(0.89)
    assert r.minimum == r.maximum == 5.0


def test_validate_wrong_crs(tmp_path):
    p = tmp_path / "a.tif"
    write_tif(p, np.ones((4, 4)), crs="EPSG:32644")
    with pytest.raises(RasterValidationError, match="expected EPSG:4326"):
        validate_raster(p, expected_epsg=4326)


def test_validate_all_nodata(tmp_path):
    p = tmp_path / "a.tif"
    write_tif(p, np.full((4, 4), -9999.0))
    with pytest.raises(RasterValidationError, match="valid pixels"):
        validate_raster(p)


def test_candidate_cells_spans_tile_boundary():
    cells = candidate_cells((80.05, 12.85, 80.35, 13.25))
    assert cells == [(12, 80), (13, 80)]
    assert candidate_cells((79.5, 12.5, 80.5, 13.5)) == [
        (12, 79), (12, 80), (13, 79), (13, 80)
    ]


def test_candidate_cells_invalid():
    with pytest.raises(ValueError):
        candidate_cells((80, 13, 79, 14))


class FakeS3:
    def __init__(self, keys):
        self.keys = keys

    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        return {"KeyCount": int((Bucket, Prefix) in self.keys)}


def test_discover_falls_back_to_glo90():
    n30 = "Copernicus_DSM_COG_10_N13_00_E080_00_DEM"
    n90 = "Copernicus_DSM_COG_30_N12_00_E080_00_DEM"
    s3 = FakeS3({("copernicus-dem-30m", f"{n30}/{n30}.tif"),
                 ("copernicus-dem-90m", f"{n90}/{n90}.tif")})
    tiles = discover_tiles((80.05, 12.85, 80.35, 13.25), s3=s3)
    assert [(t.product, t.resolution_m) for t in tiles] == [("GLO-90", 90), ("GLO-30", 30)]
    assert all(isinstance(t, DemTile) for t in tiles)
