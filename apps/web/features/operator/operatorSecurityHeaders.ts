const OPERATOR_ROLE_STORAGE_KEY = "oday.operator.role";
const OPERATOR_SUBJECT_STORAGE_KEY = "oday.operator.subject";
const OPERATOR_TENANT_STORAGE_KEY = "oday.operator.tenant";
const DEFAULT_OPERATOR_ROLE_ID = "ops-lead";

/** Browser hints can invalidate a lease; they never authenticate its owner.
 * Callers must report unavailable storage rather than treating it as absence. */
export function readOperatorBrowserHints(storage?: Storage): { subject: string; tenant: string | null } {
  if (typeof window === "undefined") return { subject: "", tenant: null };
  const current = storage ?? window.sessionStorage;
  return { subject: current.getItem(OPERATOR_SUBJECT_STORAGE_KEY)?.trim() || "",
    tenant: current.getItem(OPERATOR_TENANT_STORAGE_KEY) };
}

export type OperatorStorageFailureHandler = (retryReadOrPreference: () => void) => void;

const OPERATOR_API_ROLES: Record<string, string> = {
  "platform-admin": "platform_admin",
  platformAdmin: "platform_admin",
  admin: "platform_admin",
  platform_admin: "platform_admin",
  "cs-lead": "operations_manager",
  csLead: "operations_manager",
  supportLead: "operations_manager",
  "expansion-manager": "expansion_user,site_reviewer",
  "expansion-staff": "expansion_user",
  expansionManager: "expansion_user,site_reviewer",
  "field-lead": "regional_supervisor",
  facilitiesLead: "regional_supervisor",
  fieldLead: "regional_supervisor",
  "marketing-manager": "marketing_manager",
  marketingManager: "marketing_manager",
  "ops-lead": "operations_manager",
  opsLead: "operations_manager",
  operations_manager: "operations_manager",
  "pm-audit": "auditor",
  auditPm: "auditor",
  pmAudit: "auditor",
  auditor: "auditor",
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
    const subject = readOperatorBrowserHints().subject;
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
  if (!isOperatorProductionMode()) {
    headers["X-Roles"] =
      OPERATOR_API_ROLES[resolvedRoleId] ?? "operations_manager";
    const hints = readOperatorBrowserHints();
    const subject = subjectId?.trim() || hints.subject;
    const tenant = hints.tenant?.trim() || "";
    if (subject) headers["X-Subject-Id"] = subject;
    if (tenant) headers["X-Tenant-Id"] = tenant;
  }
  return headers;
}
import { isOperatorProductionMode } from "./operatorDataMode";
