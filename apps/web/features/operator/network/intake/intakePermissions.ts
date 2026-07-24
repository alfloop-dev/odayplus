// Role gating for assisted listing intake (ODP-OC-R5-011).
//
// This mirrors the SERVER's two independent gates so the console presents an
// honest surface instead of offering buttons that 403/422:
//
//   1. The HTTP guard — require_operator_permission("listing", VIEW|UPDATE)
//      resolved through shared/auth/rbac.py ROLE_PERMISSIONS.
//   2. NetworkListingService's per-action actorRoleId allowlists.
//
// A call must clear BOTH. Of the console's roles, operatorSecurityHeaders()
// maps `expansion-manager` to an API role with listing mutation grants and
// `pm-audit` to the governance/auditor read-only grant. Every other console
// role has no listing grant, so the queue must not attempt a guaranteed 403.
//
// These guards are presentation only and are deliberately no MORE permissive
// than the server's; the server re-checks every read and write.

import type { OperatorRoleId } from "../../navigation";

export type IntakeAction = "view" | "submit" | "correct" | "decide" | "retry" | "promote";

/**
 * The console role id travels verbatim as `actorRoleId`; the service allowlists
 * accept the kebab-case spelling ("expansion-manager"), so nothing is
 * re-spelled on the wire.
 *
 * Kept per-action rather than as one "can write" flag because the service's
 * allowlists are not symmetric (correct admits dataSteward but not
 * siteReviewer; decide admits siteReviewer but not expansionStaff). If a role
 * with listing:UPDATE is added later, only this table needs to change.
 */
const ALLOWED: Record<IntakeAction, readonly OperatorRoleId[]> = {
  view: ["expansion-manager", "pm-audit"],
  // submit/retry have no service-side allowlist — the listing:UPDATE HTTP
  // guard is their only gate.
  submit: ["expansion-manager"],
  retry: ["expansion-manager"],
  correct: ["expansion-manager"],
  decide: ["expansion-manager"],
  promote: ["expansion-manager"],
};

export function canPerform(action: IntakeAction, roleId: OperatorRoleId): boolean {
  return ALLOWED[action].includes(roleId);
}

export function canView(roleId: OperatorRoleId): boolean {
  return canPerform("view", roleId);
}

/** True when the role may look but not touch — drives the read-only banner. */
export function isReadOnly(roleId: OperatorRoleId): boolean {
  return (
    canView(roleId) &&
    (["submit", "correct", "decide", "retry", "promote"] as IntakeAction[]).every(
      (action) => !canPerform(action, roleId),
    )
  );
}

export const READ_ONLY_NOTE =
  "唯讀模式 — 你的角色可檢視收件紀錄、來源證據與稽核軌跡，但不可送件或決策。";

export const NO_ACCESS_NOTE =
  "權限不足 — URL 收件佇列僅開放展店角色（listing 權限）檢視。請切換為展店經理，或洽平台維運調整角色權限。";

export const ACTION_DENIED_NOTE: Record<Exclude<IntakeAction, "view">, string> = {
  submit: "你的角色不可送出新的收件。",
  correct: "你的角色不可修正欄位；請洽展店經理或資料管理員。",
  decide: "你的角色不可決策此收件；請洽展店經理或選址審核。",
  retry: "你的角色不可重試擷取。",
  promote: "你的角色不可將收件升級為候選點。",
};
