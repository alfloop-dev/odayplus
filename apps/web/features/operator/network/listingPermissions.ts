// Role gating for Listing Radar merge (ODP-OC-R5-011).
//
// Mirrors the SERVER's two independent gates so the console does not offer a
// button that is guaranteed to fail:
//
//   1. The HTTP guard — require_operator_permission("listing", UPDATE)
//      resolved through shared/auth/rbac.py ROLE_PERMISSIONS.
//   2. NetworkListingService.merge_listing's actorRoleId allowlist
//      ({expansionManager, expansion-manager, siteReviewer, site_reviewer}).
//
// A call must clear BOTH, and the intersection is narrower than either. Of the
// console's roles, operatorSecurityHeaders() maps only `expansion-manager` onto
// the expansion_user + site_reviewer claims required for both listing:UPDATE
// and activation of the manager role. `site-reviewer` passes
// the service allowlist but maps to site_reviewer, which holds listing VIEW and
// CREATE but NOT UPDATE — so it would 403 at the HTTP guard.
//
// This guard is presentation only and is deliberately no MORE permissive than
// the server's; the server re-checks every write.

import type { OperatorRoleId } from "../navigation";

const MERGE_ROLES: readonly OperatorRoleId[] = ["expansion-manager"];

export function canMergeListing(roleId: OperatorRoleId): boolean {
  return MERGE_ROLES.includes(roleId);
}

export const MERGE_DENIED_NOTE =
  "你的角色不可標記重複；請切換為展店經理，或洽平台維運調整角色權限。";
