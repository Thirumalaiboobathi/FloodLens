import { keepPreviousData, useQuery } from "@tanstack/react-query";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { usePolygons, useRainfallCatalog, useRainfallCells, useRegions } from "../api/hooks";
import type { RainfallStream } from "../api/types";
import { BLANK_STYLE, loadRuntimeConfig, loadStyle, resolveBasemap } from "./basemap";
import { describeCell } from "../lib/rainfall";
import { type Bounds, MIN_VECTOR_ZOOM, bboxParam, debounce, snapBounds, vectorKey } from "../lib/viewport";
import { STREAM_LAYER, resolveDataset, useUi } from "../state/store";
import {
  HATCH_IMAGE, exposureLayer, facilitiesLayer, makeHatch, rainfallLayerIds, rainfallLayers, rainfallSourceId,
  regionBoxLayer, roadsLayer, waterLayers,
} from "./layers";

const STREAMS: RainfallStream[] = ["historical_final", "recent_late"];
const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
const OWN_SOURCES = ["exposure", "water", "roads", "facilities", "region-box", ...STREAMS.map(rainfallSourceId)];
const ATTRIBUTION = "FloodLens screening · IMERG: NASA GES DISC · OSM contributors (ODbL) · Copernicus DEM";

function useEverTrue(flag: boolean): boolean {
  const [seen, setSeen] = useState(flag);
  useEffect(() => { if (flag) setSeen(true); }, [flag]);
  return seen || flag;
}

interface Viewport { bounds: Bounds; zoom: number }

export function MapView() {
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [ready, setReady] = useState(false);
  const [basemapFailed, setBasemapFailed] = useState(false);
  const [viewport, setViewport] = useState<Viewport | null>(null);
  const ui = useUi();
  const regions = useRegions();
  const bbox = regions.data?.regions.find((r) => r.region_id === ui.region)?.bbox;
  const catalog = useRainfallCatalog(ui.region);

  const dataset = (s: RainfallStream) =>
    resolveDataset(catalog.data?.datasets ?? [], s, ui.selectedRainfall[s])?.scenario_id ?? null;
  const histId = dataset("historical_final");
  const recentId = dataset("recent_late");
  // cells are fetched only once their layer is switched on
  // once requested, a query stays attached while its layer is hidden, so toggling never refetches
  const wantHist = useEverTrue(ui.layers.rain_historical_final);
  const wantRecent = useEverTrue(ui.layers.rain_recent_late);
  const histCells = useRainfallCells(ui.region, wantHist ? histId : null);
  const recentCells = useRainfallCells(ui.region, wantRecent ? recentId : null);
  const polys = usePolygons(ui.region, ui.exposureScenario, ui.layers.exposure);

  const inView = viewport !== null && viewport.zoom >= MIN_VECTOR_ZOOM;
  const snapped = viewport ? snapBounds(viewport.bounds) : null;
  const roads = useQuery({
    queryKey: ["roads", ...(viewport ? [vectorKey("roads", ui.exposureScenario, viewport.bounds, viewport.zoom)] : [])],
    queryFn: ({ signal }) => api.roads(ui.region, ui.exposureScenario, bboxParam(snapped!), viewport!.zoom, signal),
    enabled: ui.layers.roads && inView, staleTime: Infinity, placeholderData: keepPreviousData,
  });
  // water is mapped reference geometry: viewport-bounded, and only while the box is within the API's span limit
  const waterOk = viewport !== null && snapped !== null && snapped[2] - snapped[0] <= 2 && snapped[3] - snapped[1] <= 2;
  const water = useQuery({
    queryKey: ["water", ...(viewport ? [vectorKey("water", ui.region, viewport.bounds, 0)] : [])],
    queryFn: ({ signal }) => api.water(ui.region, bboxParam(snapped!), signal),
    enabled: ui.layers.water && waterOk, staleTime: Infinity, placeholderData: keepPreviousData,
  });
  const facilities = useQuery({
    queryKey: ["facilities", ...(viewport ? [vectorKey("facilities", ui.exposureScenario, viewport.bounds, 14)] : [])],
    queryFn: () => api.facilities(ui.region, ui.exposureScenario, bboxParam(snapped!)),
    enabled: ui.layers.facilities && inView, staleTime: Infinity, placeholderData: keepPreviousData,
  });

  // ---- map lifecycle -----------------------------------------------------------------------------
  useEffect(() => {
    if (!el.current) return;
    let cancelled = false;
    let cleanup: (() => void) | null = null;
    const start = (style: maplibregl.StyleSpecification | string, failed: boolean) => {
      if (cancelled || !el.current) return;
      setBasemapFailed(failed);
      const map = new maplibregl.Map({
        container: el.current, style, center: [80.2, 13.05], zoom: 9.2,
        attributionControl: false, dragRotate: false,
      });
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
      map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
      map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: ATTRIBUTION }));
      const publish = debounce(() => {
        const b = map.getBounds();
        setViewport({ bounds: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], zoom: map.getZoom() });
      }, 300);
      map.on("moveend", publish);
      // a basemap tile/sprite/glyph failure is non-fatal: data layers are our own GeoJSON sources
      map.on("error", (e) => {
        const id = (e as { sourceId?: string }).sourceId;
        if (id && !OWN_SOURCES.includes(id)) setBasemapFailed(true);
      });
      map.on("load", () => {
        const hatch = makeHatch();
        if (hatch && !map.hasImage(HATCH_IMAGE)) map.addImage(HATCH_IMAGE, hatch);
        for (const id of OWN_SOURCES) map.addSource(id, { type: "geojson", data: EMPTY });
        const add = (l: unknown) => map.addLayer(l as maplibregl.LayerSpecification);
        add(regionBoxLayer);
        add(exposureLayer(false));
        for (const s of STREAMS) rainfallLayers(s, false).forEach(add);
        waterLayers(false).forEach(add);
        add(roadsLayer(false));
        add(facilitiesLayer(false));
        for (const s of STREAMS) {
          map.on("click", rainfallLayerIds(s).fill, (e) => popup(map, e));
          map.on("click", rainfallLayerIds(s).nodata, (e) => popup(map, e));
          for (const id of [rainfallLayerIds(s).fill, rainfallLayerIds(s).nodata]) {
            map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
            map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
          }
        }
        setReady(true);
        publish();
      });
      mapRef.current = map;
      cleanup = () => { publish.cancel(); map.remove(); mapRef.current = null; setReady(false); };
    };
    void loadRuntimeConfig().then(async (runtime) => {
      const source = resolveBasemap({ ...(import.meta.env ?? {}), ...runtime }); // deploy-time config wins
      if (source.kind === "none") start(BLANK_STYLE, false);
      else { const r = await loadStyle(source); start(r.style, r.failed); }
    });
    return () => { cancelled = true; cleanup?.(); };
  }, []);

  const setData = (id: string, data: unknown) =>
    (mapRef.current?.getSource(id) as maplibregl.GeoJSONSource | undefined)?.setData((data ?? EMPTY) as GeoJSON.FeatureCollection);

  // ---- data -> sources ---------------------------------------------------------------------------
  useEffect(() => {
    if (!ready || !bbox) return;
    const { west: w, south: s, east: e, north: n } = bbox;
    setData("region-box", { type: "FeatureCollection", features: [{ type: "Feature", properties: {},
      geometry: { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] } }] });
    mapRef.current?.fitBounds([[w, s], [e, n]], { padding: 40, animate: false });
  }, [ready, bbox?.west, bbox?.south, bbox?.east, bbox?.north]);
  useEffect(() => { if (ready) setData("exposure", polys.data); }, [ready, polys.data]);
  useEffect(() => { if (ready) setData(rainfallSourceId("historical_final"), histCells.data); }, [ready, histCells.data]);
  useEffect(() => { if (ready) setData(rainfallSourceId("recent_late"), recentCells.data); }, [ready, recentCells.data]);
  useEffect(() => { if (ready) setData("water", ui.layers.water && waterOk ? water.data : undefined); }, [ready, water.data, ui.layers.water, waterOk]);
  useEffect(() => { if (ready) setData("roads", ui.layers.roads && inView ? roads.data : undefined); }, [ready, roads.data, ui.layers.roads, inView]);
  useEffect(() => { if (ready) setData("facilities", ui.layers.facilities && inView ? facilities.data : undefined); }, [ready, facilities.data, ui.layers.facilities, inView]);

  // ---- visibility --------------------------------------------------------------------------------
  const visibility = useMemo(() => {
    const v: Record<string, boolean> = {
      "exposure-fill": ui.layers.exposure, "water-fill": ui.layers.water, "water-line": ui.layers.water, "roads-line": ui.layers.roads, "facilities-circle": ui.layers.facilities,
    };
    for (const s of STREAMS) for (const id of Object.values(rainfallLayerIds(s))) v[id] = ui.layers[STREAM_LAYER[s]];
    return v;
  }, [ui.layers]);
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    for (const [id, on] of Object.entries(visibility))
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
  }, [ready, visibility]);

  const hint = ui.layers.roads || ui.layers.facilities
    ? viewport && !inView ? `Zoom in to level ${MIN_VECTOR_ZOOM}+ to load roads and facilities for the visible area.` : null
    : null;
  const failed = [polys, histCells, recentCells, roads, facilities, water].find((q) => q.isError);

  return (
    <div className="relative h-full w-full" data-testid="map-region">
      <div ref={el} className="h-full w-full" role="application" aria-label="Interactive flood exposure and rainfall context map" />
      {hint && <p role="status" className="absolute left-1/2 top-3 -translate-x-1/2 rounded bg-white/95 px-3 py-1 text-xs shadow">{hint}</p>}
      {basemapFailed && (
        <p role="status" className="absolute left-3 top-3 max-w-xs rounded bg-amber-50 px-3 py-1 text-xs text-amber-900 shadow">
          Basemap unavailable. Data layers are shown on a plain background.
        </p>
      )}
      {failed && (
        <p role="alert" className="absolute bottom-16 left-1/2 -translate-x-1/2 rounded bg-red-50 px-3 py-1 text-xs text-red-800 shadow">
          Some map data could not be loaded. The other layers are unaffected.
        </p>
      )}
    </div>
  );
}

function popup(map: maplibregl.Map, e: maplibregl.MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }) {
  const f = e.features?.[0];
  if (!f) return;
  const p = f.properties as { state: "rain" | "zero" | "nodata"; accumulation_mm: number | null };
  const box = document.createElement("div");
  const strong = document.createElement("strong");
  strong.textContent = f.layer.id.includes("recent_late") ? "Recent Late Run cell" : "Historical Final Run cell";
  const line = document.createElement("div");
  line.textContent = describeCell(p.state, p.accumulation_mm === null || p.accumulation_mm === undefined ? null : Number(p.accumulation_mm));
  const note = document.createElement("div");
  note.textContent = "~10 km satellite grid cell. Rainfall accumulation, not flood depth.";
  note.style.fontSize = "11px";
  box.append(strong, line, note);
  new maplibregl.Popup({ closeButton: true, maxWidth: "240px" }).setLngLat(e.lngLat).setDOMContent(box).addTo(map);
}
