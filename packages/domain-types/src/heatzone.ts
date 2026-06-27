import type { Confidence, DataQuality } from "./common.ts";

export type HeatZoneState =
  | "UNTOUCHED"
  | "PARTIALLY_ABSORBED"
  | "SATURATED"
  | "UNDER_REALIZED"
  | "STILL_EXPANDABLE"
  | "SUPPRESSED_LOW_CONFIDENCE";

export type HeatZoneScore = {
  heat_zone_id: string;
  h3_index: string;
  h3_resolution: number;
  score: number;
  priority_rank: number;
  unmet_demand_score: number;
  format_fit_score: number;
  cannibalization_risk_score: number;
  rent_feasibility_score: number;
  listing_availability_score: number;
  confidence: number;
  state: HeatZoneState;
  feature_snapshot_time: string;
  prediction_origin_time: string;
  last_scored_at: string;
  model_version: string;
  feature_version: string;
  source_snapshot_ids: string[];
  reasons: string[];
  warnings: string[];
  admin_city?: string;
  admin_district?: string;
};

export type HeatZoneMapProperties = {
  heat_zone_id: string;
  h3_index: string;
  score: number;
  priority_rank: number;
  unmet_demand_score: number;
  format_fit_score: number;
  cannibalization_risk: number;
  rent_feasibility: number;
  listing_availability: number;
  confidence: number;
  status: HeatZoneState;
  last_scored_at: string;
  model_version: string;
  feature_version: string;
  admin_city?: string;
  admin_district?: string;
  warnings: string[];
};

export type HeatZoneMapFeature = {
  type: "Feature";
  id: string;
  geometry: null | {
    type: "Polygon" | "MultiPolygon";
    coordinates: number[][][] | number[][][][];
  };
  properties: HeatZoneMapProperties;
};

export type HeatZoneMapFeatureCollection = {
  type: "FeatureCollection";
  features: HeatZoneMapFeature[];
  count: number;
  data_quality?: DataQuality;
};

export type HeatZoneListResponse = {
  items: HeatZoneScore[];
  count: number;
  confidence_summary?: Confidence;
};

export type HeatZoneScoreJobRequest = {
  features: Record<string, unknown>[];
  prediction_origin_time?: string;
  idempotency_key?: string;
};

export type HeatZoneScoreJobResponse = {
  job_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "partial";
  scores: HeatZoneScore[];
  map_features: HeatZoneMapFeature[];
  completed_at: string;
  warnings: string[];
  created?: boolean;
  audit_event_id?: string;
  correlation_id?: string;
};

