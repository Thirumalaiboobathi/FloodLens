# Draft support inquiry to NASA GES DISC (NOT SENT)

**To:** GES DISC User Services (Earthdata support form)
**Subject:** IMERG V07 daily `randomError` appears to equal the unnormalised sum of squared half-hourly `randomError`

Hello,

While using the IMERG V07 daily Late product I found that the daily `randomError` does not match its
documented definition. I would like to confirm whether this is intended.

## Datasets and versions
| Item | Value |
|---|---|
| Daily | `GPM_3IMERGDL` version 07, granule `3B-DAY-L.MS.MRG.3IMERG.20261022-S000000-E235959.V07C.nc4` |
| Half-hourly | `GPM_3IMERGHHL` version 07, 48 granules `3B-HHR-L.MS.MRG.3IMERG.20261022-S<hhmmss>-E<hhmmss>.<minutes>.V07C.HDF5` |
| Day | 2026-10-22 (UTC), Late Run, V07C |
| Earlier observation | the same behaviour in V07B Late and V07B Final daily files |

## Variables
- Daily: `randomError` (long_name "Root-mean-square error estimate for combined microwave-IR daily
  precipitation rate", units `mm/day`, fill -9999.9), `randomError_cnt` ("Count of valid half-hourly
  randomError retrievals for the day"), `precipitation` (mm/day), `precipitation_cnt`.
- Half-hourly: `Grid/randomError` and `Grid/precipitation`, units `mm/hr`, fill -9999.9. There is no
  half-hourly count variable.

## Published formula
    Perr_daily = sqrt( SUM(Perr_i^2 * valid_i) / N_err * 24 )

## Reconstructed relationship
For every cell with a valid half-hour count > 0:

    stored daily randomError = SUM over valid half-hours of Perr_i^2    (no division, no x24, no sqrt)

## Numerical comparison (all 48 half-hours; 6,479,924 global cells with count > 0)
| Quantity | Result |
|---|---|
| Stored vs SUM(Perr_i^2): correlation | 1.0000 |
| Stored vs SUM(Perr_i^2): max abs. difference | 0.107 (float32 rounding, values up to ~1.3e5) |
| Stored / SUM(Perr_i^2) at p0, p5, p50, p95, p100 | 1.0 at all |
| Global mean: stored vs published formula | 258.78 vs 5.20 (median relative difference -0.57, r = 0.839) |
| Chennai area (16 cells): stored vs published formula | 750.33 vs 16.72; unscaled sum of squares differs by <= 0.0001 |
| `randomError_cnt` vs valid half-hour count | equal in all 6,480,000 daily-valid cells |
| 24 x mean half-hourly `precipitation` vs daily `precipitation` | max difference 0.0001 |

No scaling factor was applied. Other candidates (sum; 24 x mean; 24 x sqrt(sum of squares / N)) do not
reproduce the stored values.

## Questions
1. Is the daily `randomError` intended to be the unnormalised sum of squared half-hourly errors, or is
   this a processing or documentation error?
2. If the documented RMS is intended, will the files be corrected, and for which versions and runs?
3. Which unit applies? With mm/hr half-hourly inputs the published expression yields mm/hr x sqrt(24),
   not mm/day.
4. Is the same behaviour expected for the Early and Final Runs and for V07A/V07B?

## Separate issue
The half-hourly `FileHeader` reads `ProductVersion=07B` in granules whose names say V07C (for example
`3B-HHR-L.MS.MRG.3IMERG.20261022-S000000-E002959.0000.V07C.HDF5`, with `AlgorithmVersion=3IMERGH_7.0`).
Is the header label stale?

This is based on a single day; other days have not been checked. The files can be provided on request.

Thank you,
[name]
