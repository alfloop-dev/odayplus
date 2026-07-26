// Types for the R4 Network SiteScore scoring surface served by
// /api/v1/operator/network-scoring. They mirror NetworkScoringService's JSON
// so CandidatePanel / SiteScorePanel / ComparePanel can render the data-gate,
// scorecards, and Compare recommendation without re-deriving them client-side.

export type ScoreRecommendation = "GO" | "WAIT" | "REJECT";

export type ScoringGateCheck = {
  key: string;
  label: string;
  state: "ok" | "warn" | "fail";
  note: string;
};

export type ScoringGate = {
  state: string;
  passed: boolean;
  missing: string[];
  otherMissing: string[];
  blockNote: string;
  checks: ScoringGateCheck[];
  okCount: number;
  totalCount: number;
};

export type ScoringCandidate = {
  id: string;
  listingId?: string | null;
  heatZoneId: string;
  title: string;
  zoneLabel: string;
  address: string;
  district?: string;
  modelVersion: string;
  datasetSnapshotId: string;
  stage: string;
  gate: ScoringGate;
  scored: boolean;
  score: number | null;
  recommendation: ScoreRecommendation | null;
  reviewId?: string | null;
  inCompare: boolean;
};

export type ScoreCard = {
  id: string;
  title: string;
  zoneLabel: string;
  heatZoneId: string;
  score: number;
  recommendation: ScoreRecommendation;
  modelVersion: string;
  datasetSnapshotId: string;
  generatedAt: string;
  confidence: string;
  payback: string;
  revenuePath: { m1: number; m3: number; m6: number; m12: number };
  band: { p10: string; p50: string; p90: string };
  subScores: {
    rentReasonableness?: string;
    cannibalization?: string;
    competition?: string;
    demand?: string;
    poiFit?: string;
    access?: string;
  };
  capex: string;
  rentAssumption: string;
  drivers: string[];
  reasons: string[];
  risks: string[];
  conditions: string[];
  conditionTitle: string;
  reviewId?: string | null;
};

export type BatchResultRow = {
  rank: number;
  priority: string;
  id: string;
  title: string;
  score: number;
  recommendation: ScoreRecommendation;
  m12P50: string;
  payback: string;
  cannibalization: string;
  inCompare: boolean;
};

export type CompareColumn = {
  id: string;
  title: string;
  priority: string;
  recommendation: ScoreRecommendation;
  score: number;
  isBest: boolean;
};

export type CompareMetric = {
  key: string;
  label: string;
  values: Array<{ id: string; text: string; isBest: boolean }>;
};

export type CompareRecommendationCard = {
  id: string;
  title: string;
  recommendation: ScoreRecommendation;
  score: number;
  text: string;
  why?: string[];
};

export type CompareRecommendation = {
  primary: CompareRecommendationCard;
  alternate: CompareRecommendationCard | null;
  avoid: CompareRecommendationCard | null;
  priorityList: Array<{
    priority: string;
    id: string;
    title: string;
    score: number;
    recommendation: ScoreRecommendation;
  }>;
};

export type NetworkScoringCompare = {
  columns: CompareColumn[];
  metrics: CompareMetric[];
  recommendation: CompareRecommendation | null;
  empty: boolean;
};

export type NetworkScoringSnapshot = {
  source?: "api" | "fixture";
  modelVersion: string;
  candidates: ScoringCandidate[];
  scorecards: ScoreCard[];
  batchResults: BatchResultRow[];
  compare: NetworkScoringCompare;
  compareSet: string[];
  counts?: { candidates: number; scored: number; gateBlocked: number };
};

export function recommendationTone(recommendation: ScoreRecommendation | null): "good" | "watch" | "risk" {
  if (recommendation === "GO") return "good";
  if (recommendation === "REJECT") return "risk";
  return "watch";
}
