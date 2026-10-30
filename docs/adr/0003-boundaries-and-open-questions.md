# ADR-0003: Administrative boundaries (OPEN)

Status: Proposed -> boundary source ACCEPTED (2026-10-30), legal review still open

Decision: geoBoundaries gbOpen IND (pinned commit 9469f09). Actual licences, read from the API
metadata (an earlier note here said both candidates were CC BY 4.0; that was wrong):
ADM1 states = CC BY 2.5 India (DataMeet), 2011; ADM2 districts = ODbL 1.0 (Pathways Data /
lgdirectory.gov.in), 2021. Findings: ADM2 has 735 units (metadata says 736); the 'Chennai' polygon
is 175 km2 vs ~426 km2 commonly quoted. Boundaries must carry the disclaimer that they are not the
official external boundary of India. Open: Survey of India depiction rules for any India-wide map.

Boundary data for India is legally sensitive (the depiction of the external boundary is regulated
in India) and not all downloadable sets are reusable. Candidates to evaluate in Phase 1, recording
licence for each: geoBoundaries (CC BY 4.0), DataMeet community maps (CC BY 4.0), OSM-derived
(ODbL). No dataset is adopted until its licence text is read and stored in its manifest.

# ADR-0004: Hydrology engine

Status: Accepted 2026-10-30. WhiteboxTools 2.4.0 runs natively on Windows via the `whitebox` pip
package. Verified on an analytic V-valley (flow concentrates on the axis, HAND 0 on streams and
rising away, TWI higher in the valley). Deviation: BreachDepressionsLeastCost (100-cell) ran >10 min
on the real flat Chennai DEM and was killed; switched to FillDepressions (Wang & Liu, flats fixed),
which completes in seconds. Stream threshold units are m2 (catchment area), found empirically.

Original proposal

Preferred: WhiteboxTools (MIT; `FillDepressions`/`BreachDepressions`, `D8FlowAccumulation`,
`WetnessIndex`). Verify the Windows binary runs and that outputs match analytic fixtures (planar
slope, single pit, V-valley) before use. Fallback: pysheds. Rejected: hand-rolled flow
accumulation.

# ADR-0005: Rainfall source access (OPEN, needs developer input)

Status: Accepted; implementation done, live download unverified (token not present in agent env).

IMERG V07 (Final Run, daily 0.1°) is served via NASA GES DISC and requires an Earthdata Login
(free). Without credentials the rainfall layer cannot be built from real data. We will not
substitute synthetic rainfall; if credentials are unavailable the layer is shown as `unavailable`.
