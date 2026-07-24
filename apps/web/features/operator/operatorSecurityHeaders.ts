const OPERATOR_ROLE_STORAGE_KEY = "oday.operator.role";
const OPERATOR_SUBJECT_STORAGE_KEY = "oday.operator.subject";
const OPERATOR_TENANT_STORAGE_KEY = "oday.operator.tenant";
const DEFAULT_OPERATOR_ROLE_ID = "ops-lead";

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

/** Resolve the display actor; production obtains it from `/auth/session`. */
export function operatorSubjectId(
  _roleId?: string | null,
  subjectId?: string | null,
): string {
  if (subjectId?.trim()) return subjectId.trim();
  if (typeof window !== "undefined") {
    const subject = window.sessionStorage.getItem(OPERATOR_SUBJECT_STORAGE_KEY)?.trim();
    if (subject) return subject;
  }
  return "";
}

export function operatorSecurityHeaders(
  roleId?: string | null,
  subjectId?: string | null,
): Record<string, string> {
  const resolvedRoleId = currentOperatorRoleId(roleId);
  const headers: Record<string, string> = {
    "X-Operator-Role": resolvedRoleId,
  };
  if (process.env.NODE_ENV !== "production") {
    headers["X-Roles"] =
      OPERATOR_API_ROLES[resolvedRoleId] ?? "operations_manager";
    const subject = operatorSubjectId(resolvedRoleId, subjectId);
    const tenant =
      typeof window === "undefined"
        ? ""
        : window.sessionStorage.getItem(OPERATOR_TENANT_STORAGE_KEY)?.trim() ||
          "";
    if (subject) headers["X-Subject-Id"] = subject;
    if (tenant) headers["X-Tenant-Id"] = tenant;
  }
  return headers;
}
