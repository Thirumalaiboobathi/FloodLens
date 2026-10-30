// Rainfall presentation rules. Values are satellite-estimated rainfall accumulation in mm over the
// stated period, never flood depth. Valid zero rainfall and missing data are different things and
// are always drawn differently.

import type { CellState, RainfallStream } from "../api/types";

export const STREAM_LABEL: Record<RainfallStream, string> = {
  historical_final: "Historical Final Run",
  recent_late: "Recent Late Run",
};
export const STREAM_BLURB: Record<RainfallStream, string> = {
  historical_final: "IMERG Final Run: gauge-adjusted historical estimate.",
  recent_late: "IMERG Late Run: provisional near-real-time estimate; not live, may be superseded by Final.",
};

/** Absolute, stream-independent classes so the two streams are visually comparable. mm, lower bound inclusive. */
export const RAIN_BREAKS = [0.0001, 10, 25, 50, 100, 200, 300] as const;
export const RAIN_COLORS = ["#e3f0fb", "#bcd9f2", "#8ebfe8", "#5a9bd6", "#2f73b8", "#1b4f94", "#0d2f6b"] as const;
export const ZERO_COLOR = "#f6f1df"; // valid observation of 0 mm
export const NODATA_COLOR = "#9aa0a6"; // missing: drawn hatched, never as 0
export const NODATA_HATCH = "#5f6368";

export interface LegendEntry { key: string; label: string; color: string; kind: "value" | "zero" | "nodata" }

const fmt = (n: number) => (Number.isInteger(n) ? String(n) : String(+n.toFixed(1)));

export function rainfallLegend(): LegendEntry[] {
  const out: LegendEntry[] = [{ key: "zero", label: "0 mm (valid observation, no rain)", color: ZERO_COLOR, kind: "zero" }];
  const edges = [0, ...RAIN_BREAKS.slice(1)];
  RAIN_COLORS.forEach((color, i) => {
    const lo = edges[i];
    const hi = edges[i + 1];
    const label = i === 0 ? `> 0 to ${fmt(hi)} mm` : hi === undefined ? `${fmt(lo)} mm or more` : `${fmt(lo)} to ${fmt(hi)} mm`;
    out.push({ key: `c${i}`, label, color, kind: "value" });
  });
  out.push({ key: "nodata", label: "No data (missing, not zero)", color: NODATA_COLOR, kind: "nodata" });
  return out;
}

export function colorFor(state: CellState, mm: number | null): string {
  if (state === "nodata" || mm === null) return NODATA_COLOR;
  if (state === "zero" || mm === 0) return ZERO_COLOR;
  let c = RAIN_COLORS[0] as string;
  RAIN_BREAKS.forEach((b, i) => { if (mm >= b) c = RAIN_COLORS[i]; });
  return c;
}

export function formatMm(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined ? "no data" : `${v.toFixed(digits)} mm`;
}

export function describeCell(state: CellState, mm: number | null): string {
  if (state === "nodata" || mm === null) return "No data (missing; not zero rainfall)";
  if (state === "zero") return "0 mm (valid observation: no rain recorded)";
  return `${mm.toFixed(1)} mm rainfall accumulation`;
}

export function formatPeriod(start: string, end: string): string {
  return start === end ? start : `${start} to ${end}`;
}

export function formatUtc(ts: string | null | undefined): string {
  if (!ts) return "not recorded";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? "not recorded" : d.toISOString().replace("T", " ").slice(0, 16) + " UTC";
}

export const FRESHNESS_LABEL = {
  historical_archive: "Historical archive",
  recent_provisional: "Recent, provisional",
  aged_provisional: "Older provisional (may be superseded)",
} as const;
