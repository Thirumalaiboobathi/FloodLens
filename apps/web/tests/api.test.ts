import { describe, expect, it } from "vitest";
import { ApiError, api } from "../src/api/client";
import { HttpFail, mockApi } from "./fixtures";

describe("api client", () => {
  it("sends credential-free same-origin requests", async () => {
    let init: RequestInit | undefined;
    globalThis.fetch = (async (_u: unknown, i?: RequestInit) => { init = i; return new Response("{}"); }) as typeof fetch;
    await api.regions();
    expect(JSON.stringify(init?.headers ?? {}).toLowerCase()).not.toMatch(/authorization|token|x-api-key|aws/);
    expect(init?.credentials).toBeUndefined();
  });
  it("maps the structured error envelope", async () => {
    mockApi({ "/rainfall": new HttpFail(404, "region_not_found", "unknown region") });
    await expect(api.rainfallCatalog("chennai")).rejects.toMatchObject({ status: 404, code: "region_not_found", requestId: "r1" });
  });
  it("reports network failure without leaking internals", async () => {
    globalThis.fetch = (async () => { throw new TypeError("boom"); }) as typeof fetch;
    await expect(api.regions()).rejects.toMatchObject({ code: "network_error" });
  });
  it("rejects malformed identifiers before any request is made", () => {
    const calls = mockApi({});
    expect(() => api.rainfallMeta("chennai", "../../etc/passwd")).toThrow(ApiError);
    expect(() => api.rainfallSummary("chennai", "obs_late_1_2")).toThrow(ApiError);
    expect(() => api.polygons("chen nai", "baseline")).toThrow(ApiError);
    expect(calls).toEqual([]);
  });
  it("builds stream filters and comparison ids", async () => {
    const calls = mockApi({ "/rainfall": {}, "/rainfall/comparison": {} });
    await api.rainfallCatalog("chennai", "recent_late");
    await api.rainfallComparison("chennai", ["obs_late_20261022_20261028", "obs_final_20231203_20231205"]);
    expect(calls[0]).toContain("stream=recent_late");
    expect(decodeURIComponent(calls[1])).toContain("ids=obs_late_20261022_20261028,obs_final_20231203_20231205");
  });
  it("roads requests are viewport bounded", async () => {
    const calls = mockApi({ "/roads": { features: [], truncated: false } });
    await api.roads("chennai", "baseline", "80.2,12.95,80.3,13.05", 14.04);
    expect(calls[0]).toMatch(/bbox=80\.2%2C12\.95%2C80\.3%2C13\.05/);
    expect(calls[0]).toContain("zoom=14");
  });
});
