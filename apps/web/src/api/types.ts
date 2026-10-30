// Mirrors the API contracts: schemas/v1 (1.0.0, frozen) and the additive rainfall schemas (1.1.0).
// Observed rainfall is satellite-estimated rainfall accumulation, never flood depth.

export type RainfallStream = "historical_final" | "recent_late";
export type CellState = "rain" | "zero" | "nodata";
export type ScenarioType = "terrain_baseline" | "hypothetical_rainfall_accumulation" | "observed_rainfall";

export interface Bbox { west: number; south: number; east: number; north: number }

export interface ApiErrorBody { code: string; message: string; details?: Record<string, unknown> | null; request_id?: string | null }

export interface Freshness {
  status: "historical_archive" | "recent_provisional" | "aged_provisional";
  as_of_utc: string;
  observation_end: string;
  age_days: number;
  is_live: false;
  note: string;
}

export interface SourceInfo { provider: string | null; source_url: string | null; attribution: string | null; license: string | null }

export interface ObservedRainfallMetadata {
  schema_version: "1.1.0";
  scenario_id: string;
  scenario_type: "observed_rainfall";
  region_id: string;
  label: string;
  rainfall_stream: RainfallStream;
  run_type: "final" | "late";
  product_identifier: string;
  product_version: string;
  product_versions: string[];
  observation_start: string;
  observation_end: string;
  observation_start_utc: string;
  observation_end_utc: string;
  n_days: number;
  retrieval_timestamp: string | null;
  native_resolution: string;
  native_resolution_deg: number;
  units: string;
  spatial_coverage: { bbox: Bbox; description: string; cells_x: number; cells_y: number };
  valid_cell_count: number;
  missing_cell_count: number;
  missing_percentage: number;
  source: SourceInfo;
  provenance_reference: { derived_dataset_id: string; raw_dataset_id: string; raw_verification: "verified" | "partial" | "not_present" };
  processing_status: "published";
  data_freshness: Freshness;
  raster: { kind: "geotiff"; width: number; height: number; cell_size_deg: number; nodata: number | null; sha256: string; href: string };
  in_exposure_index: false;
  is_forecast: false;
  limitations: string[];
  disclaimer: string;
}

export interface StreamAvailability {
  stream: RainfallStream;
  label: string;
  product_identifier: string;
  status: "available" | "pending" | "unavailable";
  scenario_ids: string[];
  note: string;
}

export interface RainfallCatalog {
  region_id: string;
  streams: StreamAvailability[];
  datasets: ObservedRainfallMetadata[];
  filters: Record<string, string | null>;
}

export interface AccumulationStats {
  min_mm: number | null; mean_mm: number | null; median_mm: number | null; max_mm: number | null;
  valid_cell_count: number; missing_cell_count: number; missing_percentage: number;
}

export interface DailyRainfall {
  date: string; product_version: string;
  min_mm: number | null; mean_mm: number | null; median_mm: number | null; max_mm: number | null;
  aoi_missing_cells: number | null; global_missing_percent: number | null;
}

export interface RainfallSummary {
  scenario_id: string;
  rainfall_stream: RainfallStream;
  observation_start: string;
  observation_end: string;
  n_days: number;
  accumulation_mm: AccumulationStats;
  mean_daily_rate_mm_per_day: number | null;
  daily: DailyRainfall[];
  statistic_scope: string;
  coverage_caveat: string;
}

export interface RainfallProvenance {
  scenario_id: string;
  rainfall_stream: RainfallStream;
  product_identifier: string;
  product_versions: string[];
  derived_dataset_id: string;
  raw_dataset_id: string;
  raw_verification: "verified" | "partial" | "not_present";
  processed_sha256: string;
  retrieval_timestamp: string | null;
  source: SourceInfo;
  granules: { file: string; date: string; product_version: string }[];
  processing: string[];
  limitations: string[];
  warnings: string[];
}

export interface CellFeature {
  type: "Feature";
  geometry: { type: "Polygon"; coordinates: number[][][] };
  properties: { row: number; col: number; state: CellState; accumulation_mm: number | null };
}
export interface RainfallCells {
  type: "FeatureCollection";
  scenario_id: string;
  rainfall_stream: RainfallStream;
  native_resolution_deg: number;
  units: string;
  resampled: false;
  features: CellFeature[];
}

export interface ComparedDataset {
  scenario_id: string; rainfall_stream: RainfallStream; label: string;
  observation_start: string; observation_end: string; n_days: number; product_version: string;
  mean_accumulation_mm: number | null; max_accumulation_mm: number | null; mean_daily_rate_mm_per_day: number | null;
}
export interface RainfallComparison {
  region_id: string;
  observed: ComparedDataset[];
  hypothetical_reference: { scenario_id: string; rainfall_accumulation_mm: number; accumulation_period: string | null }[];
  pairwise: null | {
    a: string; b: string; same_grid: boolean; same_duration: boolean;
    mean_difference_mm: number | null; mean_daily_rate_difference_mm_per_day: number | null;
    cells: { row: number; col: number; a_mm: number | null; b_mm: number | null; b_minus_a_mm: number | null }[];
    caution: string;
  };
  exposure_note: string;
  caution: string;
}

export interface ScenarioCatalogEntry {
  scenario_id: string;
  scenario_type: ScenarioType;
  group: "terrain_baseline" | "hypothetical_rainfall" | "historical_observed" | "recent_observed";
  label: string;
  has_exposure_outputs: boolean;
  rainfall_accumulation_mm?: number | null;
}
export interface ScenarioCatalog { region_id: string; scenarios: ScenarioCatalogEntry[]; note: string }

export interface RegionInfo { region_id: string; name: string; bbox: Bbox; boundaries: string[]; scenarios: string[] }
export interface RegionList { regions: RegionInfo[] }

export interface PolygonCollection {
  features: { type: "Feature"; geometry: GeoJSON.Geometry; properties: { exposure_class: number; label: string; area_km2: number } }[];
  matched?: number | null; truncated: boolean;
}
export interface RoadCollection {
  features: { type: "Feature"; geometry: GeoJSON.Geometry; properties: Record<string, unknown> }[];
  matched?: number | null; truncated: boolean; detail?: "full" | "overview" | null;
}
export interface WaterCollection {
  features: { type: "Feature"; geometry: GeoJSON.Geometry;
    properties: { osm_id: number; name: string | null; feature_type: "water_body" | "waterway"; water_class: string } }[];
  matched: number; returned: number; truncated: boolean; next_offset: number | null;
  source: { license: string; attribution: string; dataset_id: string };
}
export interface FacilityCollection {
  features: { type: "Feature"; geometry: GeoJSON.Geometry; properties: { facility_type: string; name: string | null; exp_class: number } }[];
  matched?: number | null; truncated: boolean;
}
