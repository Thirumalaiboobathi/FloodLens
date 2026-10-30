import { useRainfallCatalog, useScenarioCatalog } from "../api/hooks";
import type { RainfallStream, StreamAvailability } from "../api/types";
import { STREAM_BLURB, STREAM_LABEL } from "../lib/rainfall";
import { type LayerId, STREAM_LAYER, resolveDataset, useUi } from "../state/store";
import { Badge, ErrorNote, Loading } from "./common";

const STREAMS: RainfallStream[] = ["historical_final", "recent_late"];

function Toggle({ id, label, hint }: { id: LayerId; label: string; hint?: string }) {
  const on = useUi((s) => s.layers[id]);
  const toggle = useUi((s) => s.toggleLayer);
  return (
    <label className="flex cursor-pointer items-start gap-2 py-1 text-sm">
      <input type="checkbox" className="mt-1" checked={on} onChange={() => toggle(id)} aria-label={label} />
      <span>{label}{hint && <span className="block text-xs text-slate-500">{hint}</span>}</span>
    </label>
  );
}

function RainfallStreamControl({ stream, info }: { stream: RainfallStream; info?: StreamAvailability }) {
  const region = useUi((s) => s.region);
  const chosen = useUi((s) => s.selectedRainfall[stream]);
  const select = useUi((s) => s.selectRainfall);
  const focus = useUi((s) => s.focusStream === stream);
  const setFocus = useUi((s) => s.setFocusStream);
  const catalog = useRainfallCatalog(region);
  const all = catalog.data?.datasets ?? [];
  const current = resolveDataset(all, stream, chosen);
  const options = all.filter((d) => d.rainfall_stream === stream);
  const available = info?.status === "available" && options.length > 0;
  return (
    <fieldset className={`rounded border p-2 ${focus ? "border-sky-400" : "border-slate-200"}`} data-testid={`stream-${stream}`}>
      <legend className="px-1 text-xs font-semibold uppercase tracking-wide text-slate-600">{STREAM_LABEL[stream]}</legend>
      {available ? (
        <>
          <Toggle id={STREAM_LAYER[stream]} label={`${STREAM_LABEL[stream]} rainfall`} hint={STREAM_BLURB[stream]} />
          {options.length > 1 ? (
            <label className="block text-xs text-slate-600">
              Observation period
              <select className="mt-1 block w-full rounded border border-slate-300 bg-white p-1 text-sm"
                value={current?.scenario_id ?? ""} onChange={(e) => select(stream, e.target.value)}>
                {options.map((d) => <option key={d.scenario_id} value={d.scenario_id}>{d.observation_start} to {d.observation_end} ({d.product_version})</option>)}
              </select>
            </label>
          ) : current && (
            <p className="text-xs text-slate-600">{current.observation_start} to {current.observation_end} (UTC days), {current.product_version}</p>
          )}
          <button type="button" onClick={() => setFocus(stream)} aria-pressed={focus}
            className="mt-1 text-xs text-sky-800 underline">Show details for this stream</button>
        </>
      ) : (
        <div className="text-sm text-slate-600">
          <Badge tone="amber">{info?.status === "pending" ? "Pending" : "Unavailable"}</Badge>
          <p className="mt-1 text-xs">{info?.note ?? "No dataset has been published for this region."}</p>
        </div>
      )}
    </fieldset>
  );
}

export function LayerPanel() {
  const region = useUi((s) => s.region);
  const scenario = useUi((s) => s.exposureScenario);
  const setScenario = useUi((s) => s.setExposureScenario);
  const scn = useScenarioCatalog(region);
  const rain = useRainfallCatalog(region);
  const exposure = (scn.data?.scenarios ?? []).filter((s) => s.has_exposure_outputs);
  const bothOn = useUi((s) => s.layers.rain_historical_final && s.layers.rain_recent_late);
  return (
    <nav aria-label="Map layers and scenarios" className="space-y-3 p-3">
      <section aria-labelledby="exp-h">
        <h2 id="exp-h" className="mb-1 text-sm font-semibold">Exposure screening</h2>
        {scn.isLoading && <Loading what="scenarios" />}
        {scn.isError && <ErrorNote error={scn.error} onRetry={() => scn.refetch()} />}
        {exposure.length > 0 && (
          <label className="block text-xs text-slate-600">
            Scenario (terrain baseline or hypothetical accumulation)
            <select className="mt-1 block w-full rounded border border-slate-300 bg-white p-1 text-sm" value={scenario}
              onChange={(e) => setScenario(e.target.value)}>
              {exposure.map((s) => <option key={s.scenario_id} value={s.scenario_id}>{s.label}</option>)}
            </select>
          </label>
        )}
        <Toggle id="exposure" label="Exposure classification" hint="Relative terrain-based classes, not risk or depth" />
      </section>

      <section aria-labelledby="rain-h">
        <h2 id="rain-h" className="mb-1 text-sm font-semibold">Observed rainfall (context only)</h2>
        <p className="mb-2 text-xs text-slate-500">Satellite-estimated accumulation. Not part of the exposure index.</p>
        {rain.isLoading && <Loading what="rainfall datasets" />}
        {rain.isError && <ErrorNote error={rain.error} onRetry={() => rain.refetch()} />}
        {rain.data && (
          <div className="space-y-2">
            {STREAMS.map((s) => <RainfallStreamControl key={s} stream={s} info={rain.data.streams.find((x) => x.stream === s)} />)}
            {bothOn && <p role="note" className="text-xs text-amber-800">Both streams cover the same cells; the Late Run is drawn on top. Switch one off to view the other.</p>}
          </div>
        )}
      </section>

      <section aria-labelledby="inf-h">
        <h2 id="inf-h" className="mb-1 text-sm font-semibold">Roads, facilities and water</h2>
        <Toggle id="roads" label="Roads touching the highest class" hint="Loaded for the visible area only" />
        <Toggle id="facilities" label="Hospitals, clinics and schools" hint="Loaded for the visible area only" />
        <Toggle id="water" label="Water bodies and waterways" hint="Mapped by OpenStreetMap contributors; not a flood extent" />
      </section>
    </nav>
  );
}
