import { useUi } from "../state/store";
import { ComparisonPanel } from "./ComparisonPanel";
import { ProvenancePanel } from "./ProvenancePanel";
import { RainfallPanel } from "./RainfallPanel";

const TABS = [
  { id: "rainfall", label: "Rainfall" },
  { id: "provenance", label: "Provenance" },
  { id: "compare", label: "Compare" },
] as const;

export function InfoPanel() {
  const panel = useUi((s) => s.panel);
  const setPanel = useUi((s) => s.setPanel);
  return (
    <section aria-label="Data information" className="flex h-full flex-col">
      <div role="tablist" aria-label="Information panels" className="flex border-b border-slate-200">
        {TABS.map((t) => (
          <button key={t.id} role="tab" type="button" id={`tab-${t.id}`} aria-selected={panel === t.id} aria-controls="info-body"
            onClick={() => setPanel(t.id)}
            className={`flex-1 px-3 py-2 text-sm ${panel === t.id ? "border-b-2 border-sky-600 font-semibold" : "text-slate-600 hover:bg-slate-50"}`}>
            {t.label}
          </button>
        ))}
      </div>
      <div id="info-body" role="tabpanel" aria-labelledby={`tab-${panel}`} className="min-h-0 flex-1 overflow-auto">
        {panel === "rainfall" && <RainfallPanel />}
        {panel === "provenance" && <ProvenancePanel />}
        {panel === "compare" && <ComparisonPanel />}
      </div>
    </section>
  );
}
