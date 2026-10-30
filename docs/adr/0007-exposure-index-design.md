# ADR-0007: Exposure index design

Status: Accepted 2026-10-30 (heuristic, uncalibrated)

- Monotone bounded transforms per indicator (exponential decay for length-scale inputs; robust
  percentile min-max for heavy-tailed TWI and log SCA); all scales are configuration.
- Weights renormalised per cell over available inputs; completeness reported per cell.
- Rainfall is a forcing term, `index = S + (1 - S) * rho * f(R)`, not another averaged score. Rejected
  design: weighted average including rainfall. A unit test showed it lowered the score of the most
  exposed cells under rain (a uniform 0.6 rainfall score dragged a 0.96 terrain score down).
- Classes are baseline quantiles (relative), reused across scenarios. Rejected: fixed absolute cut-offs,
  which would imply a calibration that does not exist.
- Permanent water is excluded from scoring; the DEM's negative values over water are kept, not clipped.
- Vector exports are generalised (median filter, sieve, simplify); rasters are unchanged.
- Out of scope: hydraulic routing, drainage capacity, tides, depth. Calibration against mapped flood
  extents is future work and until then no claim of predictive skill is made.
