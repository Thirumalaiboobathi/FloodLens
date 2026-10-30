import { describe, expect, it } from "vitest";
import { NODATA_COLOR, RAIN_COLORS, ZERO_COLOR } from "../src/lib/rainfall";
import {
  exposureLayer, facilitiesLayer, rainfallFillColor, rainfallLayerIds, rainfallLayers, rainfallSourceId, roadsLayer, waterLayers, WATER_COLOR,
} from "../src/map/layers";

type Spec = { filter?: unknown[]; paint: Record<string, unknown>; layout: { visibility: string }; source: string };

describe("rainfall map layers", () => {
  it("historical and recent rainfall use separate sources and layer ids", () => {
    expect(rainfallSourceId("historical_final")).not.toBe(rainfallSourceId("recent_late"));
    const a = Object.values(rainfallLayerIds("historical_final"));
    const b = Object.values(rainfallLayerIds("recent_late"));
    expect(a.filter((x) => b.includes(x))).toEqual([]);
    for (const s of ["historical_final", "recent_late"] as const) {
      expect(rainfallLayers(s, true).every((l) => l.source === rainfallSourceId(s))).toBe(true);
    }
  });
  it("missing data gets its own hatched layer, never a plain colour fill", () => {
    const [fill, nodata] = rainfallLayers("recent_late", true) as unknown as Spec[];
    expect(JSON.stringify(nodata.filter)).toContain("nodata");
    expect(nodata.paint["fill-pattern"]).toBe("nodata-hatch");
    expect(nodata.paint["fill-color"]).toBeUndefined();
    expect(JSON.stringify(fill.filter)).toContain("!=");
    expect(JSON.stringify(fill.paint["fill-color"])).not.toContain(NODATA_COLOR);
  });
  it("the colour step renders 0 mm with the zero colour and rain with the class colours", () => {
    const expr = rainfallFillColor();
    expect(expr[0]).toBe("step");
    expect(expr[2]).toBe(ZERO_COLOR);
    expect(expr.filter((x) => (RAIN_COLORS as readonly unknown[]).includes(x)).length).toBe(RAIN_COLORS.length);
    const stops = expr.slice(3).filter((_, i) => i % 2 === 0) as number[];
    expect([...stops].sort((a, b) => a - b)).toEqual(stops);
  });
  it("visibility follows the flag and the two streams are styled differently", () => {
    const off = rainfallLayers("recent_late", false) as unknown as Spec[];
    const on = rainfallLayers("recent_late", true) as unknown as Spec[];
    expect(off.every((l) => l.layout.visibility === "none")).toBe(true);
    expect(on.every((l) => l.layout.visibility === "visible")).toBe(true);
    const h = (rainfallLayers("historical_final", true) as unknown as Spec[])[2].paint;
    expect(h["line-color"]).not.toBe(on[2].paint["line-color"]);
  });
  it("no layer or paint wording refers to depth or the delivered error field", () => {
    const all = JSON.stringify([
      rainfallLayers("recent_late", true), rainfallLayers("historical_final", true),
      exposureLayer(true), roadsLayer(true), facilitiesLayer(true),
    ]).toLowerCase();
    expect(all).not.toMatch(/randomerror|flood.?depth|inundation/);
  });
});

describe("water map layers", () => {
  it("draws polygons as bodies and lines as waterways from one source, hidden until toggled", () => {
    const [fill, line] = waterLayers(false) as unknown as (Spec & { id: string; type: string })[];
    expect([fill.type, line.type]).toEqual(["fill", "line"]);
    expect(fill.source).toBe("water");
    expect(line.source).toBe("water");
    expect(fill.filter).toEqual(["==", ["geometry-type"], "Polygon"]);
    expect(line.filter).toEqual(["==", ["geometry-type"], "LineString"]);
    expect(fill.layout.visibility).toBe("none");
    expect((waterLayers(true) as unknown as Spec[])[0].layout.visibility).toBe("visible");
  });

  it("stays visually distinct from every rainfall class colour", () => {
    expect([...RAIN_COLORS, ZERO_COLOR, NODATA_COLOR]).not.toContain(WATER_COLOR);
  });
});
