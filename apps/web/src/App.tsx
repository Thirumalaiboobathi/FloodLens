import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { Route, Routes } from "react-router-dom";
import { Header } from "./components/Header";
import { InfoPanel } from "./components/InfoPanel";
import { Legend } from "./components/Legend";
import { LayerPanel } from "./components/LayerPanel";
import { MapView } from "./map/MapView";

export function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } } });
}

function MapPage() {
  const [drawer, setDrawer] = useState<"layers" | "info" | null>(null);
  const side = "absolute inset-y-0 z-20 w-80 max-w-[90vw] overflow-auto bg-white shadow-lg transition-transform lg:static lg:z-auto lg:translate-x-0 lg:shadow-none";
  return (
    <div className="flex h-screen flex-col">
      <Header onLayers={() => setDrawer(drawer === "layers" ? null : "layers")} onInfo={() => setDrawer(drawer === "info" ? null : "info")} />
      <div className="relative flex min-h-0 flex-1 overflow-hidden">
        <div className={`${side} left-0 border-r border-slate-200 ${drawer === "layers" ? "" : "-translate-x-full max-lg:invisible"}`}><LayerPanel /></div>
        <main className="relative min-w-0 flex-1">
          <MapView />
          <div className="pointer-events-none absolute bottom-6 left-3 max-lg:bottom-14"><Legend /></div>
        </main>
        <div className={`${side} right-0 border-l border-slate-200 lg:w-96 ${drawer === "info" ? "" : "translate-x-full max-lg:invisible"}`}><InfoPanel /></div>
      </div>
    </div>
  );
}

export function App() {
  const [client] = useState(makeQueryClient);
  return (
    <QueryClientProvider client={client}>
      <Routes>
        <Route path="/" element={<MapPage />} />
        <Route path="*" element={<p className="p-6">Page not found. <a className="underline" href="/">Back to the map</a></p>} />
      </Routes>
    </QueryClientProvider>
  );
}
