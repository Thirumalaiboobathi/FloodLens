# Scientific validation notes (Phase 2.5)

Status: **the exposure index is not validated.** This document records what was checked, what was
found and what could not be checked. Nothing here is a calibration.

## 1. Velachery diagnosis

Earlier notes said Velachery "scores class 1". That was a single-point impression and is **not
supported** by the full raster. Measured on `baseline_class.tif` (30 m cells, class shares inside an
approximate 3.5 km x 3.9 km box, lon 80.205-80.240, lat 12.965-13.000; the box is a sampling aid,
not a flood extent):

| Area (box) | class 1 | 2 | 3 | 4 | 5 (top) |
|---|---|---|---|---|---|
| Velachery | 13% | 22% | 23% | 21% | 21% |
| Pallikaranai-adjacent | 11% | 15% | 14% | 14% | 47% |
| T. Nagar | 19% | 26% | 23% | 20% | 13% |
| Kolathur/Perambur | 6% | 23% | 28% | 24% | 20% |
| Tambaram | 53% | 22% | 11% | 7% | 7% |
| Guindy | 24% | 23% | 19% | 16% | 19% |

Velachery is mid-ranked and heterogeneous, with a fifth of its cells in the top class; the
Pallikaranai-adjacent cells (the marsh Velachery drains toward) are strongly top-class. The index
does separate the marsh edge from the high ground at Tambaram. What it cannot do is single out
Velachery as *more* exposed than its surroundings. Likely reasons, by evidence level:

1. **DSM, not DTM (documented).** Copernicus GLO-30 is a surface model. Buildings and canopy raise
   cells and make streets look like pits or ridges; HAND/TWI inherit that. A point sampled at
   (12.9815 N, 80.2207 E) sits at 11.5 m with HAND 4.6 m, no upstream accumulation (the minimum
   SCA value) and a TWI at the 19th land percentile: the DEM reads it as locally high and
   non-convergent.
2. **No drainage network.** Storm drains, culverts, pumping and encroached lake beds are not in the
   DEM or in OSM. Velachery's reported flooding is mostly a drainage/backwater problem near the
   Pallikaranai marsh and former lake beds, which a 30 m surface model does not represent.
3. **Resolution.** 30 m cells (and a 5 m HAND scale) cannot resolve the sub-metre relief that
   separates flooded from dry streets in a city this flat.
4. **Water proximity** uses mapped permanent water only (WBM + OSM); the Pallikaranai marsh edge
   is captured, interior channels are not.

These are hypotheses about the *inputs*. They were not tested against observed flood extents
(section 3).

## 2. Sensitivity analysis

`scripts/run_sensitivity.py` (module `floodlens_geo.exposure.sensitivity`) perturbs the production
configuration one parameter at a time on a 90 m decimated grid and writes
`data/processed/exposure/chennai/sensitivity.json`. Results from the executed run:

- Dropping HAND is the most influential change (Spearman 0.87 vs baseline, only 54% of cells keep
  their class, top-class overlap 0.72). Dropping elevation is next (rho 0.98, 79% unchanged).
- Halving or 1.5x-ing any weight, or halving/doubling the HAND, elevation or water-distance
  scale, keeps rho >= 0.98 and 82-96% of cells in the same class.
- A +/-1 m vertical shift of elevation and HAND (proxy for DSM error) keeps rho >= 0.99, with 89-95%
  of cells unchanged and top-class overlap 0.84-0.96.
- **Velachery's mean class stays between 2.94 and 3.32 (baseline 3.17) in every experiment**, so no
  plausible re-weighting changes its mid-rank. The limitation is in the inputs, not the weights.
  Weights were deliberately not tuned toward an expected answer.

This shows the *ranking* is stable under heuristic changes; it does not show the ranking is
*right*. Stability is not accuracy.

## 3. Historical flood-extent datasets

Searched 2026-10-30. Only listing pages could be read; **licences and resolutions below are
unverified unless stated**, and none has been downloaded or used.

| Candidate | Event | What it is | Verified | Concerns |
|---|---|---|---|---|
| UNOSAT flood-water geodata, Chennai (via HDX; two versions) | Nov-Dec 2015 | Satellite-derived flood water polygons | Existence and title only (HDX returned 403) | Search snippet says imagery from 2015-11-24 and earlier, i.e. possibly *pre-peak*; "preliminary, not field-validated"; sensor, resolution, licence unconfirmed |
| Sentinel Asia activation 2015-12-01 | 2015-12-03 and 12-09 | ALOS-2 PALSAR-2 radar flood-extent maps (JAXA/AIT/IWMI) | Page read: JPEG/PDF only, no usage terms | Image maps, not GIS layers; no stated resolution or licence; would need georeferencing |
| IWMI Sentinel-1 flood maps (Nov 2015) | Nov 2015 | PDF maps for Chennai/Cuddalore | Titles only | PDFs; dates before the 1-2 Dec peak; licence unconfirmed |
| NRSC/ISRO DMSP inundation, Michaung | 2023-12-07 | Official PDF map, Tamil Nadu | Existence only (fetch 404) | Satellite, resolution, Chennai coverage and licence unconfirmed |
| NRSC Andhra report | 2023-12-06 | RISAT-1A inundation | Search snippet | Andhra Pradesh, not Chennai |
| Global Flood Database (MODIS, Cloud to Street/DFO) | 2000-2018 | 250 m flood maps | Dataset exists | No Chennai-2015 entry confirmed; 250 m is far coarser than the ~30 m index; 2023 is outside its range |
| OpenCity "Chennai 2015 GCC area flood hotspots" | 2015 | Hotspot locations (points) | Search snippet says public-domain | Page returned 404; points, not extents |
| Published papers (IIT Madras Sentinel-1 2015/2021; Springer 2021) | 2015, 2021 | Method + figures | Titles only | Underlying layers not confirmed as released |

**Conclusion:** no dataset has been confirmed to be (a) machine-readable, (b) at suitable
resolution, (c) dated at or near the peak, and (d) openly licensed. The index therefore has **no
quantitative validation**. Validation data has not been fabricated, digitised from images or
substituted.

Proposed next step (needs developer approval, no code yet): confirm licences from the publishers'
own pages; if UNOSAT/NRSC layers are usable, compare class shares inside and outside the mapped
extent (e.g. area under the ROC curve, with the caveat that drainage-driven urban flooding and
pre-peak imagery bias the comparison). Independent derivation from Sentinel-1 would need ASF
access and an urban-SAR method; it is out of scope for the current phase.

## 4. Remaining limitations

DSM not DTM; no drainage; 30 m resolution; heuristic uncalibrated weights; class labels are
*relative ranks* of the baseline distribution, not risk levels; the index ignores tides, reservoir
releases, river flow and infrastructure; official Chennai boundary undecided.
