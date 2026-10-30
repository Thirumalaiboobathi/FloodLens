# FloodLens methodology (screening index)

**What this is.** A *relative* terrain-and-hydrology exposure screening index with hypothetical rainfall
scenarios. **What it is not.** A flood forecast, probability, depth, extent, evacuation/safe-route
advice or official warning. It has not been validated against observed flood extents.

## 1. Inputs (all on one 30 m UTM grid)
| Input | Source | Why it matters |
|---|---|---|
| Elevation | Copernicus DEM GLO-30 (surface model) | low ground is reached first |
| Slope | Horn (1981) on the DEM, metric CRS | flat ground drains slowly |
| TWI | WhiteboxTools wetness index from D-infinity specific contributing area and slope | tendency to accumulate water |
| Flow accumulation | D-infinity specific contributing area (log10) | convergence of flow |
| HAND | WhiteboxTools elevation above nearest stream (streams = drainage area > 0.25 km2) | height above the drainage network |
| Distance to water | Copernicus water-body mask + OSM water polygons, distance transform | proximity to rivers, lakes, sea |
| Rainfall | IMERG V07 accumulation (observed sample, **pending**) or hypothetical depth | forcing |

Preprocessing: depressions filled (Wang & Liu, flats resolved), sea (flat 0 m connected to the edge)
masked. Permanent water (Copernicus WBM) is excluded from scoring: it is not land exposure.

## 2. Indicator transforms (each maps to 0..1, 1 = more exposed)
All are monotone and use the parameters in `ExposureConfig.transforms`; none is calibrated.
- HAND: `exp(-HAND / 5 m)`; elevation: `exp(-z / 10 m)`; water distance: `exp(-d / 500 m)`
  (exponential decay: bounded, smooth, one interpretable length scale each).
- Slope: `1 - min(slope, 5 deg) / 5 deg` (above 5 deg drainage is not limited by slope).
- TWI and log10(SCA): clipped min-max between the 2nd and 98th percentile of land cells in the AOI
  (heavy-tailed, so percentiles not extremes). Bounds are stored in the output summary.
- Rainfall forcing `f = 1 - exp(-R / 200 mm)` (saturating).
- Nodata or non-positive SCA is *missing*, never converted to "lowest".

## 3. Combination
1. Terrain susceptibility `S` = weighted mean of the available terrain/hydrology scores; weights are
   configurable and **renormalised per cell** over what is present.
2. Rainfall amplification: `index = S + (1 - S) * rho * f`, `rho` = rainfall weight / total weight.
   An earlier draft averaged rainfall in as another term; a test showed that *lowered* the score of
   the most exposed cells when it rained, so it was replaced. The current form never lowers a score.
3. Reach scaling (hypothetical heuristic): for depth R the length scales of HAND and water distance
   are multiplied by `(R / 100 mm)^0.5`, so more rain reaches higher above drainage. The exponent is
   not calibrated.
4. `completeness` = share of configured weight available in the cell (rainfall counts only if a
   rainfall input exists). Cells below `min_completeness` (0.5) stay nodata. Cells with no terrain input
   are never scored from rainfall alone.

## 4. Classes
Classes 1..5 are **quantiles of the baseline index** over scored land (thresholds recorded in
`exposure_summary.json`) and reused for every scenario, so scenario maps show shifts relative to the
baseline. They are relative ranks, not risk levels. The baseline has no rainfall term, so scenarios
below the reference depth (100 mm) can score slightly *lower* than baseline because the reach narrows.

## 5. Scenarios
`baseline` (terrain only) and hypothetical 24 h depths 50/100/200/300 mm. `observed_sample` is added
only when a real IMERG product exists. Scenarios do not route water, model drainage capacity, tides,
infrastructure failure or antecedent wetness.

## 6. Products
Rasters (index, class, completeness per scenario), class polygons (median-filtered, sieved and
simplified for export only), road ways touching the top class, facility proximity (OSM hospitals/clinics
and schools: terrain context only, no capacity or access), regional summaries, manifests with checksums.

## 7. Known limitations and validation status
- Copernicus DEM is a **surface** model at 30 m: buildings/vegetation distort slope and HAND; drains,
  culverts and embankments are invisible. Chennai is extremely flat.
- Spot check (qualitative, not validation): Pallikaranai marsh and Ennore/Manali score in the top class,
  Tambaram and Guindy in the lowest; Velachery, widely reported as flood-prone, is mid-ranked and
  heterogeneous (13% class 1, 21% class 5; mean ~3.2) and does not stand out from its surroundings; the
  cause is traced to inputs (DSM, no drainage, 30 m), not weights. See `docs/VALIDATION.md`. The index should not be used to rank individual places.
- Weights and scales are expert-style defaults. A calibration study against mapped flood extents
  (e.g. Dec 2015 / Dec 2023 events) is future work.
- Region boundaries: the geoBoundaries "Chennai" polygon (175 km2) is a subset of the OSM Chennai
  district polygon (441 km2); summaries are reported for both and neither is silently preferred.
- OSM completeness is unknown; IMERG (when added) is a ~10 km satellite estimate.

## 8. Sensitivity analysis and output contract (added 2026-10-30)
- `scripts/run_sensitivity.py` perturbs weights (x0.5, x1.5, leave-one-out), transform scales (x0.5, x2)
  and shifts elevation/HAND by +/-1 m, comparing class stability (Spearman, same-class share,
  top-class overlap). It measures stability, not accuracy; results and Velachery diagnosis are in
  `docs/VALIDATION.md`.
- Scenario vocabulary is fixed in the v1 schemas: `terrain_baseline`, `hypothetical_rainfall_accumulation`
  (analyst-chosen mm), `observed_rainfall` (real data only; pending). Rainfall values are
  *rainfall accumulation*, never flood depth.


## 9. Observed rainfall context (Phase 3, added 2026-10-30)
Observed IMERG rainfall is displayed as a separate context layer and is **not** an input of the exposure
index. The scoring weights, thresholds, classes and every existing exposure output are unchanged, and the
exposure package does not import the rainfall code (a test enforces this). Exposure results exist only for
the terrain baseline and the hypothetical 50/100/200/300 mm accumulations.

- Values are satellite-estimated rainfall accumulation in mm: the sum of consecutive UTC daily totals, a
  cell missing on any day staying missing. Valid 0 mm is a real observation.
- Rainfall stays at its native 0.1 degree (~10 km) cells; it is not resampled to the 30 m exposure grid.
- Comparing observed and hypothetical accumulations, or observed streams with each other, shows how the
  estimates differ. It does not validate the exposure screening and does not predict flooding. Periods of
  different length are compared by mean daily rate.
- Integrating observed rainfall into the index would need a separate decision (resolution mismatch,
  calibration, validation data) and is not done.
