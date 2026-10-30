// Test data comes from the committed API schema fixtures (slices of the real built artifacts).
import catalog from "../../../schemas/v1/fixtures/rainfall_catalog.example.json";
import cells from "../../../schemas/v1/fixtures/rainfall_cells.example.json";
import comparison from "../../../schemas/v1/fixtures/rainfall_comparison.example.json";
import provenance from "../../../schemas/v1/fixtures/rainfall_provenance.example.json";
import scenarioCatalog from "../../../schemas/v1/fixtures/scenario_catalog.example.json";
import summary from "../../../schemas/v1/fixtures/rainfall_summary.example.json";
import type {
  RainfallCatalog, RainfallCells, RainfallComparison, RainfallProvenance, RainfallSummary, ScenarioCatalog,
} from "../src/api/types";

export const CATALOG = catalog as unknown as RainfallCatalog;
export const CELLS = cells as unknown as RainfallCells;
export const SUMMARY = summary as unknown as RainfallSummary;
export const PROVENANCE = provenance as unknown as RainfallProvenance;
export const COMPARISON = comparison as unknown as RainfallComparison;
export const SCENARIOS = scenarioCatalog as unknown as ScenarioCatalog;
export const REGIONS = {
  regions: [{ region_id: "chennai", name: "Chennai", bbox: { west: 80.05, south: 12.85, east: 80.35, north: 13.25 },
              boundaries: ["b"], scenarios: ["baseline"] }],
};
export const FINAL = CATALOG.datasets.find((d) => d.rainfall_stream === "historical_final")!;
export const LATE = CATALOG.datasets.find((d) => d.rainfall_stream === "recent_late")!;

type Handler = unknown | ((url: URL) => unknown);

/** Answer with the API error envelope. */
export class HttpFail {
  constructor(public status: number, public code: string, public message = "failed") {}
}

/** Route fetch by path suffix (longest match wins). Returns the list of requested paths. */
export function mockApi(routes: Record<string, Handler>) {
  const calls: string[] = [];
  const fn = async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    calls.push(url.pathname + url.search);
    const key = Object.keys(routes).sort((a, b) => b.length - a.length).find((k) => url.pathname.endsWith(k));
    if (!key) return new Response(JSON.stringify({ error: { code: "not_found", message: "no route" } }), { status: 404 });
    const h = routes[key];
    const v = typeof h === "function" ? (h as (u: URL) => unknown)(url) : h;
    if (v instanceof HttpFail) {
      return new Response(JSON.stringify({ error: { code: v.code, message: v.message, request_id: "r1" } }), { status: v.status });
    }
    return new Response(JSON.stringify(v), { status: 200, headers: { "content-type": "application/json" } });
  };
  globalThis.fetch = fn as typeof fetch;
  return calls;
}

export function standardRoutes(extra: Record<string, Handler> = {}): Record<string, Handler> {
  return {
    "/regions": REGIONS,
    "/scenario-catalog": SCENARIOS,
    "/rainfall": CATALOG,
    "/rainfall/comparison": COMPARISON,
    [`/rainfall/${LATE.scenario_id}/summary`]: SUMMARY,
    [`/rainfall/${LATE.scenario_id}/provenance`]: PROVENANCE,
    [`/rainfall/${LATE.scenario_id}/cells`]: CELLS,
    [`/rainfall/${FINAL.scenario_id}/summary`]: {
      ...SUMMARY, scenario_id: FINAL.scenario_id, rainfall_stream: "historical_final", n_days: 3,
      observation_start: FINAL.observation_start, observation_end: FINAL.observation_end,
    },
    [`/rainfall/${FINAL.scenario_id}/provenance`]: {
      ...PROVENANCE, scenario_id: FINAL.scenario_id, rainfall_stream: "historical_final",
      product_identifier: "GPM_3IMERGDF", product_versions: ["V07B"],
    },
    ...extra,
  };
}
