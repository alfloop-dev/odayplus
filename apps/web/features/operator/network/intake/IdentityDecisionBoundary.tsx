"use client";

/**
 * Production integration boundary for ODP-INTAKE-FCL-IDENTITY-001.
 *
 * Shell/Integration code supplies an authoritative comparison, graph plans,
 * workflow, conflicts and command callback. This boundary never fetches
 * legacy fixture data and never constructs entity IDs, graph plans or
 * receipts.
 */
export { IdentityDecisionPanel as IdentityDecisionBoundary } from "./IdentityDecisionPanel";
export type {
  IdentityActor,
  IdentityComparableValue,
  IdentityComparisonContract,
  IdentityComparisonField,
  IdentityComparisonFieldKey,
  IdentityComparisonState,
  IdentityConflict,
  IdentityDecisionCommand,
  IdentityDecisionDraft,
  IdentityDecisionReceipt,
  IdentityDecisionStatus,
  IdentityGraphEdge,
  IdentityGraphNode,
  IdentityGraphOperation,
  IdentityGraphPlan,
  IdentityGraphSnapshot,
  IdentityOutcomeAction,
  IdentityReviewWorkflow,
  IdentitySignal,
} from "./identityTypes";
