import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it } from "vitest";
import type { RainfallCatalog } from "../src/api/types";
import { ComparisonPanel } from "../src/components/ComparisonPanel";
import { InfoPanel } from "../src/components/InfoPanel";
import { LayerPanel } from "../src/components/LayerPanel";
import { Legend } from "../src/components/Legend";
import { ProvenancePanel } from "../src/components/ProvenancePanel";
import { RainfallPanel } from "../src/components/RainfallPanel";
import { useUi } from "../src/state/store";
import { CATALOG, FINAL, HttpFail, LATE, mockApi, standardRoutes } from "./fixtures";

const wrap = (ui: ReactElement) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
};

const reset = () =>
  useUi.setState({
    region: "chennai", exposureScenario: "baseline", focusStream: "recent_late", panel: "rainfall",
    layers: { exposure: true, rain_historical_final: false, rain_recent_late: false, roads: false, facilities: false, water: false },
    selectedRainfall: { historical_final: null, recent_late: null },
  });

beforeEach(() => { reset(); mockApi(standardRoutes()); });

describe("LayerPanel: layer selection and stream separation", () => {
  it("shows Historical Final and Recent Late as separate controls with their own periods", async () => {
    wrap(<LayerPanel />);
    const hist = await screen.findByTestId("stream-historical_final");
    const recent = screen.getByTestId("stream-recent_late");
    expect(within(hist).getByText(/Historical Final Run/, { selector: "legend" })).toBeInTheDocument();
    expect(within(recent).getByText(/Recent Late Run/, { selector: "legend" })).toBeInTheDocument();
    expect(hist).toHaveTextContent("2023-12-03 to 2023-12-05");
    expect(hist).toHaveTextContent("V07B");
    expect(recent).toHaveTextContent("2026-10-22 to 2026-10-28");
    expect(recent).toHaveTextContent("V07C");
  });

  it("toggling one rainfall layer never toggles the other", async () => {
    const user = userEvent.setup();
    wrap(<LayerPanel />);
    await user.click(await screen.findByRole("checkbox", { name: "Historical Final Run rainfall" }));
    expect(useUi.getState().layers).toMatchObject({ rain_historical_final: true, rain_recent_late: false });
    await user.click(screen.getByRole("checkbox", { name: "Recent Late Run rainfall" }));
    expect(useUi.getState().layers).toMatchObject({ rain_historical_final: true, rain_recent_late: true });
    expect(screen.getByRole("note")).toHaveTextContent(/Late Run is drawn on top/);
    await user.click(screen.getByRole("checkbox", { name: "Historical Final Run rainfall" }));
    expect(useUi.getState().layers).toMatchObject({ rain_historical_final: false, rain_recent_late: true });
  });

  it("lists terrain baseline and hypothetical scenarios, but not observed ones, as exposure scenarios", async () => {
    wrap(<LayerPanel />);
    const select = await screen.findByLabelText(/Scenario \(terrain baseline/);
    const labels = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(labels.some((l) => /Terrain baseline/.test(l ?? ""))).toBe(true);
    expect(labels.some((l) => /Hypothetical/.test(l ?? ""))).toBe(true);
    expect(labels.some((l) => /Late Run|Final Run/.test(l ?? ""))).toBe(false);
  });

  it("offers a period selector only when a stream has several published periods", async () => {
    const second = { ...LATE, scenario_id: "obs_late_20261105_20261111", observation_start: "2026-11-05", observation_end: "2026-11-11" };
    const catalog: RainfallCatalog = {
      ...CATALOG,
      streams: CATALOG.streams.map((s) => (s.stream === "recent_late" ? { ...s, scenario_ids: [LATE.scenario_id, second.scenario_id] } : s)),
      datasets: [...CATALOG.datasets, second],
    };
    mockApi(standardRoutes({ "/rainfall": catalog }));
    const user = userEvent.setup();
    wrap(<LayerPanel />);
    const select = await within(await screen.findByTestId("stream-recent_late")).findByLabelText("Observation period");
    expect(within(select).getAllByRole("option")).toHaveLength(2);
    await user.selectOptions(select, "obs_late_20261105_20261111");
    expect(useUi.getState().selectedRainfall).toEqual({ historical_final: null, recent_late: "obs_late_20261105_20261111" });
    expect(within(screen.getByTestId("stream-historical_final")).queryByLabelText("Observation period")).toBeNull();
  });

  it("shows unavailable and pending streams honestly instead of a toggle", async () => {
    const catalog: RainfallCatalog = {
      ...CATALOG, datasets: CATALOG.datasets.filter((d) => d.rainfall_stream === "historical_final"),
      streams: CATALOG.streams.map((s) => (s.stream === "recent_late"
        ? { ...s, status: "pending" as const, scenario_ids: [], note: "Candidate datasets exist but failed validation, so none is published." } : s)),
    };
    mockApi(standardRoutes({ "/rainfall": catalog }));
    wrap(<LayerPanel />);
    const recent = await screen.findByTestId("stream-recent_late");
    expect(recent).toHaveTextContent("Pending");
    expect(recent).toHaveTextContent(/failed validation/);
    expect(within(recent).queryByRole("checkbox")).toBeNull();
  });

  it("offers a water layer that is off by default and states it is not a flood extent", async () => {
    wrap(<LayerPanel />);
    const box = await screen.findByRole("checkbox", { name: "Water bodies and waterways" });
    expect(box).not.toBeChecked();
    expect(screen.getByText(/not a flood extent/)).toBeInTheDocument();
    expect(screen.queryByText(/Not yet published by the API/)).toBeNull();
  });

  it("shows loading, then an error with retry", async () => {
    mockApi(standardRoutes({ "/rainfall": new HttpFail(500, "internal_error", "unexpected server error") }));
    wrap(<LayerPanel />);
    expect(screen.getAllByRole("status").length).toBeGreaterThan(0);
    expect(await screen.findByRole("alert")).toHaveTextContent(/unexpected server error/);
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});

describe("RainfallPanel: information shown for the selected stream", () => {
  it("shows the observation window, version, resolution, coverage, source and retrieval time", async () => {
    wrap(<RainfallPanel />);
    const panel = await screen.findByTestId("rainfall-panel");
    expect(panel).toHaveTextContent("Recent Late Run");
    expect(panel).toHaveTextContent("2026-10-22 to 2026-10-28");
    expect(panel).toHaveTextContent("GPM_3IMERGDL V07C");
    expect(panel).toHaveTextContent(/0\.1 degree \(~10 km\)/);
    expect(panel).toHaveTextContent("Valid cells");
    expect(panel).toHaveTextContent(/Missing cells\s*0 \(0\.0%\)/);
    expect(panel).toHaveTextContent("2026-10-30 04:51 UTC");
    expect(panel).toHaveTextContent("NASA GES DISC");
    expect(panel).toHaveTextContent("Recent, provisional");
    expect(panel).toHaveTextContent("Satellite estimate");
    await waitFor(() => expect(panel).toHaveTextContent("Mean of cells (areal total)"));
    expect(panel).toHaveTextContent(/not neighbourhood-level rainfall/);
    expect(panel).toHaveTextContent(/Statistics are over the valid 0\.1 degree/);
  });

  it("switching to the historical stream shows the Final Run, not the Late Run", async () => {
    useUi.setState({ focusStream: "historical_final" });
    wrap(<RainfallPanel />);
    const panel = await screen.findByTestId("rainfall-panel");
    expect(panel).toHaveTextContent("Historical Final Run");
    expect(panel).toHaveTextContent("2023-12-03 to 2023-12-05");
    expect(panel).toHaveTextContent("GPM_3IMERGDF V07B");
    expect(panel).toHaveTextContent("Historical archive");
    expect(panel).not.toHaveTextContent("2026-10-22");
  });

  it("labels older Late data as provisional and possibly superseded, never live", async () => {
    const aged = { ...LATE, data_freshness: { ...LATE.data_freshness, status: "aged_provisional" as const, age_days: 60, note: "Observation ended 60 day(s) before this request. Not live." } };
    mockApi(standardRoutes({ "/rainfall": { ...CATALOG, datasets: [FINAL, aged] } }));
    wrap(<RainfallPanel />);
    expect(await screen.findByText(/Older provisional \(may be superseded\)/)).toBeInTheDocument();
    expect(screen.queryByText(/real-time rainfall|live rainfall/i)).toBeNull();
  });

  it("never mentions the delivered error field or flood depth as a quantity", async () => {
    wrap(<RainfallPanel />);
    await screen.findByText(/Mean of cells/);
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/random.?error|rmse|uncertainty/i);
    expect(text).not.toMatch(/flood depth of|depth \(m\)/i);
  });

  it("shows an explicit empty state when the stream has no published dataset", async () => {
    mockApi(standardRoutes({ "/rainfall": { ...CATALOG, datasets: [FINAL], streams: CATALOG.streams.map((s) => (s.stream === "recent_late" ? { ...s, status: "unavailable", scenario_ids: [] } : s)) } }));
    wrap(<RainfallPanel />);
    expect(await screen.findByText(/no dataset is published for this region/)).toBeInTheDocument();
  });

  it("shows an error when statistics fail but keeps the metadata visible", async () => {
    mockApi(standardRoutes({ [`/rainfall/${LATE.scenario_id}/summary`]: new HttpFail(404, "rainfall_not_found", "not published") }));
    wrap(<RainfallPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("not published");
    expect(screen.getByTestId("rainfall-panel")).toHaveTextContent("GPM_3IMERGDL V07C");
  });

  it("reports a missing-data percentage and explains zero versus missing", async () => {
    const holes = { ...LATE, valid_cell_count: 18, missing_cell_count: 2, missing_percentage: 10 };
    mockApi(standardRoutes({ "/rainfall": { ...CATALOG, datasets: [FINAL, holes] } }));
    wrap(<RainfallPanel />);
    expect(await screen.findByText(/2 \(10\.0%\)/)).toBeInTheDocument();
  });
});

describe("ProvenancePanel", () => {
  it("shows source, version, checksum, granules, limitations and a raster download", async () => {
    wrap(<ProvenancePanel />);
    const panel = await screen.findByTestId("provenance-panel");
    await waitFor(() => expect(panel).toHaveTextContent("GPM_3IMERGDL"));
    expect(panel).toHaveTextContent("V07C");
    expect(panel).toHaveTextContent(/Granules \(7\)/);
    expect(panel).toHaveTextContent("Known limitations");
    expect(panel).toHaveTextContent(/provisional/i);
    const link = within(panel).getByRole("link", { name: /Download the native-resolution GeoTIFF/ });
    expect(link.getAttribute("href")).toBe(`/v1/regions/chennai/rainfall/${LATE.scenario_id}/raster`);
  });
  it("does not expose server file paths", async () => {
    wrap(<ProvenancePanel />);
    await screen.findByText(/Known limitations/);
    expect(document.body.textContent).not.toMatch(/data\/raw|data\/processed|C:\\|D:\\/);
  });
});

describe("ComparisonPanel", () => {
  it("compares streams side by side without implying validation", async () => {
    wrap(<ComparisonPanel />);
    const panel = await screen.findByTestId("comparison-panel");
    expect(panel).toHaveTextContent("Historical Final Run");
    expect(panel).toHaveTextContent("Recent Late Run");
    expect(panel).toHaveTextContent(/does not validate the exposure screening or predict flooding/);
    expect(panel).toHaveTextContent(/not an input of the exposure index/);
    expect(panel).toHaveTextContent(/Hypothetical accumulations \(analyst-chosen, not data\)/);
    expect(within(panel).getByLabelText("Grid of the 20 cell differences").children).toHaveLength(20);
  });
  it("shows an error state", async () => {
    mockApi(standardRoutes({ "/rainfall/comparison": new HttpFail(422, "invalid_ids", "bad ids") }));
    wrap(<ComparisonPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("bad ids");
  });
});

describe("Legend", () => {
  it("is hidden when nothing is on, and shows zero and no-data entries when rainfall is on", async () => {
    useUi.setState({ layers: { ...useUi.getState().layers, exposure: false } });
    const first = wrap(<Legend />);
    expect(first.container).toBeEmptyDOMElement();
    first.unmount();
    useUi.setState({ layers: { ...useUi.getState().layers, rain_recent_late: true } });
    wrap(<Legend />);
    const list = await screen.findByRole("list", { name: "Rainfall accumulation classes" });
    const items = within(list).getAllByRole("listitem").map((i) => i.textContent);
    expect(items[0]).toMatch(/^0 mm \(valid observation, no rain\)/);
    expect(items.at(-1)).toMatch(/No data \(missing, not zero\)/);
    expect(screen.getByText(/Not flood depth/)).toBeInTheDocument();
  });
  it("lists the two streams separately with their own periods", async () => {
    useUi.setState({ layers: { ...useUi.getState().layers, rain_recent_late: true, rain_historical_final: true } });
    wrap(<Legend />);
    expect(await screen.findByText(/2026-10-22 to 2026-10-28 \(UTC\), V07C/)).toBeInTheDocument();
    expect(screen.getByText(/2023-12-03 to 2023-12-05 \(UTC\), V07B/)).toBeInTheDocument();
  });
});

describe("InfoPanel", () => {
  it("switches between rainfall, provenance and compare tabs", async () => {
    const user = userEvent.setup();
    wrap(<InfoPanel />);
    expect(await screen.findByTestId("rainfall-panel")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Provenance" }));
    expect(await screen.findByTestId("provenance-panel")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Compare" }));
    expect(await screen.findByTestId("comparison-panel")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Compare" })).toHaveAttribute("aria-selected", "true");
  });
});
