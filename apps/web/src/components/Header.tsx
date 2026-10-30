import { Layers, Info } from "lucide-react";
import { useRegions } from "../api/hooks";
import { useUi } from "../state/store";

export function Header({ onLayers, onInfo }: { onLayers: () => void; onInfo: () => void }) {
  const region = useUi((s) => s.region);
  const setRegion = useUi((s) => s.setRegion);
  const regions = useRegions();
  return (
    <header className="flex items-center gap-3 border-b border-slate-200 bg-white px-3 py-2">
      <button type="button" onClick={onLayers} aria-label="Open layers" className="rounded p-1 hover:bg-slate-100 lg:hidden"><Layers className="h-5 w-5" aria-hidden /></button>
      <div className="min-w-0">
        <h1 className="text-lg font-bold leading-tight">FloodLens</h1>
        <p className="hidden truncate text-xs text-slate-600 sm:block">See the Risk. Understand the Impact. Find the Safer Way.</p>
      </div>
      <label className="ml-auto flex items-center gap-2 text-sm">
        <span className="text-slate-600">Region</span>
        <select value={region} onChange={(e) => setRegion(e.target.value)} className="rounded border border-slate-300 bg-white p-1"
          disabled={!regions.data}>
          {(regions.data?.regions ?? [{ region_id: region, name: region }]).map((r) => <option key={r.region_id} value={r.region_id}>{r.name}</option>)}
        </select>
      </label>
      <span className="hidden rounded bg-amber-100 px-2 py-1 text-xs font-medium text-amber-900 md:inline">
        Exploratory screening · not a forecast, warning or flood-depth model
      </span>
      <button type="button" onClick={onInfo} aria-label="Open data information" className="rounded p-1 hover:bg-slate-100 lg:hidden"><Info className="h-5 w-5" aria-hidden /></button>
    </header>
  );
}
