// Same-origin, credential-free API client. Paths are built only from validated ids; the browser never
// holds AWS credentials or tokens.

import type {
  FacilityCollection, ObservedRainfallMetadata, PolygonCollection, RainfallCatalog, RainfallCells,
  RainfallComparison, RainfallProvenance, RainfallStream, RainfallSummary, RegionList, RoadCollection, WaterCollection,
  ScenarioCatalog, ApiErrorBody,
} from "./types";

const BASE = (import.meta.env?.VITE_API_BASE as string | undefined) ?? "";
const ID = /^[a-z0-9_]+$/;
const RAIN_ID = /^obs_(final|late)_\d{8}_\d{8}$/;

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public requestId?: string | null) {
    super(message);
    this.name = "ApiError";
  }
}

function seg(value: string, re = ID): string {
  if (!re.test(value)) throw new ApiError(0, "invalid_identifier", `invalid identifier: ${value}`);
  return encodeURIComponent(value);
}

export async function getJson<T>(path: string, params?: Record<string, string | number | undefined>, signal?: AbortSignal): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) if (v !== undefined) qs.set(k, String(v));
  const url = `${BASE}/v1${path}${qs.size ? `?${qs}` : ""}`;
  let res: Response;
  try {
    res = await fetch(url, { signal, headers: { Accept: "application/json" } });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "network_error", "The FloodLens API could not be reached.");
  }
  if (!res.ok) {
    let body: { error?: ApiErrorBody } = {};
    try { body = await res.json(); } catch { /* non-JSON error body */ }
    const e = body.error;
    throw new ApiError(res.status, e?.code ?? "http_error", e?.message ?? `Request failed (${res.status})`, e?.request_id);
  }
  return (await res.json()) as T;
}

const r = (region: string) => `/regions/${seg(region)}`;

export const api = {
  regions: () => getJson<RegionList>("/regions"),
  scenarioCatalog: (region: string) => getJson<ScenarioCatalog>(`${r(region)}/scenario-catalog`),
  rainfallCatalog: (region: string, stream?: RainfallStream) =>
    getJson<RainfallCatalog>(`${r(region)}/rainfall`, { stream }),
  rainfallMeta: (region: string, id: string) => getJson<ObservedRainfallMetadata>(`${r(region)}/rainfall/${seg(id, RAIN_ID)}`),
  rainfallSummary: (region: string, id: string) => getJson<RainfallSummary>(`${r(region)}/rainfall/${seg(id, RAIN_ID)}/summary`),
  rainfallProvenance: (region: string, id: string) => getJson<RainfallProvenance>(`${r(region)}/rainfall/${seg(id, RAIN_ID)}/provenance`),
  rainfallCells: (region: string, id: string) => getJson<RainfallCells>(`${r(region)}/rainfall/${seg(id, RAIN_ID)}/cells`),
  rainfallComparison: (region: string, ids?: string[]) =>
    getJson<RainfallComparison>(`${r(region)}/rainfall/comparison`, { ids: ids?.map((i) => seg(i, RAIN_ID)).join(",") }),
  polygons: (region: string, scenario: string) =>
    getJson<PolygonCollection>(`${r(region)}/scenarios/${seg(scenario)}/polygons`, { limit: 20000 }),
  facilities: (region: string, scenario: string, bbox: string) =>
    getJson<FacilityCollection>(`${r(region)}/scenarios/${seg(scenario)}/facilities`, { bbox, limit: 5000 }),
  water: (region: string, bbox: string, signal?: AbortSignal) =>
    getJson<WaterCollection>(`${r(region)}/water`, { bbox, limit: 5000 }, signal),
  roads: (region: string, scenario: string, bbox: string, zoom: number, signal?: AbortSignal) =>
    getJson<RoadCollection>(`${r(region)}/scenarios/${seg(scenario)}/roads`, { bbox, zoom: Math.round(zoom * 10) / 10, limit: 5000 }, signal),
  rasterHref: (region: string, id: string) => `${BASE}/v1${r(region)}/rainfall/${seg(id, RAIN_ID)}/raster`,
};
