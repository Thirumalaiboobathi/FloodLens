// Pure MapLibre layer/source specs. Kept separate from MapView so they can be unit tested.
// Rainfall stays at its native 0.1 degree cells (polygons, not resampled).

import type { RainfallStream } from "../api/types";
import { NODATA_HATCH, RAIN_BREAKS, RAIN_COLORS, ZERO_COLOR } from "../lib/rainfall";

export const HATCH_IMAGE = "nodata-hatch";

export const rainfallSourceId = (s: RainfallStream) => `rain-${s}`;
export const rainfallLayerIds = (s: RainfallStream) => ({
  fill: `rain-${s}-fill`, nodata: `rain-${s}-nodata`, line: `rain-${s}-line`,
});

/** A step expression: 0 mm and valid rain use distinct colours; nodata is a separate hatched layer. */
export function rainfallFillColor(): unknown[] {
  const stops: unknown[] = [];
  RAIN_BREAKS.forEach((b, i) => stops.push(b, RAIN_COLORS[i]));
  return ["step", ["coalesce", ["get", "accumulation_mm"], 0], ZERO_COLOR, ...stops];
}

export function rainfallLayers(stream: RainfallStream, visible: boolean) {
  const vis = visible ? "visible" : "none";
  const src = rainfallSourceId(stream);
  const ids = rainfallLayerIds(stream);
  // historical outlines are solid navy, recent are dashed teal: the two streams stay distinguishable
  const outline = stream === "recent_late" ? "#0b7285" : "#1c3d6e";
  return [
    { id: ids.fill, type: "fill", source: src, filter: ["!=", ["get", "state"], "nodata"],
      layout: { visibility: vis }, paint: { "fill-color": rainfallFillColor(), "fill-opacity": 0.4 } },
    { id: ids.nodata, type: "fill", source: src, filter: ["==", ["get", "state"], "nodata"],
      layout: { visibility: vis }, paint: { "fill-pattern": HATCH_IMAGE, "fill-opacity": 0.9 } },
    { id: ids.line, type: "line", source: src, layout: { visibility: vis },
      paint: { "line-color": outline, "line-width": 1.6, ...(stream === "recent_late" ? { "line-dasharray": [3, 2] } : {}) } },
  ] as const;
}

export const EXPOSURE_COLORS = ["#fff7bc", "#fee391", "#fec44f", "#fe9929", "#d95f0e"] as const;

export function exposureLayer(visible: boolean) {
  return {
    id: "exposure-fill", type: "fill", source: "exposure",
    layout: { visibility: visible ? "visible" : "none" },
    paint: {
      "fill-color": ["match", ["get", "exposure_class"], 1, EXPOSURE_COLORS[0], 2, EXPOSURE_COLORS[1], 3, EXPOSURE_COLORS[2],
        4, EXPOSURE_COLORS[3], 5, EXPOSURE_COLORS[4], "#cccccc"],
      "fill-opacity": 0.55,
    },
  } as const;
}

export const WATER_COLOR = "#1f6f8b";

/** Mapped OSM water: polygons are water bodies, lines are waterways. Reference geometry, not a flood extent. */
export const waterLayers = (visible: boolean) => {
  const layout = { visibility: visible ? "visible" : "none" };
  return [
    { id: "water-fill", type: "fill", source: "water", filter: ["==", ["geometry-type"], "Polygon"], layout,
      paint: { "fill-color": WATER_COLOR, "fill-opacity": 0.35, "fill-outline-color": WATER_COLOR } },
    { id: "water-line", type: "line", source: "water", filter: ["==", ["geometry-type"], "LineString"], layout,
      paint: { "line-color": WATER_COLOR, "line-width": ["interpolate", ["linear"], ["zoom"], 9, 0.8, 15, 2.5] } },
  ] as const;
};

export const roadsLayer = (visible: boolean) => ({
  id: "roads-line", type: "line", source: "roads", layout: { visibility: visible ? "visible" : "none" },
  paint: { "line-color": "#7a1f1f", "line-width": ["interpolate", ["linear"], ["zoom"], 11, 1, 16, 3] },
}) as const;

export const facilitiesLayer = (visible: boolean) => ({
  id: "facilities-circle", type: "circle", source: "facilities", layout: { visibility: visible ? "visible" : "none" },
  paint: {
    "circle-radius": 4.5, "circle-stroke-width": 1, "circle-stroke-color": "#ffffff",
    "circle-color": ["match", ["get", "facility_type"], "hospital_or_clinic", "#c92a2a", "school", "#5f3dc4", "#495057"],
  },
}) as const;

export const regionBoxLayer = {
  id: "region-box", type: "line", source: "region-box",
  paint: { "line-color": "#495057", "line-width": 1, "line-dasharray": [2, 2] },
} as const;

/** Canvas hatch for missing data, so it can never be mistaken for a rainfall colour. */
export function makeHatch(size = 8): { width: number; height: number; data: Uint8ClampedArray } | null {
  if (typeof document === "undefined") return null;
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const ctx = c.getContext("2d");
  if (!ctx) return null;
  ctx.fillStyle = "#e9ecef";
  ctx.fillRect(0, 0, size, size);
  ctx.strokeStyle = NODATA_HATCH;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(0, size); ctx.lineTo(size, 0);
  ctx.stroke();
  const d = ctx.getImageData(0, 0, size, size);
  return { width: size, height: size, data: d.data };
}
