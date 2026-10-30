// Credential-free API client. Paths are built only from validated ids; the browser never holds AWS credentials
// or tokens. Production calls go straight to the API Gateway HTTP API named by VITE_API_BASE_URL (CORS-enabled,
// public read-only); in development the Vite proxy serves /v1 from the same origin and the base is empty.

import type {
  FacilityCollection, ObservedRainfallMetadata, PolygonCollection, RainfallCatalog, RainfallCells,
  RainfallComparison, RainfallProvenance, RainfallStream, RainfallSummary, RegionList, RoadCollection, WaterCollection,
  ScenarioCatalog, ApiErrorBody,
} from "./types";

export interface ApiBaseEnv { VITE_API_BASE_URL?: string; VITE_API_BASE?: string }
export type ApiBase = { ok: true; base: string } | { ok: false; code: "api_not_configured" | "invalid_api_base"; message: string };

/**
 * Resolve the API origin. VITE_API_BASE_URL is the supported name (VITE_API_BASE is the older alias). The value is an
 * https origin with no path, query or credentials (plain http only for localhost); a trailing slash is removed. An empty value means
 * same-origin, which only works in development (Vite proxy), so a production build without it reports a clear error
 * instead of asking the static host for /v1 and receiving index.html.
 */
export function resolveApiBase(env: ApiBaseEnv, production: boolean): ApiBase {
  const raw = (env.VITE_API_BASE_URL ?? env.VITE_API_BASE ?? "").trim().replace(/\/+$/, "");
  if (!raw) {
    return production
      ? { ok: false, code: "api_not_configured", message: "The FloodLens API address is not configured for this build." }
      : { ok: true, base: "" };
  }
  try {
    const u = new URL(raw);
    const local = u.protocol === "http:" && ["localhost", "127.0.0.1"].includes(u.hostname);
    if ((u.protocol !== "https:" && !local) || u.username || u.password || u.search || u.hash || (u.pathname !== "/" && u.pathname !== "")) {
      return { ok: false, code: "invalid_api_base", message: "VITE_API_BASE_URL must be a bare https origin (scheme and host only; no path, query or credentials)." };
    }
    return { ok: true, base: u.origin };
  } catch {
    return { ok: false, code: "invalid_api_base", message: "VITE_API_BASE_URL is not a valid URL." };
  }
}

const RESOLVED = resolveApiBase((import.meta.env ?? {}) as ApiBaseEnv, Boolean(import.meta.env?.PROD));
const BASE = RESOLVED.ok ? RESOLVED.base : "";
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
  if (!RESOLVED.ok) throw new ApiError(0, RESOLVED.code, RESOLVED.message);
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
