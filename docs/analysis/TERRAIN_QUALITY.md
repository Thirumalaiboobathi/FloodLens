# Terrain data quality: negative elevations and the Chennai boundary (2026-10-30)

Reproduce: `python scripts/investigate_negatives.py chennai` (report: `data/processed/terrain/chennai/negative_cells_report.json`).

## 1. Negative-elevation cells
**Correction:** the earlier "247 cells" counted only cells below -5 m. Cells below 0 m number **597**
(71 connected clusters, 247 below -5 m, minimum -64.8 m), all inside the 30 m UTM land grid.

| Test | Result |
|---|---|
| Copernicus water-body mask (WBM, same tile folders on AWS) | **597 / 597** on water pixels (lake/river/tidal) |
| Copernicus editing mask (EDM) | 187 cells flagged as edited (flattened/filled water) |
| Independent GLO-90 product | 363 / 597 also negative; clusters differ in depth, e.g. -64.8 m (GLO-30) vs -29 m (GLO-90) |
| OSM quarries (6 mapped in AOI) | 0 cells inside a quarry |
| Height-error mask (HEM) | not informative here (mostly no-data) |
| Neighbour land elevation | median 6 m (range 0-27 m), so they are depressions inside positive terrain |

Largest clusters (lon, lat): 137 cells at (80.112, 12.954) min -64.8 m; 150 at (80.098, 13.016) min -12 m;
32 at (80.232, 12.947); 22 at (80.302, 13.237); 21 at (80.263, 13.190). All coincide with WBM water.

**Decision.** They are DSM values over water surfaces (radar over water and the water-editing process),
not legitimate land terrain and not ocean pixels (the edge-connected 0 m sea is already masked).
- No value was clipped, zeroed or interpolated: that would invent terrain.
- They stay unchanged in the terrain rasters (a water surface genuinely is a local sink for flow).
- Permanent water (WBM) is excluded from exposure scoring and from land statistics, so these cells
  cannot influence the index; neighbouring land cells are unaffected except through flow routing.
- Terrain indicators were not recalculated: the only affected products are over water, and the
  exposure engine masks them. Elevation-based statistics should use land cells only.
- Caveat: matching the water mask explains the cells but does not prove each depth is physically right;
  the -64.8 m cluster is deeper than any plausible water surface and is treated as an artefact. Narrow
  channels may be only partly covered by the 30 m mask.

## 2. Chennai boundary: 175 km2 vs ~426 km2
| Polygon | Area | Notes |
|---|---|---|
| geoBoundaries ADM2 "Chennai" | 175.3 km2 | what the pipeline used; single valid polygon |
| OSM Chennai district (relation 7910817, admin_level 5) | 441.0 km2 | may include coastal water |
| Overlap | 172.9 km2 | geoBoundaries polygon is 98.6 % inside the OSM district |

The geoBoundaries polygon is a strict subset: it lacks 268 km2 (north: Ennore/Manali/Madhavaram; south:
Perungudi, Sholinganallur, Pallikaranai areas). 175 km2 matches the pre-2011 Chennai city extent (~174 km2),
and ~426 km2 is the district area after the 2011 expansion. Conclusion: **not an extraction error** (one
feature, filtered by name); the source polygon is an **older/city-core boundary**, incomplete for the current
district. This is inferred from areas and extents; the authoritative LGD/Survey of India geometry was not
obtained, so neither polygon is declared official.

**Decision.** Nothing was silently changed. The geoBoundaries polygon stays the dataset
`chennai-district-boundary` (flagged as city core); the OSM polygon is added as
`chennai-district-boundary-osm`. The exposure summaries report both. Hospitals inside the core polygon:
772 of 1,450 (1,019 inside the OSM district); schools 402 of 683 (575).
Open: obtain the LGD Chennai district geometry and decide the official pilot boundary.
