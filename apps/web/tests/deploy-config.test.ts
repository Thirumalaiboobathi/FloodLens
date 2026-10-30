import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { resolveApiBase } from "../src/api/client";

const root = resolve(__dirname, "../../..");
const read = (p: string) => readFileSync(resolve(root, p), "utf-8");

describe("VITE_API_BASE_URL", () => {
  it("is empty (same-origin via the Vite proxy) in development", () => {
    expect(resolveApiBase({}, false)).toEqual({ ok: true, base: "" });
  });
  it("is required in a production build, with a clear error rather than calling the static host", () => {
    expect(resolveApiBase({}, true)).toMatchObject({ ok: false, code: "api_not_configured" });
  });
  it("accepts an https origin, strips trailing slashes, and honours the older alias", () => {
    const u = "https://abc123.execute-api.ap-south-1.amazonaws.com";
    expect(resolveApiBase({ VITE_API_BASE_URL: `${u}/` }, true)).toEqual({ ok: true, base: u });
    expect(resolveApiBase({ VITE_API_BASE: u }, true)).toEqual({ ok: true, base: u });
    expect(resolveApiBase({ VITE_API_BASE_URL: u, VITE_API_BASE: "https://old.example.org" }, true)).toEqual({ ok: true, base: u });
    expect(resolveApiBase({ VITE_API_BASE_URL: "http://localhost:8000" }, false)).toEqual({ ok: true, base: "http://localhost:8000" });
  });
  it.each([
    "http://abc.execute-api.ap-south-1.amazonaws.com", "https://x.example/v1", "https://x.example?a=1", "https://user:pw@x.example",
    "ftp://x.example", "not a url", "//x.example",
  ])("rejects %s", (v) => {
    expect(resolveApiBase({ VITE_API_BASE_URL: v }, true)).toMatchObject({ ok: false, code: "invalid_api_base" });
  });
});

describe("amplify.yml", () => {
  const y = read("amplify.yml");
  it("builds apps/web with npm ci and npm run build and publishes dist", () => {
    expect(y).toMatch(/appRoot: apps\/web/);
    expect(y).toMatch(/npm ci/);
    expect(y).toMatch(/npm run build/);
    expect(y).toMatch(/baseDirectory: dist/);
  });
  it("contains no token, key or secret value", () => {
    expect(y).not.toMatch(/AKIA|ASIA|ghp_|github_pat_|v1\.public\.|aws_secret|password\s*:|token\s*:/i);
    expect(y.split("\n").filter((l) => !l.trim().startsWith("#")).join("\n")).not.toMatch(/VITE_[A-Z_]+\s*=/);
  });
  it("does not route API traffic through Amplify", () => {
    const code = y.split("\n").filter((l) => !l.trim().startsWith("#")).join("\n");
    expect(code).not.toMatch(/\/v1|execute-api|rewrite/i);
  });
});

describe("Amplify SPA rewrite", () => {
  const rules = JSON.parse(read("apps/web/amplify-rewrites.json")) as { source: string; target: string; status: string }[];
  const re = new RegExp(rules[0].source.replace(/^</, "").replace(/>$/, "").replace(/^\/|\/$/g, ""));
  it("serves index.html with 200 for client-side routes", () => {
    expect(rules).toHaveLength(1);
    expect(rules[0]).toMatchObject({ target: "/index.html", status: "200" });
    for (const p of ["/", "/regions/chennai", "/rainfall/obs_final_20250101_20250107", "/about"]) expect(re.test(p), p).toBe(true);
  });
  it("leaves real static files alone", () => {
    for (const p of ["/assets/index-abc123.js", "/assets/style.css", "/favicon.ico", "/assets/map.png", "/robots.txt", "/config.json"]) {
      expect(re.test(p), p).toBe(false);
    }
  });
});

describe("Amplify response headers", () => {
  const h = read("apps/web/customHttp.yml");
  it("sets the security headers and limits connect-src to the API host pattern and the Location maps host", () => {
    for (const k of ["Strict-Transport-Security", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Content-Security-Policy"]) expect(h).toContain(k);
    expect(h).toContain("connect-src 'self' https://*.execute-api.ap-south-1.amazonaws.com https://maps.geo.ap-south-1.amazonaws.com");
    expect(h).toContain("frame-ancestors 'none'");
    expect(h).not.toMatch(/script-src[^;]*unsafe-(inline|eval)/);
  });
});

describe("environment documentation", () => {
  it(".env.example documents VITE_API_BASE_URL, says the map key is not a secret, and holds no key", () => {
    const e = read("apps/web/.env.example");
    expect(e).toContain("VITE_API_BASE_URL");
    expect(e).toMatch(/not a secret/i);
    expect(e).not.toMatch(/^VITE_[A-Z_]+=\S/m);
  });
});
