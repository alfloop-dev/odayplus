export type HeatZone = {
  id: string;
  district: string;
  h3: string;
  centroid: [number, number];
  h3Resolution: number;
  score: number | null;
  confidence: number | null;
  state:
    | "UNTOUCHED"
    | "PARTIALLY_ABSORBED"
    | "SATURATED"
    | "UNDER_REALIZED"
    | "STILL_EXPANDABLE"
    | "SUPPRESSED_LOW_CONFIDENCE"
    | "ABSTAINED"
    | "UNKNOWN";
  rank: number;
  listings: number | null;
  warnings: string[];
  reasons: string[];
  modelVersion: string;
  executionMode?: string;
  featureVersion: string;
  featureSnapshotTime: string;
  predictionOriginTime: string;
  lastScoredAt: string;
  sourceSnapshotIds: string[];
  unmetDemandScore: number | null;
  formatFitScore: number | null;
  cannibalizationRisk: number | null;
  rentFeasibility: number | null;
  listingAvailability: number | null;
  competitorPressureScore?: number | null;
  poiCount: number | null;
  competitorCount: number | null;
  competitorCapacity: number | null;
  medianListingRent: number | null;
  existingStoreCount: number | null;
  dataQualityScore: number | null;
  isMerged?: boolean;
  compositionKind?: "MERGED" | "SPLIT_CHILD" | "ATOMIC";
  memberCellIds?: string[];
  parentZoneId?: string;
  revertedAt?: string;
};

export type Listing = {
  id: string;
  source: string;
  address: string;
  status:
    | "RAW"
    | "PARSED"
    | "GEOCODED"
    | "DUPLICATE"
    | "FAILED_HARD_RULE"
    | "CANDIDATE";
  issue: string;
  rent: string;
  area: string;
  geocode: string;
  duplicate: string;
  heatZoneId: string | null;
  coordinates: [number, number] | null;
  updatedAt: string;
  action: string;
};

export type CandidateSite = {
  id: string;
  address: string;
  status:
    | "new"
    | "screened"
    | "scored"
    | "visited"
    | "rejected"
    | "approved"
    | "opened";
  heatZoneId: string;
  coordinates: [number, number] | null;
  heatZoneScore: number | null;
  rentArea: string;
  geocode: string;
  feasibility: string;
  listingSource: string;
  siteScore: string;
  readiness: "ready" | "blocked";
  disabledReason?: string;
};
