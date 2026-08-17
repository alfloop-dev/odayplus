// Role gating for governed geocoder search (ODP-CAP-GEOCODER-SEARCH-001).
//
// Accepting a geocode result writes a coordinate onto an expansion record, so
// the select gate is the same one Listing Radar's merge uses: of the console's
// roles, operatorSecurityHeaders() maps only `expansion-manager` onto the
// claims that carry listing:UPDATE. `pm-audit` holds the governance read-only
// grant, so it may search and inspect candidates — including the flags and the
// review reasons — but not accept one.
//
// Presentation only, and deliberately no MORE permissive than the server; the
// surface that persists the accepted coordinate re-checks the role.

import type { OperatorRoleId } from "../../navigation";

const SEARCH_ROLES: readonly OperatorRoleId[] = ["expansion-manager", "pm-audit"];
const SELECT_ROLES: readonly OperatorRoleId[] = ["expansion-manager"];

export function canSearchAddress(roleId: OperatorRoleId): boolean {
  return SEARCH_ROLES.includes(roleId);
}

export function canSelectGeocodeCandidate(roleId: OperatorRoleId): boolean {
  return SELECT_ROLES.includes(roleId);
}
