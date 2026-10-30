import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useRainfallCatalog, useRainfallSummary } from "../api/hooks";
import { FRESHNESS_LABEL, STREAM_LABEL, formatMm, formatPeriod, formatUtc } from "../lib/rainfall";
import { resolveDataset, useUi } from "../state/store";
import { Badge, Empty, ErrorNote, Loading, Row } from "./common";

export function RainfallPanel() {
  const { region, focusStream, selectedRainfall } = useUi();
  const catalog = useRainfallCatalog(region);
  const meta = resolveDataset(catalog.data?.datasets ?? [], focusStream, selectedRainfall[focusStream]);
  const summary = useRainfallSummary(region, meta?.scenario_id ?? null);

  if (catalog.isLoading) return <Loading what="rainfall information" />;
  if (catalog.isError) return <ErrorNote error={catalog.error} onRetry={() => catalog.refetch()} />;
  if (!meta) {
    const s = catalog.data?.streams.find((x) => x.stream === focusStream);
    return <Empty>{STREAM_LABEL[focusStream]}: {s?.status === "pending" ? "candidate data failed validation, so nothing is published." : "no dataset is published for this region."}</Empty>;
  }
  const f = meta.data_freshness;
  const st = summary.data?.accumulation_mm;
  return (
    <div className="space-y-3 p-3" data-testid="rainfall-panel">
      <header>
        <h2 className="text-base font-semibold">{STREAM_LABEL[meta.rainfall_stream]}</h2>
        <p className="text-sm text-slate-700">{formatPeriod(meta.observation_start, meta.observation_end)} (UTC days)</p>
        <div className="mt-1 flex flex-wrap gap-1">
          <Badge tone={meta.rainfall_stream === "recent_late" ? "teal" : "blue"}>{meta.product_identifier} {meta.product_version}</Badge>
          <Badge tone="amber">{FRESHNESS_LABEL[f.status]}</Badge>
          <Badge>Satellite estimate</Badge>
        </div>
        <p className="mt-1 text-xs text-slate-600">{f.note}</p>
      </header>

      <section aria-label="Rainfall accumulation statistics">
        <h3 className="text-sm font-semibold">Rainfall accumulation over the observation window</h3>
        {summary.isLoading && <Loading what="statistics" />}
        {summary.isError && <ErrorNote error={summary.error} onRetry={() => summary.refetch()} />}
        {summary.data && st && (
          <>
            <dl>
              <Row label="Mean of cells (areal total)">{formatMm(st.mean_mm)}</Row>
              <Row label="Minimum cell">{formatMm(st.min_mm)}</Row>
              <Row label="Maximum cell">{formatMm(st.max_mm)}</Row>
              <Row label="Mean daily rate">{summary.data.mean_daily_rate_mm_per_day === null ? "no data" : `${summary.data.mean_daily_rate_mm_per_day.toFixed(1)} mm/day`}</Row>
            </dl>
            <p className="mt-1 text-xs text-slate-600">{summary.data.statistic_scope}</p>
            <p role="note" className="mt-1 rounded bg-amber-50 p-2 text-xs text-amber-900">{summary.data.coverage_caveat}</p>
          </>
        )}
      </section>

      <section aria-label="Data coverage and quality">
        <h3 className="text-sm font-semibold">Coverage and data quality</h3>
        <dl>
          <Row label="Valid cells">{meta.valid_cell_count}</Row>
          <Row label="Missing cells">{meta.missing_cell_count} ({meta.missing_percentage.toFixed(1)}%)</Row>
          <Row label="Native resolution">{meta.native_resolution}</Row>
          <Row label="Coverage">{meta.spatial_coverage.description}</Row>
          <Row label="UTC period">{formatUtc(meta.observation_start_utc)} to {formatUtc(meta.observation_end_utc)}</Row>
          <Row label="Retrieved">{formatUtc(meta.retrieval_timestamp)}</Row>
          <Row label="Source">{meta.source.provider ?? "unknown"}</Row>
        </dl>
        {meta.missing_cell_count === 0 && <p className="mt-1 text-xs text-slate-500">No cells are missing in this window. A 0 mm cell would still be a valid observation, shown differently from missing data.</p>}
      </section>

      {summary.data && summary.data.daily.length > 0 && (
        <section aria-label="Daily mean rainfall">
          <h3 className="text-sm font-semibold">Daily mean over the cells (mm/day)</h3>
          <div className="h-36" role="img" aria-label={`Bar chart of daily mean rainfall, ${summary.data.daily.length} days`}>
            <ResponsiveContainer width="100%" height="100%" minWidth={0}>
              <BarChart data={summary.data.daily.map((d) => ({ day: d.date.slice(5), mm: d.mean_mm }))}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="day" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }} width={32} />
                <Tooltip formatter={(v) => [`${v} mm/day`, "Daily mean"]} />
                <Bar dataKey="mm" fill="#2f73b8" isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>
      )}

      <p className="text-xs text-slate-500">{meta.disclaimer}</p>
    </div>
  );
}
