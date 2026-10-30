# IMERG daily `randomError`: investigation and usage restrictions (2026-10-30)

Status: **`as_delivered_unverified`.** A one-day reconstruction supports a specific explanation; NASA has
not confirmed it. The field is excluded from the API, the frontend and every exposure score.

## 1. Question
The V07 daily `randomError` (labelled "RMS error, mm/day") has values of hundreds to thousands where
the same cell's rainfall is a few mm/day, and grows linearly with `randomError_cnt`. Is it the
documented RMS?

## 2. Published definition
Daily error as documented for IMERG:

    Perr_daily = sqrt( SUM(Perr_i^2 * valid_i) / N_err * 24 )

`Perr_i` are the half-hourly `randomError` values, `valid_i` the validity mask, `N_err` the number of
valid half-hours (`randomError_cnt`). The result is described as RMS in mm/day.

## 3. Method (read-only; one day)
- Day: 2026-10-22, V07C Late. The daily `GPM_3IMERGDL` 07 granule was already held in the project.
- Downloaded all 48 half-hourly `GPM_3IMERGHHL` 07 granules (357,080,310 bytes), stored under
  `data/raw/investigations/imerg-late-halfhourly-20261022/` (gitignored). SHA-256 values are in
  `SHA256SUMS.txt` and `download_log.json`; re-hashing gave 0 mismatches.
- Verified 00:00:00-23:30:00 UTC at 30 min steps (48/48, no gaps); header start/stop match file names.
- Half-hourly `randomError` and `precipitation` are mm/hr with fill -9999.9; the fills coincide and
  there are no negative errors. There is no half-hourly count variable.
- Per cell, applied the validity mask independently and accumulated sum(e^2), sum(e) and the valid
  count; compared with the stored daily values over the 6,479,924 global cells with count > 0.
- No scaling factor was applied to any candidate. The stored data were not modified.

## 4. Results
The valid half-hour count equals `randomError_cnt` and `precipitation_cnt` in all 6,480,000
daily-valid cells. 24 x the mean half-hourly precipitation reproduces the daily precipitation (max
difference 0.0001).

| Candidate vs stored daily `randomError` | Mean abs. diff | Median rel. diff | r | Reproduces? |
|---|---|---|---|---|
| sum(Perr_i^2), unscaled | ~0 (max 0.107) | 0 | 1.0000 | **yes** |
| Published sqrt(24 sum(Perr_i^2)/N) | 253.6 | -0.57 | 0.839 | no |
| sum(Perr_i) | 239.3 | +3.17 | 0.857 | no |
| 24 x mean(Perr_i) | 246.9 | +1.08 | 0.857 | no |
| 24 x sqrt(sum(Perr_i^2)/N) | 237.3 | +1.08 | 0.839 | no |

Global mean: stored 258.78, published formula 5.20. Chennai AOI (16 cells): stored 750.33, published
formula 16.72; the unscaled sum of squares differs by at most 0.0001. The ratio stored / sum(Perr_i^2)
is 1.000 at every percentile checked.

## 5. Confirmed, conflicting, pending
- **Confirmed (this one day, V07C Late):** the stored daily value equals the unscaled sum of squared
  half-hourly errors, up to float32 rounding (max absolute difference 0.107 at values near 1.3e5).
- **Conflict with NASA's published formula:** the stored value is not the documented daily RMS: it is
  not divided by N, not multiplied by 24 and not square-rooted. The documented expression is also not
  dimensionally mm/day for mm/hr inputs (it gives mm/hr x sqrt(24)); this is unresolved and was not
  scaled.
- **Pending NASA confirmation:** whether this is intended, a processing defect or a documentation
  error; whether it holds on other days, versions and runs; the intended unit. See
  `NASA_GES_DISC_RANDOMERROR_INQUIRY.md` (drafted, not sent).
- **Separate:** half-hourly headers say `ProductVersion=07B` in files named V07C.

## 6. Restrictions on using `randomError`
1. Keep it exactly as delivered; the status stays `as_delivered_unverified`.
2. The reconstructed sum of squares is **not** an official or validated uncertainty estimate. Applying
   sqrt(24 V / N) to it is an unverified research hypothesis.
3. Do not show it in the API or frontend, and do not use it in exposure scores, classes or confidence.
4. Never sum or average it across days or cells.
5. One day is not enough to change ingestion; no production code was changed.

## 7. Where recorded
Additive key `randomError_investigation` in the two Late manifests
(`nasa-gpm-imerg-v07-late-daily-raw-chennai-2026-10-22_2026-10-28`,
`imerg-v07-late-total-chennai-2026-10-22_2026-10-28`). Original statistics, notes and the earlier
`hypothesis` block are unchanged; that block's wording ("not confirmed ... by half-hourly
reconstruction") is superseded by section 5 here. Script:
`scripts/annotate_random_error_investigation.py`.
