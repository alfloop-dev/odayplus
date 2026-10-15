import type { GovernanceAuditCategory } from "../governanceTypes";

export const GOVERNANCE_TAB_IDS = ["approvals", "decisions", "audit", "userManagement", "evidencePackage", "statusBoard", "featureFlags", "learningHub"] as const;
export type GovernanceTab = (typeof GOVERNANCE_TAB_IDS)[number];
export type GovernanceViewState = {
  tab: GovernanceTab;
  approvalId: string | null;
  evidenceId: string | null;
  auditCategory: GovernanceAuditCategory | "all";
};

type Params = URLSearchParams | Record<string, string | string[] | undefined>;
function read(params: Params, key: string): string | null {
  const value = params instanceof URLSearchParams ? params.get(key) : params[key];
  return (Array.isArray(value) ? value[0] : value)?.trim() || null;
}

export function readGovernanceViewState(params: Params): GovernanceViewState {
  const requestedTab = read(params, "tab");
  return {
    tab: GOVERNANCE_TAB_IDS.find(tab => tab === requestedTab) ?? "approvals",
    approvalId: read(params, "entity"),
    evidenceId: read(params, "evidence"),
    auditCategory: read(params, "auditCategory") ?? "all",
  };
}

export function serializeGovernanceViewState(state: GovernanceViewState, existing: URLSearchParams): URLSearchParams {
  const params = new URLSearchParams(existing);
  params.set("ws", "govern");
  params.set("tab", state.tab);
  for (const [key, value] of [["entity",state.approvalId],["evidence",state.evidenceId],["auditCategory",state.auditCategory === "all" ? null : state.auditCategory]] as const) {
    if (value) params.set(key, value); else params.delete(key);
  }
  return params;
}
