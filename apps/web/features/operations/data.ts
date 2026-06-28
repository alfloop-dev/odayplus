export type AlertLevel = "green" | "yellow" | "orange" | "red";
export type TrajectoryClass = "growing" | "ramping" | "plateau" | "declining";
export type OperationsView = "overview" | "forecast" | "alerts" | "storeDetail";

export type ForecastBand = {
  horizon: "w4" | "w8" | "w12" | "w24";
  p10: number;
  p50: number;
  p90: number;
  actual: number;
};

export type StoreForecast = {
  storeId: string;
  storeName: string;
  district: string;
  light: AlertLevel;
  gapRatio: number;
  trajectory: TrajectoryClass;
  turningPointProbability: number;
  actualRevenue: number;
  baselineP50: number;
  modelVersion: string;
  featureVersion: string;
  policyVersion: string;
  predictionOriginTime: string;
  scoredAt: string;
  sourceSnapshotIds: string[];
  freshness: "FRESH" | "STALE" | "LOW_CONFIDENCE";
  forecastVersion: number;
  bands: ForecastBand[];
  alertId?: string;
  alertStatus?: "open" | "closed";
  handoffId?: string;
  interventionType?: "maintenance" | "promotion";
  eligibilityStatus?: "manual_review" | "eligible";
  recommendedActions: string[];
  positiveSignals: string[];
  negativeSignals: string[];
  qualityWarning?: string;
  correlationId: string;
  predictionRunId: string;
};

export type ForecastAlert = {
  alertId: string;
  storeId: string;
  light: AlertLevel;
  reasonCode: "sitescore_gap" | "within_expected_band";
  evidenceSummary: string;
  openedAt: string;
  waitTime: string;
  status: "open" | "closed";
  closedAt?: string;
  handoffId?: string;
  interventionType?: "maintenance" | "promotion";
  eligibilityStatus?: "manual_review" | "eligible";
  correlationId: string;
};

export const jobStatus = {
  status: "SUCCEEDED",
  dataStatus: "FRESH",
  updatedAt: "2026-06-28T02:40:00Z",
  sourceSnapshot: "snap-forecastops-20260628-0200",
};

export const stores: StoreForecast[] = [
  {
    storeId: "store-001",
    storeName: "台北信義旗艦店",
    district: "台北市信義區",
    light: "red",
    gapRatio: -0.42,
    trajectory: "declining",
    turningPointProbability: 0.78,
    actualRevenue: 69600,
    baselineP50: 120000,
    modelVersion: "forecastops-r3-20260627",
    featureVersion: "store-machine-timeseries-view-v1",
    policyVersion: "four-light-policy-v1",
    predictionOriginTime: "2026-06-27T09:00:00Z",
    scoredAt: "2026-06-28T02:37:00Z",
    sourceSnapshotIds: ["pos-20260627", "machine-20260627", "sitescore-ssr-7001"],
    freshness: "FRESH",
    forecastVersion: 2,
    alertId: "alert-red-1001",
    alertStatus: "open",
    handoffId: "handoff-9001",
    interventionType: "maintenance",
    eligibilityStatus: "manual_review",
    recommendedActions: ["inspect_machine_uptime", "review_staffing", "open_recovery_plan"],
    positiveSignals: ["SiteScore baseline remains stable", "Nearby lunch traffic is within expected band"],
    negativeSignals: ["Actual revenue is 42% below baseline", "Machine cycles dropped 31% week over week"],
    qualityWarning: "Partial machine telemetry: 2 of 24 hourly slices arrived late.",
    correlationId: "corr-forecast-red-1001",
    predictionRunId: "pred-run-20260628-001",
    bands: [
      { horizon: "w4", p10: 64000, p50: 72000, p90: 85000, actual: 69600 },
      { horizon: "w8", p10: 61000, p50: 70000, p90: 83000, actual: 69600 },
      { horizon: "w12", p10: 59000, p50: 68000, p90: 82000, actual: 69600 },
      { horizon: "w24", p10: 55000, p50: 66000, p90: 80000, actual: 69600 },
    ],
  },
  {
    storeId: "store-002",
    storeName: "新北板橋店",
    district: "新北市板橋區",
    light: "orange",
    gapRatio: -0.26,
    trajectory: "plateau",
    turningPointProbability: 0.54,
    actualRevenue: 88800,
    baselineP50: 120000,
    modelVersion: "forecastops-r3-20260627",
    featureVersion: "store-machine-timeseries-view-v1",
    policyVersion: "four-light-policy-v1",
    predictionOriginTime: "2026-06-27T09:00:00Z",
    scoredAt: "2026-06-28T02:38:00Z",
    sourceSnapshotIds: ["pos-20260627", "promo-20260627"],
    freshness: "FRESH",
    forecastVersion: 1,
    alertId: "alert-orange-2001",
    alertStatus: "open",
    handoffId: "handoff-9002",
    interventionType: "promotion",
    eligibilityStatus: "eligible",
    recommendedActions: [
      "launch_local_promotion",
      "review_price_packaging",
      "review_local_demand",
      "create_intervention_candidate",
    ],
    positiveSignals: ["Weekend basket size held at baseline", "Staffing coverage is normal"],
    negativeSignals: ["Actual revenue is 26% below SiteScore P50", "Evening demand softened in local POS mix"],
    correlationId: "corr-forecast-orange-2001",
    predictionRunId: "pred-run-20260628-002",
    bands: [
      { horizon: "w4", p10: 83000, p50: 91000, p90: 102000, actual: 88800 },
      { horizon: "w8", p10: 82000, p50: 90000, p90: 101000, actual: 88800 },
      { horizon: "w12", p10: 81000, p50: 89500, p90: 100000, actual: 88800 },
      { horizon: "w24", p10: 80500, p50: 89000, p90: 99500, actual: 88800 },
    ],
  },
  {
    storeId: "store-003",
    storeName: "桃園中壢店",
    district: "桃園市中壢區",
    light: "yellow",
    gapRatio: -0.14,
    trajectory: "ramping",
    turningPointProbability: 0.34,
    actualRevenue: 103200,
    baselineP50: 120000,
    modelVersion: "forecastops-r3-20260627",
    featureVersion: "store-machine-timeseries-view-v1",
    policyVersion: "four-light-policy-v1",
    predictionOriginTime: "2026-06-27T09:00:00Z",
    scoredAt: "2026-06-28T02:39:00Z",
    sourceSnapshotIds: ["pos-20260627"],
    freshness: "STALE",
    forecastVersion: 1,
    alertId: "alert-yellow-3001",
    alertStatus: "open",
    recommendedActions: ["review_local_demand", "create_data_quality_check"],
    positiveSignals: ["Traffic recovery is visible in P90 band", "No hard operations blocker detected"],
    negativeSignals: ["Actual revenue is 14% below baseline", "Feature snapshot is older than SLA"],
    correlationId: "corr-forecast-yellow-3001",
    predictionRunId: "pred-run-20260628-003",
    bands: [
      { horizon: "w4", p10: 98000, p50: 106000, p90: 117000, actual: 103200 },
      { horizon: "w8", p10: 100000, p50: 109000, p90: 121000, actual: 103200 },
      { horizon: "w12", p10: 101000, p50: 111000, p90: 124000, actual: 103200 },
      { horizon: "w24", p10: 103000, p50: 114000, p90: 127000, actual: 103200 },
    ],
  },
  {
    storeId: "store-004",
    storeName: "台中公益店",
    district: "台中市西區",
    light: "green",
    gapRatio: -0.04,
    trajectory: "growing",
    turningPointProbability: 0.18,
    actualRevenue: 115200,
    baselineP50: 120000,
    modelVersion: "forecastops-r3-20260627",
    featureVersion: "store-machine-timeseries-view-v1",
    policyVersion: "four-light-policy-v1",
    predictionOriginTime: "2026-06-27T09:00:00Z",
    scoredAt: "2026-06-28T02:40:00Z",
    sourceSnapshotIds: ["pos-20260627", "sitescore-ssr-7004"],
    freshness: "FRESH",
    forecastVersion: 3,
    recommendedActions: ["continue_monitoring"],
    positiveSignals: ["Actual revenue remains inside expected band", "Turning point probability is low"],
    negativeSignals: ["No open operational alert"],
    correlationId: "corr-forecast-green-4001",
    predictionRunId: "pred-run-20260628-004",
    bands: [
      { horizon: "w4", p10: 110000, p50: 117000, p90: 128000, actual: 115200 },
      { horizon: "w8", p10: 112000, p50: 119000, p90: 131000, actual: 115200 },
      { horizon: "w12", p10: 114000, p50: 122000, p90: 134000, actual: 115200 },
      { horizon: "w24", p10: 116000, p50: 126000, p90: 139000, actual: 115200 },
    ],
  },
];

export const alerts: ForecastAlert[] = stores
  .filter((store) => store.alertId)
  .map((store, index) => ({
    alertId: store.alertId as string,
    storeId: store.storeId,
    light: store.light,
    reasonCode: "sitescore_gap",
    evidenceSummary: `actual ${formatMoney(store.actualRevenue)} vs forecast_p50 ${formatMoney(store.bands[0].p50)}; gap ${formatPercent(store.gapRatio)}; trajectory ${store.trajectory}`,
    openedAt: `2026-06-28T0${index}:12:00Z`,
    waitTime: index === 0 ? "2h 28m" : index === 1 ? "1h 42m" : "54m",
    status: store.alertStatus ?? "open",
    handoffId: store.handoffId,
    interventionType: store.interventionType,
    eligibilityStatus: store.eligibilityStatus,
    correlationId: store.correlationId,
  }));

export function selectedStore(storeId?: string) {
  return stores.find((store) => store.storeId === storeId) ?? stores[0];
}

export function formatMoney(value: number) {
  return `$${Math.round(value).toLocaleString("en-US")}`;
}

export function formatPercent(value: number) {
  return `${Math.round(value * 100)}%`;
}
