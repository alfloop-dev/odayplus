import type { IntakeStage, MatchOutcome } from "@oday-plus/openapi-client";
import type { IntakeDecisionKind } from "./intakeTypes";

export * from "./intakeTypes";

export type IntakeUrlState = {
  filters: {
    stage?: IntakeStage;
    matchOutcome?: MatchOutcome;
    sourceId?: string;
    heatZoneId?: string;
  };
  sort?: "submitted_at_desc" | "updated_at_desc" | "due_at_asc" | "status_asc";
  view?: "list" | "map";
  selectedId?: string | null;
  dialog?: "add" | "detail" | "fix" | "decide" | "assignmentSla" | "reopen" | null;
  activeSection?: string | null;
  fixFieldKey?: string | null;
  decisionKind?: IntakeDecisionKind | "transfer" | "pause" | null;
  receiptId?: string | null;
  compareTask?: boolean | null;
};

export type IntakeDetailPresentationFacts = {
  sourceId: string | null;
  originalUrl: string | null;
  canonicalUrl: string | null;
  submitter: string | null;
  owner: string | null;
  submittedAt: string | null;
  updatedAt: string | null;
  scope: Readonly<Record<string, unknown>>;
  policyState: string | null;
  policyReason: string | null;
  policyVersion: string | null;
  policyExpiresAt: string | null;
  etag: string | null;
  version: number | null;
};

export type IntakeDateTimePresentation = {
  absolute: string;
  relative: string;
  timeZone: string;
  text: string;
  title: string;
};

export function formatIntakeDateTime(
  value?: string | null,
  nowMs = Date.now(),
): IntakeDateTimePresentation | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;

  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const absolute = new Intl.DateTimeFormat("zh-TW", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
  }).format(date);
  const deltaSeconds = Math.round((date.getTime() - nowMs) / 1_000);
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["year", 31_536_000],
    ["month", 2_592_000],
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
    ["second", 1],
  ];
  const [unit, divisor] =
    units.find(([, seconds]) => Math.abs(deltaSeconds) >= seconds) ??
    units[units.length - 1];
  const relative = new Intl.RelativeTimeFormat("zh-TW", {
    numeric: "auto",
  }).format(Math.round(deltaSeconds / divisor), unit);

  return {
    absolute,
    relative,
    timeZone,
    text: `${relative} · ${absolute}`,
    title: `${date.toISOString()} · ${timeZone}`,
  };
}
