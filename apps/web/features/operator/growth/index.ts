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
  GrowthKind,
  GrowthOutcome,
  GrowthSegment,
  GrowthViewModel,
  PriceOpsRecommendation,
  CloseoutGate,
  CloseoutRequiredAction,
  ConfidenceLevel,
  ConflictCheck,
  ConflictResult,
  GrowthApproval,
  GrowthEntryCard,
  GrowthBuilderForm,
} from "../growthViewModel.ts";

export {
  buildGrowthViewModel,
  fetchGrowthApiData,
  createGrowthDraft,
  writeGrowthOutcome,
  checkGrowthConflicts,
  submitGrowthForApproval,
  resolveGrowthApproval,
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
  conflictLevelTone,
  growthKindLabel,
  SEGMENTS,
  PRICEOPS_RECOMMENDATIONS,
  GROWTH_ITEMS,
  GROWTH_ENTRY_CARDS,
  GROWTH_KIND_PRESETS,
  BUILDER_STEPS,
  FIXTURE_FRESHNESS,
  freshness,
} from "../growthViewModel.ts";
