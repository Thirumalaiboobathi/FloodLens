import { api } from "../api/client";
import { useRainfallCatalog, useRainfallProvenance } from "../api/hooks";
import { STREAM_LABEL, formatUtc } from "../lib/rainfall";
import { resolveDataset, useUi } from "../state/store";
import { Badge, Empty, ErrorNote, Loading, Row } from "./common";

export function ProvenancePanel() {
  const { region, focusStream, selectedRainfall } = useUi();
  const catalog = useRainfallCatalog(region);
  const meta = resolveDataset(catalog.data?.datasets ?? [], focusStream, selectedRainfall[focusStream]);
  const prov = useRainfallProvenance(region, meta?.scenario_id ?? null);

  if (catalog.isLoading) return <Loading what="provenance" />;
  if (catalog.isError) return <ErrorNote error={catalog.error} onRetry={() => catalog.refetch()} />;
  if (!meta) return <Empty>No published {STREAM_LABEL[focusStream]} dataset, so there is no provenance to show.</Empty>;
  return (
    <div className="space-y-3 p-3" data-testid="provenance-panel">
      <h2 className="text-base font-semibold">Provenance: {STREAM_LABEL[meta.rainfall_stream]}</h2>
      {prov.isLoading && <Loading what="provenance" />}
      {prov.isError && <ErrorNote error={prov.error} onRetry={() => prov.refetch()} />}
      {prov.data && (
        <>
          <dl>
            <Row label="Product">{prov.data.product_identifier}</Row>
            <Row label="Version(s)">{prov.data.product_versions.join(", ")}</Row>
            <Row label="Provider">{prov.data.source.provider ?? "unknown"}</Row>
            <Row label="Licence">{prov.data.source.license ?? "see source"}</Row>
            <Row label="Retrieved">{formatUtc(prov.data.retrieval_timestamp)}</Row>
            <Row label="Raw granules">
              <Badge tone={prov.data.raw_verification === "verified" ? "teal" : "amber"}>{prov.data.raw_verification.replace("_", " ")}</Badge>
            </Row>
            <Row label="GeoTIFF SHA-256"><code className="break-all text-xs">{prov.data.processed_sha256.slice(0, 16)}…</code></Row>
          </dl>
          {prov.data.source.attribution && <p className="text-xs text-slate-600">{prov.data.source.attribution}</p>}
          <details>
            <summary className="cursor-pointer text-sm font-medium">Granules ({prov.data.granules.length})</summary>
            <ul className="mt-1 max-h-40 overflow-auto text-xs">
              {prov.data.granules.map((g) => <li key={g.file}>{g.date} · {g.product_version}</li>)}
            </ul>
          </details>
          <details>
            <summary className="cursor-pointer text-sm font-medium">How it was processed</summary>
            <ul className="mt-1 list-disc pl-5 text-xs">{prov.data.processing.map((p) => <li key={p}>{p}</li>)}</ul>
          </details>
          <section aria-label="Known limitations">
            <h3 className="text-sm font-semibold">Known limitations</h3>
            <ul className="mt-1 list-disc pl-5 text-xs text-slate-700">{prov.data.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
          </section>
          {prov.data.warnings.length > 0 && <ul className="rounded bg-amber-50 p-2 text-xs text-amber-900">{prov.data.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
          <a className="inline-block text-sm text-sky-800 underline" href={api.rasterHref(region, meta.scenario_id)} download>Download the native-resolution GeoTIFF</a>
        </>
      )}
    </div>
  );
}
