// Basemap configuration. Pure functions so they can be unit tested without a map.
//
// Amazon Location Service Maps (v2) are reached with a *browser API key* (`v1.public.…`): a restricted,
// referrer-bound key that can only call map actions. It is not an AWS access key. It is read from build
// or runtime environment, never from source, and the style URL (which contains it) is never displayed.

import type maplibregl from "maplibre-gl";

export interface BasemapEnv {
  VITE_MAP_STYLE_URL?: string;
  VITE_LOCATION_API_KEY?: string;
  VITE_LOCATION_REGION?: string;
  VITE_LOCATION_STYLE?: string;
  VITE_LOCATION_COLOR_SCHEME?: string;
  VITE_LOCATION_POLITICAL_VIEW?: string;
}

export const LOCATION_STYLES = ["Standard", "Monochrome", "Hybrid", "Satellite"] as const;
export const DEFAULT_REGION = "ap-south-1";
// Monochrome is Amazon's minimalist style meant for data-visualisation overlays.
export const DEFAULT_STYLE = "Monochrome";
export const DEFAULT_POLITICAL_VIEW = "IND";

const REGION_RE = /^[a-z]{2}(?:-[a-z]+)+-\d$/;
const KEY_RE = /^[A-Za-z0-9._~-]{10,1000}$/;
const VIEW_RE = /^[A-Z]{2,3}$/;

export type BasemapSource =
  | { kind: "none" }
  | { kind: "url"; url: string }
  | { kind: "amazon-location"; url: string; region: string; style: string }
  | { kind: "invalid"; reason: string };

/** Resolve the configured basemap. An explicit style URL wins; otherwise an API key selects Amazon Location. */
export function resolveBasemap(env: BasemapEnv): BasemapSource {
  const explicit = env.VITE_MAP_STYLE_URL?.trim();
  if (explicit) {
    try {
      const u = new URL(explicit);
      if (u.protocol !== "https:" && !(u.protocol === "http:" && ["localhost", "127.0.0.1"].includes(u.hostname)))
        return { kind: "invalid", reason: "VITE_MAP_STYLE_URL must be https (http only for localhost)" };
      return { kind: "url", url: u.toString() };
    } catch {
      return { kind: "invalid", reason: "VITE_MAP_STYLE_URL is not a valid URL" };
    }
  }
  const key = env.VITE_LOCATION_API_KEY?.trim();
  if (!key) return { kind: "none" };
  const region = (env.VITE_LOCATION_REGION?.trim() || DEFAULT_REGION);
  const style = (env.VITE_LOCATION_STYLE?.trim() || DEFAULT_STYLE);
  const scheme = (env.VITE_LOCATION_COLOR_SCHEME?.trim() || "Light");
  const view = (env.VITE_LOCATION_POLITICAL_VIEW?.trim() ?? DEFAULT_POLITICAL_VIEW);
  if (!KEY_RE.test(key)) return { kind: "invalid", reason: "VITE_LOCATION_API_KEY has an unexpected format" };
  if (!REGION_RE.test(region)) return { kind: "invalid", reason: "VITE_LOCATION_REGION is not a valid AWS region" };
  if (!(LOCATION_STYLES as readonly string[]).includes(style))
    return { kind: "invalid", reason: `VITE_LOCATION_STYLE must be one of ${LOCATION_STYLES.join(", ")}` };
  if (scheme !== "Light" && scheme !== "Dark") return { kind: "invalid", reason: "VITE_LOCATION_COLOR_SCHEME must be Light or Dark" };
  if (view && !VIEW_RE.test(view)) return { kind: "invalid", reason: "VITE_LOCATION_POLITICAL_VIEW must be a country code such as IND" };
  const q = new URLSearchParams();
  if (style === "Standard" || style === "Monochrome") q.set("color-scheme", scheme); // not applicable to Hybrid/Satellite
  if (view) q.set("political-view", view);
  q.set("key", key);
  return { kind: "amazon-location", region, style,
    url: `https://maps.geo.${region}.amazonaws.com/v2/styles/${style}/descriptor?${q.toString()}` };
}

export const BLANK_STYLE: maplibregl.StyleSpecification = {
  version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#eef1f4" } }],
};

export interface StyleResult { style: maplibregl.StyleSpecification | string; failed: boolean }

/**
 * Load the style descriptor before creating the map, so a bad key, wrong region or network failure
 * degrades to the blank background (data layers keep working) instead of leaving the map without a style.
 * The URL contains the key, so nothing derived from it is ever surfaced.
 */
export async function loadStyle(source: BasemapSource, fetchImpl: typeof fetch = fetch, timeoutMs = 8000): Promise<StyleResult> {
  if (source.kind === "none") return { style: BLANK_STYLE, failed: false };
  if (source.kind === "invalid") return { style: BLANK_STYLE, failed: true };
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetchImpl(source.url, { signal: ctl.signal, credentials: "omit" });
    if (!res.ok) return { style: BLANK_STYLE, failed: true };
    const style = (await res.json()) as maplibregl.StyleSpecification;
    if (style?.version !== 8 || !Array.isArray(style.layers)) return { style: BLANK_STYLE, failed: true };
    return { style, failed: false };
  } catch {
    return { style: BLANK_STYLE, failed: true };
  } finally {
    clearTimeout(timer);
  }
}

const RUNTIME_KEYS = ["VITE_MAP_STYLE_URL", "VITE_LOCATION_API_KEY", "VITE_LOCATION_REGION", "VITE_LOCATION_STYLE",
  "VITE_LOCATION_COLOR_SCHEME", "VITE_LOCATION_POLITICAL_VIEW"] as const;

/**
 * Deploy-time configuration served next to the app as /config.json (written by the CDK stack, so the
 * browser API key never lives in source control or the JS bundle). Only known string fields are read;
 * a missing or malformed file (always the case in local development) yields no overrides.
 */
export async function loadRuntimeConfig(fetchImpl: typeof fetch = fetch): Promise<BasemapEnv> {
  try {
    const res = await fetchImpl("/config.json", { cache: "no-store", credentials: "omit" });
    if (!res.ok || !(res.headers.get("content-type") ?? "").includes("json")) return {};
    const raw = (await res.json()) as Record<string, unknown>;
    const out: BasemapEnv = {};
    for (const k of RUNTIME_KEYS) if (typeof raw?.[k] === "string") out[k] = raw[k] as string;
    return out;
  } catch {
    return {};
  }
}
