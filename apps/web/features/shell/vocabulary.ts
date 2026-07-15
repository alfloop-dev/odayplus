/**
 * Shared display vocabulary for the shell (ODP-PGAP-SHELL-001).
 *
 * The API speaks severity/SLA in a stable machine vocabulary; these maps are
 * the single place it becomes operator-facing zh-TW and a status tone. Kept out
 * of the components so Home, Task Center and Notifications cannot drift into
 * labelling the same state differently.
 */
import type { StatusTone } from "@oday-plus/domain-types";
import type { ShellSeverity, ShellSlaState } from "@oday-plus/openapi-client";

export const SEVERITY_TONE: Record<ShellSeverity, StatusTone> = {
  critical: "red",
  warning: "orange",
  info: "blue",
};

export const SEVERITY_LABEL: Record<ShellSeverity, string> = {
  critical: "嚴重",
  warning: "警告",
  info: "資訊",
};

export const SLA_TONE: Record<ShellSlaState, StatusTone> = {
  breached: "red",
  "at-risk": "orange",
  "on-track": "green",
  none: "gray",
};

export const SLA_LABEL: Record<ShellSlaState, string> = {
  breached: "已逾期",
  "at-risk": "即將到期",
  "on-track": "時程正常",
  none: "未設定 SLA",
};

export const REPORT_CATEGORY_LABEL: Record<string, string> = {
  equipment: "設備",
  staffing: "人力",
  supply: "供貨",
  customer: "顧客",
  other: "其他",
};
