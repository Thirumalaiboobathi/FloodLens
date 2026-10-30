import { useRainfallCatalog } from "../api/hooks";
import type { RainfallStream } from "../api/types";
import { STREAM_LABEL, rainfallLegend } from "../lib/rainfall";
import { EXPOSURE_COLORS } from "../map/layers";
import { STREAM_LAYER, resolveDataset, useUi } from "../state/store";

export function Legend() {
  const ui = useUi();
  const catalog = useRainfallCatalog(ui.region);
  const active = (Object.keys(STREAM_LAYER) as RainfallStream[]).filter((s) => ui.layers[STREAM_LAYER[s]]);
  const features = ui.layers.roads || ui.layers.facilities || ui.layers.water;
  if (!ui.layers.exposure && active.length === 0 && !features) return null;
  return (
    <aside aria-label="Map legend" className="pointer-events-auto max-h-[60vh] w-64 max-w-[calc(100vw-1.5rem)] max-lg:max-h-[30vh] overflow-auto rounded bg-white/95 p-3 text-xs shadow">
      {ui.layers.exposure && (
        <section className="mb-2">
          <h3 className="font-semibold">Relative exposure class</h3>
          <p className="text-slate-500">Class 1 (lower) to 5 (higher), ranked against the terrain baseline. Not a risk level or depth.</p>
          <ul className="mt-1 flex gap-1" aria-label="Exposure classes">
            {EXPOSURE_COLORS.map((c, i) => (
              <li key={c} className="flex flex-1 flex-col items-center"><span className="h-3 w-full" style={{ background: c }} />{i + 1}</li>
            ))}
          </ul>
        </section>
      )}
      {features && (
        <section className="mb-2" aria-label="Roads and facilities key">
          <h3 className="font-semibold">Roads, facilities and water</h3>
          <ul className="space-y-0.5">
            {ui.layers.water && <li className="flex items-center gap-2"><span aria-hidden className="inline-block h-3 w-5 border" style={{ background: "#1f6f8b59", borderColor: "#1f6f8b" }} />Mapped water body or waterway (OSM, not a flood extent)</li>}
            {ui.layers.roads && <li className="flex items-center gap-2"><span aria-hidden className="inline-block h-0.5 w-5" style={{ background: "#7a1f1f" }} />Roads touching the highest class</li>}
            {ui.layers.facilities && <li className="flex items-center gap-2"><span aria-hidden className="inline-block h-3 w-3 rounded-full border border-white" style={{ background: "#c92a2a" }} />Hospital or clinic</li>}
            {ui.layers.facilities && <li className="flex items-center gap-2"><span aria-hidden className="inline-block h-3 w-3 rounded-full border border-white" style={{ background: "#5f3dc4" }} />School</li>}
          </ul>
        </section>
      )}
      {active.length > 0 && (
        <section>
          <h3 className="font-semibold">Rainfall accumulation (mm)</h3>
          <p className="mb-1 text-slate-500">Total over each layer&apos;s observation period. Not flood depth.</p>
          <ul className="space-y-0.5" aria-label="Rainfall accumulation classes">
            {rainfallLegend().map((e) => (
              <li key={e.key} className="flex items-center gap-2" data-kind={e.kind}>
                <span aria-hidden className="inline-block h-3 w-5 border border-slate-400"
                  style={e.kind === "nodata"
                    ? { background: `repeating-linear-gradient(45deg, #e9ecef 0 3px, #5f6368 3px 4px)` }
                    : { background: e.color }} />
                {e.label}
              </li>
            ))}
          </ul>
          <ul className="mt-2 space-y-1 border-t border-slate-200 pt-2">
            {active.map((s) => {
              const d = resolveDataset(catalog.data?.datasets ?? [], s, ui.selectedRainfall[s]);
              return (
                <li key={s}>
                  <span className="font-medium">{STREAM_LABEL[s]}</span>
                  {d ? `: ${d.observation_start} to ${d.observation_end} (UTC), ${d.product_version}` : ": loading"}
                  <span className="block text-slate-500">{s === "recent_late" ? "dashed teal outline" : "solid navy outline"}, ~10 km cells</span>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </aside>
  );
}
