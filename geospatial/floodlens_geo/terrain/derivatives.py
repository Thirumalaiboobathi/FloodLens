"""Terrain preparation and derivatives on a projected DEM.

Memory: only the AOI is read (windowed merge), so the working DEM is a few MB. Slope is computed
in a metric CRS with the true cell size; hydrology is delegated to WhiteboxTools (ADR-0004), never
hand-rolled.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.merge import merge
from rasterio.warp import calculate_default_transform, reproject

BBox = tuple[float, float, float, float]
NODATA = -9999.0


def utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def prepare_dem(tiles: list[Path], bbox: BBox, out: Path, *, resolution_m: float | None = None,
                buffer_deg: float = 0.02) -> Path:
    """Mosaic tiles (windowed to bbox + buffer), reproject to local UTM, write a float32 GeoTIFF.

    The buffer keeps edge cells valid for slope/flow calculations; callers crop to the AOI later.
    """
    w, s, e, n = bbox
    clip = (w - buffer_deg, s - buffer_deg, e + buffer_deg, n + buffer_deg)
    srcs = [rasterio.open(t) for t in tiles]
    try:
        mosaic, tr = merge(srcs, bounds=clip, nodata=NODATA, dtype="float32")
        src_crs = srcs[0].crs
        native_res = srcs[0].res[0]
    finally:
        for r in srcs:
            r.close()
    dst_crs = rasterio.CRS.from_epsg(utm_epsg((w + e) / 2, (s + n) / 2))
    res = resolution_m or 30.0
    dt, width, height = calculate_default_transform(
        src_crs, dst_crs, mosaic.shape[2], mosaic.shape[1], *rasterio.transform.array_bounds(
            mosaic.shape[1], mosaic.shape[2], tr), resolution=res)
    dst = np.full((height, width), NODATA, dtype="float32")
    reproject(mosaic[0], dst, src_transform=tr, src_crs=src_crs, src_nodata=NODATA,
              dst_transform=dt, dst_crs=dst_crs, dst_nodata=NODATA,
              resampling=Resampling.bilinear)
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", driver="GTiff", height=height, width=width, count=1,
                       dtype="float32", crs=dst_crs, transform=dt, nodata=NODATA,
                       compress="deflate") as d:
        d.write(dst, 1)
        d.update_tags(SOURCE_NATIVE_RES_DEG=str(native_res), RESAMPLING="bilinear",
                      NOTE="Copernicus DSM (surface model), not bare earth")
    return out


def slope_degrees(dem: np.ndarray, cell_x: float, cell_y: float | None = None,
                  nodata: float = NODATA) -> np.ndarray:
    """Horn (1981) slope in degrees. Border cells and cells touching nodata become NaN."""
    cell_y = cell_y or cell_x
    z = np.where(dem == nodata, np.nan, dem).astype("float64")
    p = np.pad(z, 1, constant_values=np.nan)
    a, b, c = p[:-2, :-2], p[:-2, 1:-1], p[:-2, 2:]
    d, f = p[1:-1, :-2], p[1:-1, 2:]
    g, h, i = p[2:, :-2], p[2:, 1:-1], p[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * cell_x)
    dzdy = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * cell_y)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    slope[np.isnan(z)] = np.nan  # Horn ignores the centre cell; a nodata cell has no slope
    return slope


def local_relief(dem: np.ndarray, radius_cells: int, nodata: float = NODATA) -> np.ndarray:
    """Height above the lowest cell within a square window (m). A coarse low-ground indicator."""
    from scipy.ndimage import minimum_filter

    z = np.where(dem == nodata, np.nan, dem).astype("float64")
    filled = np.where(np.isnan(z), np.inf, z)
    low = minimum_filter(filled, size=2 * radius_cells + 1, mode="nearest")
    out = z - low
    out[~np.isfinite(out)] = np.nan
    return out


def ocean_mask(dem: np.ndarray, nodata: float = NODATA) -> np.ndarray:
    """True for cells that are exactly 0 m and connected to the raster edge (sea in Copernicus DEM).

    Copernicus sets water bodies to a flat 0 m. Inland zero-elevation patches that do not touch the
    edge are kept as land. Assumes the coast is cut by the raster edge, true for the pilot AOIs.
    """
    from scipy.ndimage import label

    zero = dem == 0
    lab, _ = label(zero)
    edge = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    edge = edge[edge > 0]
    return np.isin(lab, edge) & (dem != nodata)


def water_mask_from_wbm(wbm_paths: list[Path], dem_path: Path) -> np.ndarray:
    """Copernicus water-body mask (1 ocean, 2 lake, 3 river) warped (nearest) onto the DEM grid.

    255 marks "outside this tile". Source arrays are read first: warping straight from a dataset
    band with dst_nodata=0 marked land (0) cells as water on the real Chennai tiles.
    """
    with rasterio.open(dem_path) as ref:
        water = np.zeros((ref.height, ref.width), dtype=bool)
        for p in wbm_paths:
            with rasterio.open(p) as src:
                tmp = np.full(water.shape, 255, dtype="uint8")
                reproject(src.read(1), tmp, src_transform=src.transform, src_crs=src.crs,
                          src_nodata=255, dst_transform=ref.transform, dst_crs=ref.crs,
                          dst_nodata=255, resampling=Resampling.nearest)
                water |= (tmp >= 1) & (tmp <= 3)
    return water


@dataclass(frozen=True)
class HydrologyOutputs:
    conditioned_dem: Path
    sca: Path  # specific contributing area, m2/m
    slope_deg: Path
    twi: Path
    streams: Path
    hand: Path  # elevation above nearest stream, m


class WhiteboxUnavailable(RuntimeError):
    pass


def run_hydrology(dem: Path, workdir: Path, *, stream_threshold_m2: float = 250_000.0) -> HydrologyOutputs:
    """Fill depressions (Wang & Liu, flats resolved), D-infinity SCA, slope, TWI, streams and HAND via WhiteboxTools."""
    try:
        from whitebox import WhiteboxTools
    except ImportError as e:  # pragma: no cover
        raise WhiteboxUnavailable("pip install whitebox") from e
    wbt = WhiteboxTools()
    wbt.set_verbose_mode(False)
    workdir.mkdir(parents=True, exist_ok=True)
    p = {k: str((workdir / f"{k}.tif").resolve()) for k in
         ("cond", "sca", "slope", "twi", "streams", "hand", "area")}
    steps = [
        wbt.fill_depressions_wang_and_liu(str(dem.resolve()), p["cond"], fix_flats=True, flat_increment=0.001),
        wbt.d_inf_flow_accumulation(p["cond"], p["sca"], out_type="Specific Contributing Area"),
        wbt.d_inf_flow_accumulation(p["cond"], p["area"], out_type="Catchment Area"),
        wbt.slope(p["cond"], p["slope"], units="degrees"),
        wbt.wetness_index(p["sca"], p["slope"], p["twi"]),
        wbt.extract_streams(p["area"], p["streams"], stream_threshold_m2),
        wbt.elevation_above_stream(p["cond"], p["streams"], p["hand"]),
    ]
    if any(code != 0 for code in steps):
        raise RuntimeError(f"WhiteboxTools step failed (return codes {steps})")
    return HydrologyOutputs(*(Path(p[k]) for k in ("cond", "sca", "slope", "twi", "streams", "hand")))
