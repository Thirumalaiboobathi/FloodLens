import { useRainfallCatalog, useRainfallComparison } from "../api/hooks";
import { STREAM_LABEL, formatMm } from "../lib/rainfall";
import { useUi } from "../state/store";
import { Empty, ErrorNote, Loading } from "./common";

const signed = (v: number | null) => (v === null ? "no data" : `${v > 0 ? "+" : ""}${v.toFixed(1)} mm`);

export function ComparisonPanel() {
  const region = useUi((s) => s.region);
  const catalog = useRainfallCatalog(region);
  const ids = (catalog.data?.datasets ?? []).map((d) => d.scenario_id);
  const cmp = useRainfallComparison(region, ids, true);
  if (catalog.isLoading || cmp.isLoading) return <Loading what="comparison" />;
  if (catalog.isError) return <ErrorNote error={catalog.error} />;
  if (cmp.isError) return <ErrorNote error={cmp.error} onRetry={() => cmp.refetch()} />;
  if (!cmp.data || cmp.data.observed.length === 0) return <Empty>No observed rainfall is published for comparison.</Empty>;
  const c = cmp.data;
  return (
    <div className="space-y-3 p-3" data-testid="comparison-panel">
      <h2 className="text-base font-semibold">Compare rainfall context</h2>
      <p role="note" className="rounded bg-amber-50 p-2 text-xs text-amber-900">{c.caution}</p>
      <table className="w-full text-left text-xs [&_td]:pr-2 [&_td]:align-top [&_th]:pr-2 [&_th]:align-top">
        <caption className="sr-only">Observed rainfall streams side by side</caption>
        <thead><tr><th scope="col">Stream</th><th scope="col">Period</th><th scope="col">Mean</th><th scope="col">Max</th><th scope="col">mm/day</th></tr></thead>
        <tbody>
          {c.observed.map((o) => (
            <tr key={o.scenario_id} className="border-t border-slate-100">
              <th scope="row" className="py-1 pr-1 font-medium">{STREAM_LABEL[o.rainfall_stream]}<span className="block font-normal text-slate-500">{o.product_version}</span></th>
              <td><span className="block">{o.observation_start}</span><span className="block">to {o.observation_end} ({o.n_days} d)</span></td>
              <td>{formatMm(o.mean_accumulation_mm)}</td><td>{formatMm(o.max_accumulation_mm)}</td>
              <td>{o.mean_daily_rate_mm_per_day === null ? "no data" : o.mean_daily_rate_mm_per_day.toFixed(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {c.pairwise && (
        <section aria-label="Cell-by-cell difference">
          <h3 className="text-sm font-semibold">Difference in cell accumulation (second minus first)</h3>
          <p className="text-xs text-slate-600">{c.pairwise.caution}</p>
          <p className="text-xs">Mean cell difference: <strong>{signed(c.pairwise.mean_difference_mm)}</strong>
            {!c.pairwise.same_duration && c.pairwise.mean_daily_rate_difference_mm_per_day !== null &&
              <> · mean daily rate difference: <strong>{c.pairwise.mean_daily_rate_difference_mm_per_day.toFixed(1)} mm/day</strong></>}
          </p>
          <div className="mt-1 grid grid-cols-4 gap-0.5" role="img" aria-label="Grid of the 20 cell differences">
            {c.pairwise.cells.map((x) => (
              <span key={`${x.row}-${x.col}`} title={`row ${x.row}, col ${x.col}: ${signed(x.b_minus_a_mm)}`}
                className="rounded bg-slate-100 px-1 py-0.5 text-center text-[10px]">
                {x.b_minus_a_mm === null ? "–" : x.b_minus_a_mm.toFixed(0)}
              </span>
            ))}
          </div>
        </section>
      )}

      <section aria-label="Hypothetical scenarios for reference">
        <h3 className="text-sm font-semibold">Hypothetical accumulations (analyst-chosen, not data)</h3>
        <p className="text-xs text-slate-600">{c.hypothetical_reference.map((h) => `${h.rainfall_accumulation_mm} mm`).join(" · ")}</p>
      </section>
      <p className="text-xs text-slate-600">{c.exposure_note}</p>
    </div>
  );
}
