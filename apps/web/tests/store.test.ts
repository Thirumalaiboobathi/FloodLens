import { beforeEach, describe, expect, it } from "vitest";
import { resolveDataset, useUi } from "../src/state/store";

const reset = () =>
  useUi.setState({
    region: "chennai", exposureScenario: "baseline", focusStream: "recent_late", panel: "rainfall",
    layers: { exposure: true, rain_historical_final: false, rain_recent_late: false, roads: false, facilities: false, water: false },
    selectedRainfall: { historical_final: null, recent_late: null },
  });

describe("layer state", () => {
  beforeEach(reset);
  it("historical and recent rainfall toggle independently", () => {
    useUi.getState().toggleLayer("rain_historical_final");
    expect(useUi.getState().layers).toMatchObject({ rain_historical_final: true, rain_recent_late: false });
    useUi.getState().toggleLayer("rain_recent_late");
    expect(useUi.getState().layers).toMatchObject({ rain_historical_final: true, rain_recent_late: true });
    useUi.getState().toggleLayer("rain_historical_final");
    expect(useUi.getState().layers).toMatchObject({ rain_historical_final: false, rain_recent_late: true });
  });
  it("switching a rainfall layer on focuses its stream; switching off does not", () => {
    useUi.getState().toggleLayer("rain_historical_final");
    expect(useUi.getState().focusStream).toBe("historical_final");
    useUi.getState().toggleLayer("rain_recent_late");
    expect(useUi.getState().focusStream).toBe("recent_late");
    useUi.getState().toggleLayer("rain_historical_final");
    expect(useUi.getState().focusStream).toBe("recent_late");
  });
  it("dataset choice is per stream", () => {
    useUi.getState().selectRainfall("recent_late", "obs_late_20261105_20261111");
    expect(useUi.getState().selectedRainfall).toEqual({ historical_final: null, recent_late: "obs_late_20261105_20261111" });
  });
  it("rainfall layers do not change the exposure layer or scenario", () => {
    useUi.getState().toggleLayer("rain_recent_late");
    expect(useUi.getState().layers.exposure).toBe(true);
    expect(useUi.getState().exposureScenario).toBe("baseline");
  });
});

describe("resolveDataset", () => {
  const ds = [
    { scenario_id: "obs_late_20261022_20261028", rainfall_stream: "recent_late" as const, observation_end: "2026-10-28" },
    { scenario_id: "obs_late_20261105_20261111", rainfall_stream: "recent_late" as const, observation_end: "2026-11-11" },
    { scenario_id: "obs_final_20231203_20231205", rainfall_stream: "historical_final" as const, observation_end: "2023-12-05" },
  ];
  it("defaults to the latest in the requested stream only", () => {
    expect(resolveDataset(ds, "recent_late", null)?.scenario_id).toBe("obs_late_20261105_20261111");
    expect(resolveDataset(ds, "historical_final", null)?.scenario_id).toBe("obs_final_20231203_20231205");
  });
  it("honours a valid choice but never one from the other stream", () => {
    expect(resolveDataset(ds, "recent_late", "obs_late_20261022_20261028")?.scenario_id).toBe("obs_late_20261022_20261028");
    expect(resolveDataset(ds, "recent_late", "obs_final_20231203_20231205")?.scenario_id).toBe("obs_late_20261105_20261111");
  });
  it("returns null when the stream has nothing", () => {
    expect(resolveDataset(ds.slice(2), "recent_late", null)).toBeNull();
    expect(resolveDataset([], "historical_final", null)).toBeNull();
  });
});
