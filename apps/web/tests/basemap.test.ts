import { describe, expect, it, vi } from "vitest";
import { BLANK_STYLE, loadRuntimeConfig, loadStyle, resolveBasemap } from "../src/map/basemap";

const KEY = "v1.public.TESTKEYvalue123";

describe("resolveBasemap", () => {
  it("is none without configuration, so local development needs no key", () => {
    expect(resolveBasemap({})).toEqual({ kind: "none" });
    expect(resolveBasemap({ VITE_LOCATION_REGION: "ap-south-1" })).toEqual({ kind: "none" });
  });

  it("builds a Monochrome ap-south-1 Amazon Location URL with India's political view by default", () => {
    const b = resolveBasemap({ VITE_LOCATION_API_KEY: KEY });
    expect(b.kind).toBe("amazon-location");
    if (b.kind !== "amazon-location") return;
    const u = new URL(b.url);
    expect(u.origin).toBe("https://maps.geo.ap-south-1.amazonaws.com");
    expect(u.pathname).toBe("/v2/styles/Monochrome/descriptor");
    expect(u.searchParams.get("key")).toBe(KEY);
    expect(u.searchParams.get("political-view")).toBe("IND");
    expect(u.searchParams.get("color-scheme")).toBe("Light");
  });

  it("omits color-scheme for Hybrid and Satellite, and the political view when set empty", () => {
    const b = resolveBasemap({ VITE_LOCATION_API_KEY: KEY, VITE_LOCATION_STYLE: "Satellite", VITE_LOCATION_POLITICAL_VIEW: "" });
    if (b.kind !== "amazon-location") throw new Error("expected amazon-location");
    const q = new URL(b.url).searchParams;
    expect(q.has("color-scheme")).toBe(false);
    expect(q.has("political-view")).toBe(false);
  });

  it("an explicit style URL wins; non-https is rejected except for localhost", () => {
    expect(resolveBasemap({ VITE_MAP_STYLE_URL: "https://tiles.example.org/style.json", VITE_LOCATION_API_KEY: KEY }))
      .toEqual({ kind: "url", url: "https://tiles.example.org/style.json" });
    expect(resolveBasemap({ VITE_MAP_STYLE_URL: "http://evil.example/style.json" }).kind).toBe("invalid");
    expect(resolveBasemap({ VITE_MAP_STYLE_URL: "http://localhost:8080/style.json" }).kind).toBe("url");
    expect(resolveBasemap({ VITE_MAP_STYLE_URL: "not a url" }).kind).toBe("invalid");
  });

  it.each([
    [{ VITE_LOCATION_API_KEY: "short" }],
    [{ VITE_LOCATION_API_KEY: "v1.public.ok&key=other&x=1234" }],
    [{ VITE_LOCATION_API_KEY: KEY, VITE_LOCATION_REGION: "ap-south-1.evil.com/" }],
    [{ VITE_LOCATION_API_KEY: KEY, VITE_LOCATION_STYLE: "../Standard" }],
    [{ VITE_LOCATION_API_KEY: KEY, VITE_LOCATION_COLOR_SCHEME: "Blue" }],
    [{ VITE_LOCATION_API_KEY: KEY, VITE_LOCATION_POLITICAL_VIEW: "india" }],
  ])("rejects malformed configuration instead of building a URL: %j", (env) => {
    expect(resolveBasemap(env).kind).toBe("invalid");
  });
});

describe("loadStyle", () => {
  const url = resolveBasemap({ VITE_LOCATION_API_KEY: KEY });
  const okStyle = { version: 8, sources: {}, layers: [] };
  const resp = (ok: boolean, body: unknown) => ({ ok, json: async () => body }) as unknown as Response;

  it("returns the blank style with no failure when no basemap is configured (no network call)", async () => {
    const f = vi.fn();
    expect(await loadStyle({ kind: "none" }, f as unknown as typeof fetch)).toEqual({ style: BLANK_STYLE, failed: false });
    expect(f).not.toHaveBeenCalled();
  });

  it("uses the fetched descriptor, never sending credentials", async () => {
    const f = vi.fn().mockResolvedValue(resp(true, okStyle));
    const r = await loadStyle(url, f as unknown as typeof fetch);
    expect(r).toEqual({ style: okStyle, failed: false });
    expect(f.mock.calls[0][1]).toMatchObject({ credentials: "omit" });
  });

  it.each([
    ["HTTP 403 (bad key or referrer)", () => Promise.resolve(resp(false, {}))],
    ["network error", () => Promise.reject(new TypeError("Failed to fetch"))],
    ["not a style", () => Promise.resolve(resp(true, { hello: "world" }))],
  ])("falls back to the blank style and flags failure on %s, without leaking the key", async (_n, impl) => {
    const r = await loadStyle(url, vi.fn(impl) as unknown as typeof fetch);
    expect(r).toEqual({ style: BLANK_STYLE, failed: true });
    expect(JSON.stringify(r)).not.toContain(KEY);
  });

  it("an invalid configuration falls back and flags failure", async () => {
    expect(await loadStyle({ kind: "invalid", reason: "x" })).toEqual({ style: BLANK_STYLE, failed: true });
  });
});

describe("loadRuntimeConfig", () => {
  const json = (body: unknown, ok = true, type = "application/json") =>
    ({ ok, headers: new Headers({ "content-type": type }), json: async () => body }) as unknown as Response;

  it("reads only known string fields and never sends credentials or uses the cache", async () => {
    const f = vi.fn().mockResolvedValue(json({ VITE_LOCATION_API_KEY: KEY, VITE_LOCATION_STYLE: "Standard", VITE_LOCATION_REGION: 5, extra: "x" }));
    expect(await loadRuntimeConfig(f as unknown as typeof fetch)).toEqual({ VITE_LOCATION_API_KEY: KEY, VITE_LOCATION_STYLE: "Standard" });
    expect(f).toHaveBeenCalledWith("/config.json", { cache: "no-store", credentials: "omit" });
  });

  it.each([
    ["404", () => Promise.resolve(json({}, false))],
    ["an SPA fallback page served as html", () => Promise.resolve(json({}, true, "text/html"))],
    ["a network error", () => Promise.reject(new TypeError("offline"))],
  ])("yields no overrides on %s (local development has no config.json)", async (_n, impl) => {
    expect(await loadRuntimeConfig(vi.fn(impl) as unknown as typeof fetch)).toEqual({});
  });
});
