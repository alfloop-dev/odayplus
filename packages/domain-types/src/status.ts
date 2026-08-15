/**
 * Canonical state vocabularies — frozen strings shared across the platform.
 *
 * Source of truth: docs/design/ODAY_PLUS_VISUAL_DESIGN_SYSTEM.md §6.3 and
 * ODAY_PLUS_COMPONENT_CONTRACTS.md §2. These codes stay in English even in the
 * zh-TW UI (visual system §8.3); workers MUST NOT invent new state names.
 */

export type JobStatus =
  | "QUEUED"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED"
  | "PARTIAL"
  | "RETRYING"
  | "EXPIRED";

export type DecisionStatus =
  | "DRAFT"
  | "SYSTEM_RECOMMENDED"
  | "PENDING_REVIEW"
  | "APPROVED"
  | "REJECTED"
  | "OVERRIDDEN"
  | "EXECUTED"
  | "OBSERVING"
  | "OUTCOME_READY"
  | "CLOSED";

export type DataStatus =
  | "FRESH"
  | "STALE"
  | "PARTIAL"
  | "MISSING"
  | "LOW_CONFIDENCE"
  | "FAILED_QA"
  | "BLOCKED";

export type ModelStatus =
  | "EXPERIMENTAL"
  | "CANDIDATE"
  | "CHALLENGER"
  | "CHAMPION"
  | "SHADOW"
  | "CANARY"
  | "PRODUCTION"
  | "DEPRECATED"
  | "ROLLED_BACK"
  | "BLOCKED";

export type FourLight = "GREEN" | "YELLOW" | "ORANGE" | "RED";

export type RiskLevel = "low" | "medium" | "high" | "critical";

/** The seven status-colour semantics (visual system §6.1). */
export type StatusTone =
  | "green"
  | "yellow"
  | "orange"
  | "red"
  | "gray"
  | "blue"
  | "purple";

/** Runtime tuples for iteration / validation (kept in sync with the unions above). */
export const JOB_STATUSES: readonly JobStatus[] = [
  "QUEUED",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
  "PARTIAL",
  "RETRYING",
  "EXPIRED",
];

export const DATA_STATUSES: readonly DataStatus[] = [
  "FRESH",
  "STALE",
  "PARTIAL",
  "MISSING",
  "LOW_CONFIDENCE",
  "FAILED_QA",
  "BLOCKED",
];

export const FOUR_LIGHTS: readonly FourLight[] = [
  "GREEN",
  "YELLOW",
  "ORANGE",
  "RED",
];

/** Map a DataStatus to its display tone (component contracts §4.13). */
export const dataStatusTone: Record<DataStatus, StatusTone> = {
  FRESH: "green",
  STALE: "yellow",
  PARTIAL: "orange",
  LOW_CONFIDENCE: "orange",
  FAILED_QA: "red",
  MISSING: "gray",
  BLOCKED: "red",
};

/** Map a FourLight to its tone (domain component contracts §5.5). */
export const fourLightTone: Record<FourLight, StatusTone> = {
  GREEN: "green",
  YELLOW: "yellow",
  ORANGE: "orange",
  RED: "red",
};
