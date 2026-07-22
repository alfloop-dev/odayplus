const OPERATOR_ROLE_STORAGE_KEY = "oday.operator.role";
const OPERATOR_SUBJECT_STORAGE_KEY = "oday.operator.subject";
const DEFAULT_OPERATOR_ROLE_ID = "ops-lead";
const OPERATOR_TENANT_ID = "tenant-a";

const OPERATOR_API_ROLES: Record<string, string> = {
  "cs-lead": "operations_manager",
  csLead: "operations_manager",
  supportLead: "operations_manager",
  "expansion-manager": "expansion_user,site_reviewer",
  expansionManager: "expansion_user,site_reviewer",
  "field-lead": "regional_supervisor",
  facilitiesLead: "regional_supervisor",
  fieldLead: "regional_supervisor",
  "marketing-manager": "marketing_manager",
  marketingManager: "marketing_manager",
  "ops-lead": "operations_manager",
  opsLead: "operations_manager",
  "pm-audit": "auditor",
  auditPm: "auditor",
  pmAudit: "auditor",
  "site-reviewer": "site_reviewer",
  siteReviewer: "site_reviewer",
};

function currentOperatorRoleId(roleId?: string | null): string {
  if (roleId?.trim()) return roleId.trim();
  if (typeof window === "undefined") return DEFAULT_OPERATOR_ROLE_ID;

  return window.sessionStorage.getItem(OPERATOR_ROLE_STORAGE_KEY) || DEFAULT_OPERATOR_ROLE_ID;
}

/** Subject identity sent to the API; UI authorization hints must use this too. */
export function operatorSubjectId(
  roleId?: string | null,
  subjectId?: string | null,
): string {
  if (subjectId?.trim()) return subjectId.trim();
  if (typeof window !== "undefined") {
    const subject = window.sessionStorage.getItem(OPERATOR_SUBJECT_STORAGE_KEY)?.trim();
    if (subject) return subject;
  }
  return `operator-${currentOperatorRoleId(roleId)}`;
}

export function operatorSecurityHeaders(
  roleId?: string | null,
  subjectId?: string | null,
): Record<string, string> {
  const resolvedRoleId = currentOperatorRoleId(roleId);

  return {
    "X-Operator-Role": resolvedRoleId,
    "X-Roles": OPERATOR_API_ROLES[resolvedRoleId] ?? "operations_manager",
    "X-Subject-Id": operatorSubjectId(resolvedRoleId, subjectId),
    "X-Tenant-Id": OPERATOR_TENANT_ID,
  };
}
