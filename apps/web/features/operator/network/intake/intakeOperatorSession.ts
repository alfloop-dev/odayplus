import type {
  CanonicalIntakeInboxBootstrap,
  OperatorBootstrapResponse,
} from "@oday-plus/openapi-client";
import type { OperatorRoleId } from "../../navigation";
import { REQUIRED_INTAKE_ROLE_IDS } from "../../navigation";
import { getIntakeRoleProfile } from "./intakePermissions";

export type IntakeOperatorSessionStatus = "ready" | "unavailable" | "denied";

export type IntakeOperatorScope = {
  resourceInScope: boolean;
  sourceIds: readonly string[];
  ownershipMode: "OWN" | "ASSIGNED" | "MANAGED" | "SOURCE_DATA" | "READ_ONLY";
};

export type IntakeOperatorSession = {
  status: IntakeOperatorSessionStatus;
  roleId: OperatorRoleId | null;
  subjectId: string | null;
  tenantId: string | null;
  systemRoles: readonly string[];
  scope: IntakeOperatorScope | null;
  allowedActions: readonly string[];
  denialReasonByAction: Readonly<Record<string, string>>;
  purposeDeclared: boolean;
  purposeCode: string | null;
  maskingReasonCode: string | null;
  denialReasonCode: string | null;
  source: "operator-bootstrap" | "intake-bootstrap" | "unavailable";
};

const intakeRoleIds = new Set<string>(REQUIRED_INTAKE_ROLE_IDS);

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
    : [];
}

export function unavailableIntakeOperatorSession(
  reasonCode = "AUTHORIZATION_CONTEXT_UNAVAILABLE",
): IntakeOperatorSession {
  return {
    status: "unavailable",
    roleId: null,
    subjectId: null,
    tenantId: null,
    systemRoles: [],
    scope: null,
    allowedActions: [],
    denialReasonByAction: {},
    purposeDeclared: false,
    purposeCode: null,
    maskingReasonCode: null,
    denialReasonCode: reasonCode,
    source: "unavailable",
  };
}

/**
 * The durable Expansion route accepts identity only from the authenticated
 * operator bootstrap. Query parameters and sessionStorage are intentionally
 * not inputs to this parser.
 */
export function parseIntakeOperatorSession(
  payload: OperatorBootstrapResponse | unknown,
): IntakeOperatorSession {
  const root = record(payload);
  const meta = record(root?.meta);
  const role = record(meta?.role);
  const session = record(meta?.session);
  const authorization = record(session?.authorization_context);
  const scope = record(authorization?.resource_scope);
  const denialReasons = record(authorization?.denial_reasons);

  const roleId = stringValue(role?.id);
  const subjectId = stringValue(session?.subject_id);
  const tenantId = stringValue(session?.tenant_id);
  const resourceInScope =
    typeof scope?.resource_in_scope === "boolean" ? scope.resource_in_scope : null;
  const ownershipMode = stringValue(scope?.ownership_mode);
  const systemRoles = stringList(session?.system_roles ?? authorization?.system_roles);
  const allowedActionsValue = authorization?.allowed_actions;
  const allowedActions = stringList(allowedActionsValue);

  if (
    !roleId ||
    !intakeRoleIds.has(roleId) ||
    !subjectId ||
    !tenantId ||
    systemRoles.length === 0
  ) {
    return unavailableIntakeOperatorSession();
  }

  if (!Array.isArray(allowedActionsValue)) {
    return unavailableIntakeOperatorSession("AUTHORIZATION_ACTIONS_UNAVAILABLE");
  }

  if (
    resourceInScope === null ||
    !ownershipMode ||
    !["OWN", "ASSIGNED", "MANAGED", "SOURCE_DATA", "READ_ONLY"].includes(ownershipMode)
  ) {
    return unavailableIntakeOperatorSession("AUTHORIZATION_SCOPE_UNAVAILABLE");
  }

  const denialReasonCode =
    stringValue(authorization?.denial_reason_code) ??
    (resourceInScope ? null : "RESOURCE_SCOPE_DENIED");
  return {
    status: denialReasonCode ? "denied" : "ready",
    roleId: roleId as OperatorRoleId,
    subjectId,
    tenantId,
    systemRoles,
    scope: {
      resourceInScope,
      sourceIds: stringList(scope?.source_ids),
      ownershipMode: ownershipMode as IntakeOperatorScope["ownershipMode"],
    },
    allowedActions,
    denialReasonByAction: Object.fromEntries(
      Object.entries(denialReasons ?? {}).flatMap(([action, reason]) => {
        const value = stringValue(reason);
        return value ? [[action, value]] : [];
      }),
    ),
    purposeDeclared: authorization?.purpose_declared === true,
    purposeCode: stringValue(authorization?.purpose_code),
    maskingReasonCode: stringValue(authorization?.masking_reason_code),
    denialReasonCode,
    source: "operator-bootstrap",
  };
}

const OWNERSHIP_MODE_BY_ROLE: Record<
  CanonicalIntakeInboxBootstrap["role_mode"],
  IntakeOperatorScope["ownershipMode"]
> = {
  "expansion-staff": "OWN",
  "expansion-manager": "MANAGED",
  "data-steward": "SOURCE_DATA",
  "governance-reviewer": "READ_ONLY",
  "privacy-officer": "READ_ONLY",
  "permission-limited": "READ_ONLY",
};

/**
 * Builds the durable Expansion session from the canonical intake bootstrap.
 * The bootstrap identity and role came from the authenticated API principal;
 * browser query state is never an input.
 */
export function parseCanonicalIntakeOperatorSession(
  payload: CanonicalIntakeInboxBootstrap,
): IntakeOperatorSession {
  const roleId = payload.role_mode as OperatorRoleId;
  const profile = getIntakeRoleProfile(roleId);
  if (
    !profile ||
    !intakeRoleIds.has(roleId) ||
    !payload.subject_id ||
    !payload.tenant_id
  ) {
    return unavailableIntakeOperatorSession();
  }

  return {
    status: "ready",
    roleId,
    subjectId: payload.subject_id,
    tenantId: payload.tenant_id,
    systemRoles: [payload.role_mode],
    scope: {
      resourceInScope: true,
      sourceIds: [],
      ownershipMode: OWNERSHIP_MODE_BY_ROLE[payload.role_mode],
    },
    allowedActions: [...profile.actions],
    denialReasonByAction: {},
    purposeDeclared: false,
    purposeCode: null,
    maskingReasonCode:
      payload.role_mode === "permission-limited" ? "FIELD_MASKED" : null,
    denialReasonCode: null,
    source: "intake-bootstrap",
  };
}
