import { create } from "zustand";
import type { RainfallStream } from "../api/types";

/** The seven map layers. Historical and recent rainfall are independent entries, never merged. */
export type LayerId =
  | "exposure" | "rain_historical_final" | "rain_recent_late" | "roads" | "facilities" | "water";

export const STREAM_LAYER: Record<RainfallStream, LayerId> = {
  historical_final: "rain_historical_final",
  recent_late: "rain_recent_late",
};

export interface UiState {
  region: string;
  /** Exposure scenario shown by the classification layer: terrain baseline or a hypothetical accumulation. */
  exposureScenario: string;
  layers: Record<LayerId, boolean>;
  /** Chosen observed dataset per stream (null = the latest published one is used). */
  selectedRainfall: Record<RainfallStream, string | null>;
  /** The stream whose details the info panels show. */
  focusStream: RainfallStream;
  panel: "rainfall" | "provenance" | "compare";
  setRegion: (r: string) => void;
  setExposureScenario: (s: string) => void;
  toggleLayer: (l: LayerId) => void;
  selectRainfall: (stream: RainfallStream, id: string | null) => void;
  setFocusStream: (s: RainfallStream) => void;
  setPanel: (p: UiState["panel"]) => void;
}

export const useUi = create<UiState>((set) => ({
  region: "chennai",
  exposureScenario: "baseline",
  layers: { exposure: true, rain_historical_final: false, rain_recent_late: false, roads: false, facilities: false, water: false },
  selectedRainfall: { historical_final: null, recent_late: null },
  focusStream: "recent_late",
  panel: "rainfall",
  setRegion: (region) => set({ region }),
  setExposureScenario: (exposureScenario) => set({ exposureScenario }),
  toggleLayer: (l) =>
    set((s) => {
      const on = !s.layers[l];
      const stream = (Object.keys(STREAM_LAYER) as RainfallStream[]).find((k) => STREAM_LAYER[k] === l);
      return { layers: { ...s.layers, [l]: on }, ...(stream && on ? { focusStream: stream } : {}) };
    }),
  selectRainfall: (stream, id) => set((s) => ({ selectedRainfall: { ...s.selectedRainfall, [stream]: id } })),
  setFocusStream: (focusStream) => set({ focusStream }),
  setPanel: (panel) => set({ panel }),
}));

/** Pick the dataset for a stream: the user's choice if still published, else the latest by end date. */
export function resolveDataset<T extends { scenario_id: string; rainfall_stream: RainfallStream; observation_end: string }>(
  datasets: T[], stream: RainfallStream, chosen: string | null,
): T | null {
  const inStream = datasets.filter((d) => d.rainfall_stream === stream);
  if (chosen) {
    const hit = inStream.find((d) => d.scenario_id === chosen);
    if (hit) return hit;
  }
  return [...inStream].sort((a, b) => a.observation_end.localeCompare(b.observation_end)).at(-1) ?? null;
}
