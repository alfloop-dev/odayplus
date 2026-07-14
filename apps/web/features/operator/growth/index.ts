/**
 * Growth workspace feature sub-module barrel.
 *
 * Re-exports the public surface of the Growth workspace components
 * and view model so that consumers import from this directory rather
 * than from individual files.
 *
 * Owned by: ODP-OC-R4-004
 * Composes with: apps/web/features/operator/GrowthWorkspace.tsx,
 *                apps/web/features/operator/growthViewModel.ts
 */

export {
  GrowthWorkspace,
} from "../GrowthWorkspace.tsx";

export type {
  GrowthApiData,
  GrowthFreshness,
  GrowthItem,
  GrowthOutcome,
  GrowthSegment,
  GrowthViewModel,
  PriceOpsRecommendation,
  CloseoutGate,
  CloseoutRequiredAction,
  ConfidenceLevel,
} from "../growthViewModel.ts";

export {
  buildGrowthViewModel,
  fetchGrowthApiData,
  createGrowthDraft,
  writeGrowthOutcome,
  growthApiClient,
  closeoutGate,
  judgeEffectiveness,
  formatLift,
  outcomeLabel,
  outcomeTone,
  constraintTone,
  trendLabel,
  trendTone,
  confidenceTone,
  SEGMENTS,
  PRICEOPS_RECOMMENDATIONS,
  GROWTH_ITEMS,
  FIXTURE_FRESHNESS,
  freshness,
} from "../growthViewModel.ts";
