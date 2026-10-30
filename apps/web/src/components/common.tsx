import { AlertTriangle, Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { ApiError } from "../api/client";

export function Loading({ what }: { what: string }) {
  return (
    <p role="status" aria-live="polite" className="flex items-center gap-2 p-3 text-sm text-slate-600">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading {what}…
    </p>
  );
}

export function ErrorNote({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const e = error instanceof ApiError ? error : null;
  return (
    <div role="alert" className="m-2 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-900">
      <p className="flex items-center gap-2 font-medium"><AlertTriangle className="h-4 w-4" aria-hidden /> Could not load this data</p>
      <p className="mt-1">{e ? e.message : "An unexpected error occurred."}</p>
      {e?.code && <p className="mt-1 text-xs text-red-700">Code: {e.code}{e.requestId ? ` · request ${e.requestId}` : ""}</p>}
      {onRetry && <button type="button" onClick={onRetry} className="mt-2 rounded border border-red-300 bg-white px-2 py-1 text-xs hover:bg-red-100">Retry</button>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="m-2 rounded border border-dashed border-slate-300 p-3 text-sm text-slate-600">{children}</p>;
}

export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-3 border-b border-slate-100 py-1 text-sm last:border-0">
      <dt className="text-slate-600">{label}</dt>
      <dd className="text-right font-medium text-slate-900">{children}</dd>
    </div>
  );
}

export function Badge({ children, tone = "slate" }: { children: ReactNode; tone?: "slate" | "amber" | "blue" | "teal" }) {
  const tones = { slate: "bg-slate-100 text-slate-800", amber: "bg-amber-100 text-amber-900", blue: "bg-blue-100 text-blue-900", teal: "bg-teal-100 text-teal-900" };
  return <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${tones[tone]}`}>{children}</span>;
}
