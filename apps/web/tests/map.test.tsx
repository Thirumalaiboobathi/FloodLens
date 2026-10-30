import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useUi } from "../src/state/store";
import { CELLS, mockApi, standardRoutes } from "./fixtures";

// A recording stand-in for MapLibre: it checks what the app adds and updates, not WebGL rendering.
const rec = vi.hoisted(() => ({
  sources: new Map<string, unknown>(), layers: [] as { id: string; source?: string }[],
  visibility: new Map<string, string>(), handlers: new Map<string, (() => void)[]>(), zoom: 9.2,
}));

vi.mock("maplibre-gl", () => {
  class FakeSource { constructor(public id: string) {} setData(d: unknown) { rec.sources.set(this.id, d); } }
  class FakeMap {
    private src = new Map<string, FakeSource>();
    constructor() { rec.sources.clear(); rec.layers.length = 0; rec.visibility.clear(); rec.handlers.clear(); }
    addControl() {}
    on(ev: string, a: unknown, b?: unknown) {
      const cb = (typeof a === "function" ? a : b) as () => void;
      if (typeof a === "function") rec.handlers.set(ev, [...(rec.handlers.get(ev) ?? []), cb]);
    }
    hasImage() { return false; }
    addImage() {}
    addSource(id: string, spec: { data: unknown }) { this.src.set(id, new FakeSource(id)); rec.sources.set(id, spec.data); }
    getSource(id: string) { return this.src.get(id); }
    addLayer(l: { id: string; source?: string; layout?: { visibility: string } }) { rec.layers.push(l); rec.visibility.set(l.id, l.layout?.visibility ?? "visible"); }
    getLayer(id: string) { return rec.layers.find((l) => l.id === id); }
    setLayoutProperty(id: string, _p: string, v: string) { rec.visibility.set(id, v); }
    fitBounds() {}
    getBounds() { return { getWest: () => 80.05, getSouth: () => 12.85, getEast: () => 80.35, getNorth: () => 13.25 }; }
    getZoom() { return rec.zoom; }
    getCanvas() { return { style: {} }; }
    remove() {}
  }
  const noop = class { constructor(..._a: unknown[]) {} };
  const Popup = class { setLngLat() { return this; } setDOMContent() { return this; } addTo() { return this; } };
  return { default: { Map: FakeMap, NavigationControl: noop, ScaleControl: noop, AttributionControl: noop, Popup } };
});

import { MapView } from "../src/map/MapView";

const reset = () =>
  useUi.setState({
    region: "chennai", exposureScenario: "baseline", focusStream: "recent_late", panel: "rainfall",
    layers: { exposure: true, rain_historical_final: false, rain_recent_late: false, roads: false, facilities: false, water: false },
    selectedRainfall: { historical_final: null, recent_late: null },
  });

const mount = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MapView /></QueryClientProvider>);
};
// the map is created after the runtime config and style have resolved, so wait for its handlers
const fire = async (ev: string) => {
  await waitFor(() => expect(rec.handlers.get(ev)?.length ?? 0).toBeGreaterThan(0));
  await act(async () => { rec.handlers.get(ev)?.forEach((f) => f()); });
};

let calls: string[] = [];
beforeEach(() => {
  reset();
  rec.zoom = 9.2;
  calls = mockApi(standardRoutes({
    "/polygons": { features: [], truncated: false },
    "/roads": { features: [], truncated: false },
    "/facilities": { features: [], truncated: false },
  }));
});

describe("MapView layers", () => {
  it("creates separate sources and layers for historical and recent rainfall, hidden by default", async () => {
    mount();
    await fire("load");
    expect([...rec.sources.keys()]).toEqual(expect.arrayContaining(["rain-historical_final", "rain-recent_late", "exposure", "roads", "facilities"]));
    const ids = rec.layers.map((l) => l.id);
    for (const id of ["rain-historical_final-fill", "rain-historical_final-nodata", "rain-recent_late-fill", "rain-recent_late-nodata", "exposure-fill", "roads-line", "facilities-circle"]) {
      expect(ids).toContain(id);
    }
    expect(rec.layers.filter((l) => l.id.startsWith("rain-historical_final")).every((l) => l.source === "rain-historical_final")).toBe(true);
    expect(rec.layers.filter((l) => l.id.startsWith("rain-recent_late")).every((l) => l.source === "rain-recent_late")).toBe(true);
    expect(rec.visibility.get("rain-recent_late-fill")).toBe("none");
    // roads and facilities draw above rainfall; rainfall draws above exposure
    expect(ids.indexOf("exposure-fill")).toBeLessThan(ids.indexOf("rain-historical_final-fill"));
    expect(ids.indexOf("rain-recent_late-fill")).toBeLessThan(ids.indexOf("roads-line"));
  });

  it("downloads no rainfall cells, roads or facilities until their layers are switched on", async () => {
    mount();
    await fire("load");
    await waitFor(() => expect(calls.some((c) => c.includes("/rainfall"))).toBe(true));
    expect(calls.some((c) => c.includes("/cells"))).toBe(false);
    expect(calls.some((c) => c.includes("/roads"))).toBe(false);
    expect(calls.some((c) => c.includes("/facilities"))).toBe(false);
  });

  it("switching on recent rainfall loads only the recent stream cells and shows only its layers", async () => {
    mount();
    await fire("load");
    act(() => useUi.getState().toggleLayer("rain_recent_late"));
    await waitFor(() => expect(rec.sources.get("rain-recent_late")).toEqual(CELLS));
    expect(calls.filter((c) => c.includes("/cells"))).toHaveLength(1);
    expect(calls.find((c) => c.includes("/cells"))).toContain("obs_late_");
    expect(rec.visibility.get("rain-recent_late-fill")).toBe("visible");
    expect(rec.visibility.get("rain-historical_final-fill")).toBe("none");
    expect(rec.sources.get("rain-historical_final")).not.toEqual(CELLS);
  });

  it("does not fetch the same cells twice when a layer is toggled off and on", async () => {
    mount();
    await fire("load");
    act(() => useUi.getState().toggleLayer("rain_historical_final"));
    await waitFor(() => expect(calls.some((c) => c.includes("/cells"))).toBe(true));
    act(() => useUi.getState().toggleLayer("rain_historical_final"));
    act(() => useUi.getState().toggleLayer("rain_historical_final"));
    expect(calls.filter((c) => c.includes("/cells"))).toHaveLength(1);
    expect(rec.visibility.get("rain-historical_final-fill")).toBe("visible");
  });

  it("requests roads only for the visible area and only when zoomed in far enough", async () => {
    mount();
    await fire("load");
    act(() => useUi.getState().toggleLayer("roads"));
    await fire("moveend");
    await new Promise((r) => setTimeout(r, 350)); // debounce window
    expect(calls.some((c) => c.includes("/roads"))).toBe(false); // zoom 9.2 is below the vector threshold
    rec.zoom = 14;
    await fire("moveend");
    await waitFor(() => expect(calls.some((c) => c.includes("/roads"))).toBe(true), { timeout: 2000 });
    const road = calls.find((c) => c.includes("/roads"))!;
    // the viewport 80.05..80.35 x 12.85..13.25 is snapped outward to the 0.05 degree tile grid
    expect(decodeURIComponent(road)).toContain("bbox=80,12.85,80.35,13.3&");
    expect(road).toContain("zoom=14");
    expect(calls.filter((c) => c.includes("/roads"))).toHaveLength(1);
  });
});
