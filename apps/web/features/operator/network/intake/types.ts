import type { IntakeStage, MatchOutcome } from "@oday-plus/openapi-client";
import type { IntakeDecisionKind } from "./intakeTypes";

export * from "./intakeTypes";

export type IntakeUrlState = {
  /** Presentation selects one existing reader; never an authorization grant. */
  intakeKind?: "canonical" | "invalid" | null;
  /** Return-to-inbox context; empty is its first page. A selected detail never discovers the collection. */
  canonicalCursor?: string | null;
  filters: {
    stage?: IntakeStage;
    matchOutcome?: MatchOutcome;
    sourceId?: string;
    heatZoneId?: string;
  };
  sort?: "submitted_at_desc" | "updated_at_desc" | "due_at_asc" | "status_asc";
  view?: "list" | "map";
  selectedId?: string | null;
  dialog?: "add" | "detail" | "fix" | "decide" | "assignmentSla" | null;
  activeSection?: string | null;
  fixFieldKey?: string | null;
  decisionKind?: IntakeDecisionKind | "transfer" | "pause" | null;
  receiptId?: string | null;
  compareTask?: boolean | null;
};
