import { describe, expect, it, vi } from "vitest";
import {
  NODATA_COLOR, ZERO_COLOR, colorFor, describeCell, formatMm, formatPeriod, formatUtc, rainfallLegend,
} from "../src/lib/rainfall";
import { LruCache, bboxParam, debounce, snapBounds, vectorKey } from "../src/lib/viewport";

describe("rainfall colours: zero, nodata and values are distinct", () => {
  it("valid zero is not the nodata colour and not a rain colour", () => {
    expect(colorFor("zero", 0)).toBe(ZERO_COLOR);
    expect(colorFor("nodata", null)).toBe(NODATA_COLOR);
    expect(ZERO_COLOR).not.toBe(NODATA_COLOR);
    expect(colorFor("rain", 0.5)).not.toBe(ZERO_COLOR);
  });
  it("null is never rendered as zero, even if mislabelled", () => {
    expect(colorFor("rain", null)).toBe(NODATA_COLOR);
    expect(colorFor("zero", null)).toBe(NODATA_COLOR);
  });
  it("class boundaries are inclusive at the lower bound", () => {
    expect(colorFor("rain", 9.99)).toBe(colorFor("rain", 0.01));
    expect(colorFor("rain", 10)).not.toBe(colorFor("rain", 9.99));
    expect(colorFor("rain", 300)).toBe(colorFor("rain", 999));
    expect(colorFor("rain", 299.9)).not.toBe(colorFor("rain", 300));
  });
});

describe("legend", () => {
  const legend = rainfallLegend();
  it("starts with valid zero, ends with no data, and every colour is unique", () => {
    expect(legend[0].kind).toBe("zero");
    expect(legend.at(-1)!.kind).toBe("nodata");
    expect(new Set(legend.map((e) => e.color)).size).toBe(legend.length);
  });
  it("labels cover the range contiguously in mm and avoid depth wording", () => {
    const labels = legend.map((e) => e.label);
    expect(labels).toContain("> 0 to 10 mm");
    expect(labels).toContain("10 to 25 mm");
    expect(labels).toContain("300 mm or more");
    expect(labels.join(" ").toLowerCase()).not.toMatch(/depth|flood level/);
    expect(labels[0]).toMatch(/0 mm/);
    expect(labels.at(-1)).toMatch(/not zero/);
  });
  it("legend entries match colorFor for a representative value in each class", () => {
    const probes = [0.5, 15, 30, 70, 150, 250, 400];
    legend.filter((e) => e.kind === "value").forEach((e, i) => expect(colorFor("rain", probes[i])).toBe(e.color));
  });
});

describe("formatting", () => {
  it("describes the three cell states unambiguously", () => {
    expect(describeCell("nodata", null)).toMatch(/not zero/i);
    expect(describeCell("zero", 0)).toMatch(/valid observation/i);
    expect(describeCell("rain", 12.34)).toBe("12.3 mm rainfall accumulation");
  });
  it("formats values, periods and timestamps", () => {
    expect(formatMm(null)).toBe("no data");
    expect(formatMm(0)).toBe("0.0 mm");
    expect(formatPeriod("2026-10-22", "2026-10-28")).toBe("2026-10-22 to 2026-10-28");
    expect(formatPeriod("2026-10-22", "2026-10-22")).toBe("2026-10-22");
    expect(formatUtc("2026-10-30T04:51:54.476717Z")).toBe("2026-10-30 04:51 UTC");
    expect(formatUtc(null)).toBe("not recorded");
    expect(formatUtc("garbage")).toBe("not recorded");
  });
});

describe("viewport helpers", () => {
  it("snaps to the 0.05 degree tile grid, enclosing the view", () => {
    expect(snapBounds([80.212, 12.971, 80.263, 13.004])).toEqual([80.2, 12.95, 80.3, 13.05]);
  });
  it("equal views inside one snapped area share a cache key; detail follows zoom", () => {
    const a = vectorKey("roads", "baseline", [80.212, 12.971, 80.223, 12.98], 14);
    const b = vectorKey("roads", "baseline", [80.218, 12.975, 80.229, 12.99], 14.5);
    expect(a).toBe(b);
    expect(vectorKey("roads", "baseline", [80.212, 12.971, 80.223, 12.98], 12)).not.toBe(a);
    expect(vectorKey("roads", "rain_100mm", [80.212, 12.971, 80.223, 12.98], 14)).not.toBe(a);
    expect(bboxParam([1, 2, 3, 4])).toBe("1,2,3,4");
  });
  it("debounce collapses a burst into one call", () => {
    vi.useFakeTimers();
    const fn = vi.fn();
    const d = debounce(fn, 300);
    d(1); d(2); d(3);
    vi.advanceTimersByTime(299);
    expect(fn).not.toHaveBeenCalled();
    vi.advanceTimersByTime(2);
    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenCalledWith(3);
    d(4); d.cancel(); vi.advanceTimersByTime(1000);
    expect(fn).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
  it("LruCache evicts the least recently used entry", () => {
    const c = new LruCache<number>(2);
    c.set("a", 1); c.set("b", 2); c.get("a"); c.set("c", 3);
    expect(c.has("b")).toBe(false);
    expect(c.has("a") && c.has("c")).toBe(true);
    expect(c.size).toBe(2);
  });
});
