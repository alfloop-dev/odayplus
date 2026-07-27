export type HeatZone = {
  id: string;
  district: string;
  h3: string;
  centroid: [number, number];
  h3Resolution: number;
  score: number;
  confidence: number;
  state:
    | "UNTOUCHED"
    | "PARTIALLY_ABSORBED"
    | "SATURATED"
    | "UNDER_REALIZED"
    | "STILL_EXPANDABLE"
    | "SUPPRESSED_LOW_CONFIDENCE";
  rank: number;
  listings: number;
  warnings: string[];
  reasons: string[];
  modelVersion: string;
  featureVersion: string;
  featureSnapshotTime: string;
  predictionOriginTime: string;
  lastScoredAt: string;
  sourceSnapshotIds: string[];
  unmetDemandScore: number;
  formatFitScore: number;
  cannibalizationRisk: number;
  rentFeasibility: number;
  listingAvailability: number;
  poiCount: number;
  competitorCount: number;
  competitorCapacity: number;
  medianListingRent: number;
  existingStoreCount: number;
  dataQualityScore: number;
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
  heatZoneId: string;
  coordinates: [number, number];
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
  coordinates: [number, number];
  heatZoneScore: number;
  rentArea: string;
  geocode: string;
  feasibility: string;
  listingSource: string;
  siteScore: string;
  readiness: "ready" | "blocked";
  disabledReason?: string;
};
