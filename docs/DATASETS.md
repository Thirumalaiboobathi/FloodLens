# Datasets

Every dataset has a JSON manifest in `data/manifests/` (schema: `DatasetManifest`). Raw and processed
data are git-ignored; manifests are committed. Test fixtures are synthetic and are never listed here
as data.

## Copernicus DEM (GLO-30, GLO-90 fallback)
- Manifest: `dem-chennai.json`. Source: AWS Open Data `copernicus-dem-30m` / `-90m` (anonymous).
- Status: downloaded, validated (Chennai tiles N12/N13 E080, GLO-30).
- Limitations: surface model (buildings/vegetation included); not for flood depth.

## NASA GPM IMERG V07 Final Run, daily (`GPM_3IMERGDF` 07)
- Manifests: `nasa-gpm-imerg-v07-daily-raw-<region>-<start>_<end>.json` (raw granules + checksums),
  `imerg-v07-total-<region>-<start>_<end>.json` (derived GeoTIFF + checksum).
- Discovery: public NASA CMR API. Download: `https://data.gesdisc.earthdata.nasa.gov/data/GPM_L3/GPM_3IMERGDF.07/YYYY/MM/*.nc4`.
- Authentication: Earthdata Login user token, `Authorization: Bearer <token>`, from the
  `EARTHDATA_TOKEN` environment variable only (tokens last 60 days; a GES DISC app approval is needed
  on the Earthdata profile or the server answers 403). The token is sent only to https
  `*.earthdata.nasa.gov` hosts and is never logged or written anywhere.
- Variable `precipitation`: daily mean rate, **mm/day**, 0.1 degree grid, fill value masked to NaN,
  stored (time, lon, lat); the reader transposes and flips to north-up and checks units and spacing.
  Daily totals are UTC days. Accumulated rainfall over N days is the sum of the daily rates
  (each day = mm), and any cell missing on any day stays missing.
- Output: float32 GeoTIFF, EPSG:4326, nodata -9999, whole 0.1 degree cells covering the AOI.
- Limitations: ~10 km satellite estimate (not gauge data); clipped to the AOI bounding box, not the
  official boundary (pending ADR-0003); historical only, not a forecast.
- Citation: Huffman et al. (2023), GPM IMERG Final Precipitation L3 1 day 0.1 degree x 0.1 degree V07, GES DISC.
- randomError caveat: the daily `randomError` is kept as delivered (`as_delivered_unverified`), is not used in the
  API, frontend or exposure scores, and must not be summed. One-day reconstruction and the conflict with NASA's
  formula: `docs/analysis/IMERG_RANDOMERROR.md`; draft inquiry (not sent): `docs/analysis/NASA_GES_DISC_RANDOMERROR_INQUIRY.md`.
- Status: **ingestion code and tests done; live download not yet executed** (token not visible to
  the agent process, see PROJECT_STATUS.md).

## Administrative boundaries (geoBoundaries gbOpen IND, commit 9469f09)
- Manifests: `geoboundaries-ind-adm1` (36 states/UTs, CC BY 2.5 India via DataMeet, 2011),
  `geoboundaries-ind-adm2` (735 districts, ODbL 1.0 via Pathways Data/LGD, 2021),
  `chennai-district-boundary` (derived pilot polygon).
- Validated: EPSG:4326, all geometries valid and non-empty. Raw 46 MB + 48 MB (full) and 5 MB + 8 MB (simplified).
- Limitations: not the official external boundary of India; Chennai polygon is 175 km2 (vs ~426 km2
  usually quoted); ODbL share-alike on derived databases. Legal review of India-wide depiction open (ADR-0003).

## OpenStreetMap, Chennai AOI (Overpass API, ODbL 1.0)
- Manifests `osm-chennai-{roads,hospitals,schools,water}`; fetched 2026-10-30 with
  `scripts/ingest_vector.py osm`. Chosen over the ~200 MB Geofabrik Tamil Nadu extract to fit an 8 GB machine.
- Counts: roads 93,614 lines (motorway..residential, 37 MB GeoJSON); hospitals+clinics 1,450 points;
  schools 683 points; water 1,044 polygons + 606 waterway lines. All geometries valid.
- Limitations: crowd-sourced, completeness unvalidated; clinics included in "hospitals"; water
  relations return their full geometry, so bounds extend beyond the AOI; no passability data.

## Terrain derivatives (`terrain-derivatives-chennai`)
- From Copernicus DEM, reprojected to UTM 44N at 30 m, sea (flat 0 m, 24% of AOI) masked.
  Products: slope (Horn), local relief, TWI, specific contributing area, streams, HAND (WhiteboxTools).
- Observed: land valid 74.9%; elevation 0-170.6 m plus 597 cells below 0 m (247 below -5 m, min -64.8 m), all on
  Copernicus water-body-mask pixels: kept unchanged, excluded from scoring (docs/analysis/TERRAIN_QUALITY.md); slope max 53.5 deg; HAND valid 73% (cells with no stream path are no-data).
- Limitations: surface model, 30 m cells cannot see drains or culverts; not flood depth or extent.

## Chennai boundaries (two, deliberately both kept)
- `chennai-district-boundary` (geoBoundaries, 175 km2, older city-core extent) and
  `chennai-district-boundary-osm` (OSM relation 7910817, 441 km2). 98.6 % of the first lies inside the
  second. Neither is declared official; see docs/analysis/TERRAIN_QUALITY.md.

## Copernicus auxiliary masks (WBM water body, EDM, HEM, FLM; GLO-90 for cross-check)
- Fetched to `data/raw/dem/chennai/aux/` by `scripts/investigate_negatives.py` / `run_exposure.py` (WBM). Same licence as the DEM.

## Exposure screening products (`exposure-screening-chennai`)
- `data/processed/exposure/chennai/`, 33 products (104 MiB): per scenario (baseline, rain_50/100/200/300mm)
  index + class + completeness GeoTIFFs (30 m, UTM 44N), class polygons (2-3 MiB), road ways touching the top
  class (9-15 MiB, OSM ways not split), facility proximity; plus water distance, permanent-water mask and
  `exposure_summary.json` (thresholds, normalisation bounds, config, per-region statistics).
- Observed IMERG scenario: **absent** (pending token). Relative screening index only; see docs/METHODOLOGY.md.

## Observed rainfall streams and discovery (Phase 3)
Two IMERG daily streams are published as separate, independent context layers. They are never merged,
even when their dates overlap.

| Stream id | Product | Run | Current dataset |
|---|---|---|---|
| `historical_final` | `GPM_3IMERGDF` 07 | Final | 2023-12-03 to 2023-12-05, V07B (Cyclone Michaung) |
| `recent_late` | `GPM_3IMERGDL` 07 | Late | 2026-10-22 to 2026-10-28, V07C |

Product version is read from each granule file name (`...V07B.nc4`, `...V07C.nc4`) and cross-checked
against the manifest; it is never assumed. The Late dataset is a provisional estimate of past UTC days,
not live or real-time rainfall, and may be superseded by the Final Run.

Discovery (`geospatial/floodlens_geo/rainfall/catalog.py`) is deterministic. A derived manifest is
published only if all of these hold, otherwise it is rejected with a stable code and its stream is
`pending`:
1. its dataset id (`imerg-v07-[late-]total-<region>-<start>_<end>`), `processing_configuration.product`,
   `run_type` and granule names agree on the stream (`product_stream_mismatch` otherwise);
2. `observation_period` matches the id and the granules cover each UTC day exactly once, in order;
3. declared versions equal the versions in the granule names (`version_mismatch`);
4. exactly one raw parent manifest exists, lists the same granules, and any raw granules present on disk
   match their SHA-256 (absent raw files give a warning, a mismatch rejects);
5. the processed GeoTIFF lies under `data/processed/rainfall/`, exists, matches its manifest SHA-256, is
   single-band EPSG:4326 at 0.1 degree, and its min/max agree with the manifest.
Nothing is chosen by file order, and Late is never used for Final or the reverse. A file existing is
never enough to make a scenario available. The older `observed_info()` in `build_api_artifacts.py`
still gates only the (absent) exposure scenario `observed_sample`.

Limitations: the grid is about 10 km, so the Chennai analysis box is 20 cells; statistics describe those
cells, not neighbourhoods. Valid 0 mm and missing data are different (`state`: `zero` vs `nodata`).
Known quality issue: the daily `randomError` is not used anywhere (`docs/analysis/IMERG_RANDOMERROR.md`);
NASA has not yet responded because the draft inquiry has not been sent.

## Water in the API (Phase 4)
`osm-chennai-water` (1650 features, ODbL 1.0, SHA-256 `d25f4838...`) is now served by `/v1/regions/{region}/water`. `build_api_artifacts.py`
re-checks the processed file against its manifest (checksum and feature count) before publishing `data/api/<region>/water/`; on mismatch nothing
is published and the API answers `404 water_not_published`. Polygons are water bodies, lines are waterways; only identifying properties are kept.
No new data was downloaded and no feature was added or edited.
