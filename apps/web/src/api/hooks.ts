import { useQuery } from "@tanstack/react-query";
import { api } from "./client";

// Published artifacts are immutable between builds, so responses are cached for the session; only
// freshness metadata (clock-dependent) goes stale, and it arrives with the dataset metadata.
const STATIC = { staleTime: Infinity, gcTime: 30 * 60_000, refetchOnWindowFocus: false } as const;
const META = { staleTime: 5 * 60_000, gcTime: 30 * 60_000, refetchOnWindowFocus: false } as const;

export const useRegions = () => useQuery({ queryKey: ["regions"], queryFn: api.regions, ...META });
export const useScenarioCatalog = (region: string) =>
  useQuery({ queryKey: ["scenario-catalog", region], queryFn: () => api.scenarioCatalog(region), ...META });
export const useRainfallCatalog = (region: string) =>
  useQuery({ queryKey: ["rainfall-catalog", region], queryFn: () => api.rainfallCatalog(region), ...META });

export const useRainfallSummary = (region: string, id: string | null) =>
  useQuery({ queryKey: ["rainfall-summary", region, id], queryFn: () => api.rainfallSummary(region, id!), enabled: !!id, ...STATIC });
export const useRainfallProvenance = (region: string, id: string | null) =>
  useQuery({ queryKey: ["rainfall-provenance", region, id], queryFn: () => api.rainfallProvenance(region, id!), enabled: !!id, ...STATIC });
export const useRainfallCells = (region: string, id: string | null) =>
  useQuery({ queryKey: ["rainfall-cells", region, id], queryFn: () => api.rainfallCells(region, id!), enabled: !!id, ...STATIC });
export const useRainfallComparison = (region: string, ids: string[], enabled: boolean) =>
  useQuery({
    queryKey: ["rainfall-comparison", region, [...ids].sort().join(",")],
    queryFn: () => api.rainfallComparison(region, ids), enabled: enabled && ids.length > 0, ...STATIC,
  });

export const usePolygons = (region: string, scenario: string, enabled: boolean) =>
  useQuery({ queryKey: ["polygons", region, scenario], queryFn: () => api.polygons(region, scenario), enabled, ...STATIC });
