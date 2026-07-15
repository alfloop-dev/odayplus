// Assisted listing intake (ODP-OC-R5-011) — view contract for the five
// Package 7 surfaces:
//   Network URL 收件佇列 / Dialog 從網址新增物件 / Dialog 收件處理詳情 /
//   Dialog 欄位修正 / Dialog 收件決策確認
//
// Owned layer  : FE view-model types + label/tone derivation for the intake UI.
// Not changing : the wire contract itself — stage/policy/outcome unions and
//                their zh-TW labels are imported from @oday-plus/openapi-client
//                (which mirrors assisted_intake.py) rather than re-typed here.
// Composes with: AssistedIntakeQueuePanel and the four intake dialogs.

import {
  INTAKE_IDENTITY_FIELDS,
  INTAKE_STAGE_LABEL,
  MATCH_OUTCOME_LABEL,
  SOURCE_POLICY_LABEL,
  type AssistedIntake,
  type IntakeDecideAction,
  type IntakeStage,
  type MatchOutcome,
  type SourcePolicyState,
} from "@oday-plus/openapi-client";

export type IntakeTone = "good" | "watch" | "risk" | "info" | "neutral";

/**
 * Which dialog the queue has open. The detail dialog is the deep-link target
 * (`#intake/<id>`); fix/decide stack above it and return to it on close.
 */
export type IntakeDialogKind = "add" | "detail" | "fix" | "decide";

/** Design decision verbs (Package 7) mapped onto the API's decide actions. */
export type IntakeDecisionKind = "create" | "revise" | "dup" | "steward";

/**
 * The console's decision verbs are not 1:1 with the service's action names:
 * the design's 標記重複 is the service's `duplicate`, and 送交資料管理員 /
 * 送交治理覆核 both stop processing and route the record for source-quality
 * judgement, which the service models as `quarantine`.
 */
export const DECISION_API_ACTION: Record<IntakeDecisionKind, IntakeDecideAction> = {
  create: "create",
  revise: "revise",
  dup: "duplicate",
  steward: "quarantine",
};

export const IDENTITY_FIELD_KEYS: readonly string[] = INTAKE_IDENTITY_FIELDS;

/** Stages that mean the server is still working — no decision is offered. */
export const IN_FLIGHT_STAGES: readonly IntakeStage[] = [
  "SUBMITTED",
  "CHECKING_IDENTITY",
  "CHECKING_SOURCE_POLICY",
  "RETRIEVING",
  "PARSING",
  "MATCHING",
];

export function stageLabel(stage: IntakeStage): string {
  return INTAKE_STAGE_LABEL[stage] ?? stage;
}

export function policyLabel(policy: SourcePolicyState): string {
  return SOURCE_POLICY_LABEL[policy] ?? policy;
}

export function matchLabel(outcome: MatchOutcome): string {
  return MATCH_OUTCOME_LABEL[outcome] ?? outcome;
}

export function stageTone(stage: IntakeStage): IntakeTone {
  if (stage === "READY") return "good";
  if (stage === "QUARANTINED" || stage === "FAILED") return "risk";
  if (stage === "NEEDS_REVIEW") return "watch";
  if (stage === "AWAITING_ASSISTED_ENTRY") return "info";
  return "neutral";
}

export function matchTone(outcome: MatchOutcome): IntakeTone {
  if (outcome === "NEW") return "good";
  if (outcome === "QUARANTINED") return "risk";
  if (outcome === "POSSIBLE_MATCH") return "watch";
  if (outcome === "REVISION") return "info";
  return "neutral";
}

export function policyTone(policy: SourcePolicyState): IntakeTone {
  if (policy === "APPROVED_RETRIEVAL") return "good";
  if (policy === "SOURCE_BLOCKED" || policy === "POLICY_UNKNOWN") return "risk";
  if (policy === "AUTH_REQUIRED") return "watch";
  return "info";
}

/**
 * The real stage path taken, so the stepper shows actual stages rather than a
 * fabricated percentage (design requirement §5.2). A record that never gets
 * retrieved must not display RETRIEVING/PARSING as skipped steps.
 */
export function stagePath(record: AssistedIntake): IntakeStage[] {
  const head: IntakeStage[] = ["SUBMITTED", "CHECKING_IDENTITY", "CHECKING_SOURCE_POLICY"];
  const { policy, stage } = record;

  if (policy === "ASSISTED_ENTRY_ONLY" || policy === "AUTH_REQUIRED") {
    const path: IntakeStage[] = [...head, "AWAITING_ASSISTED_ENTRY"];
    // Assisted entry rejoins the pipeline at MATCHING once fields are supplied.
    if (stage !== "AWAITING_ASSISTED_ENTRY") path.push("MATCHING");
    return appendTerminal(path, stage);
  }
  if (policy === "SOURCE_BLOCKED" || policy === "POLICY_UNKNOWN") {
    return appendTerminal(head, stage);
  }
  return appendTerminal([...head, "RETRIEVING", "PARSING", "MATCHING"], stage);
}

function appendTerminal(path: IntakeStage[], stage: IntakeStage): IntakeStage[] {
  const terminal: IntakeStage[] = ["NEEDS_REVIEW", "READY", "QUARANTINED", "FAILED"];
  if (terminal.includes(stage)) return [...path, stage];
  return path.includes(stage) ? path : [...path, stage];
}

export type IntakeStepView = {
  code: IntakeStage;
  label: string;
  index: number;
  state: "done" | "current" | "upcoming" | "failed";
  /** Text/glyph marker so stage state never depends on colour alone (§9). */
  mark: string;
};

export function stageSteps(record: AssistedIntake): IntakeStepView[] {
  const path = stagePath(record);
  const found = path.indexOf(record.stage);
  const currentIndex = found === -1 ? path.length - 1 : found;
  const bad = record.stage === "QUARANTINED" || record.stage === "FAILED";

  return path.map((code, index) => {
    const isCurrent = index === currentIndex;
    const isFailed = isCurrent && bad;
    const isDone =
      index < currentIndex || (isCurrent && (record.stage === "READY" || record.stage === "NEEDS_REVIEW"));
    const state: IntakeStepView["state"] = isFailed
      ? "failed"
      : isDone
        ? "done"
        : isCurrent
          ? "current"
          : "upcoming";
    return {
      code,
      label: stageLabel(code),
      index,
      state,
      mark: isFailed ? "✕" : isDone && !isCurrent ? "✓" : String(index + 1),
    };
  });
}

/** Row action verb ladder — design §3.3. First match wins. */
export function rowActionLabel(record: AssistedIntake): string {
  if (record.stage === "NEEDS_REVIEW") return "覆核";
  if (record.stage === "AWAITING_ASSISTED_ENTRY") return "補錄";
  if (record.stage === "FAILED") return "重試";
  if (record.stage === "QUARANTINED") return "查看原因";
  if (record.stage === "READY") return "確認";
  return "處理中…";
}

export type IntakeQueueCounts = {
  needsReview: number;
  awaitingEntry: number;
  processing: number;
  blocked: number;
};

export function queueCounts(records: AssistedIntake[]): IntakeQueueCounts {
  return {
    needsReview: records.filter((r) => r.stage === "NEEDS_REVIEW").length,
    awaitingEntry: records.filter((r) => r.stage === "AWAITING_ASSISTED_ENTRY").length,
    processing: records.filter((r) => IN_FLIGHT_STAGES.includes(r.stage)).length,
    blocked: records.filter((r) => r.stage === "QUARANTINED" || r.stage === "FAILED").length,
  };
}

/** Trim a canonical URL for the fixed-width queue cell without losing identity. */
export function shortUrl(url: string, max = 34): string {
  const stripped = url.replace(/^https?:\/\/(www\.)?/, "");
  return stripped.length > max ? `${stripped.slice(0, max)}…` : stripped;
}

export function isIdentityField(key: string): boolean {
  return IDENTITY_FIELD_KEYS.includes(key);
}

/**
 * Reason requirements, mirroring the server so the dialog blocks before the
 * request rather than surfacing a 422. `decide` always requires a reason
 * server-side, so the UI never treats it as optional.
 */
export function correctionNeedsReason(fieldKeys: string[]): boolean {
  return fieldKeys.some(isIdentityField);
}

export type IntakeDecisionOption = {
  kind: IntakeDecisionKind;
  label: string;
  primary: boolean;
};

/** Available decisions per match outcome / stage — design §5.11 action matrix. */
export function decisionOptions(record: AssistedIntake): IntakeDecisionOption[] {
  const target = record.matchResult?.targetListingId ?? "";
  const outcome = record.matchResult?.outcome;

  if (record.stage === "FAILED") {
    return [{ kind: "steward", label: "送交資料管理員", primary: false }];
  }
  if (record.stage === "QUARANTINED") {
    return [{ kind: "steward", label: "送交治理覆核", primary: true }];
  }
  if (outcome === "POSSIBLE_MATCH") {
    return [
      { kind: "create", label: "建立新物件", primary: true },
      { kind: "revise", label: target ? `加入既有物件版本（${target}）` : "加入既有物件版本", primary: false },
      { kind: "dup", label: "標記重複", primary: false },
      { kind: "steward", label: "送交資料管理員", primary: false },
    ];
  }
  if (outcome === "REVISION") {
    return [
      { kind: "revise", label: target ? `加入既有物件版本（${target} v2）` : "加入既有物件版本", primary: true },
      { kind: "steward", label: "送交資料管理員", primary: false },
    ];
  }
  if (outcome === "EXACT_DUPLICATE") {
    return [{ kind: "dup", label: "標記重複結案", primary: true }];
  }
  if (outcome === "NEW" && record.stage === "READY") {
    return [
      { kind: "create", label: "建立新物件（加入收件匣）", primary: true },
      { kind: "steward", label: "送交資料管理員", primary: false },
    ];
  }
  return [];
}

export const DECISION_TITLE: Record<IntakeDecisionKind, string> = {
  create: "建立新物件",
  revise: "加入既有物件版本",
  dup: "標記重複",
  steward: "送交資料管理員",
};

export function decisionTitle(kind: IntakeDecisionKind, record: AssistedIntake): string {
  if (kind === "steward" && record.stage === "QUARANTINED") return "送交治理覆核";
  return DECISION_TITLE[kind];
}
