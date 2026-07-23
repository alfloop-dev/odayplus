import { beforeEach, describe, expect, it } from "vitest";
import {
  OPERATOR_ROLES,
  REQUIRED_INTAKE_ROLE_IDS,
  getOperatorRole,
  mergeOperatorRoles,
  planOperatorRoleSwitch,
  type OperatorRoleId,
} from "../../../navigation";
import {
  OPERATOR_API_ROLES,
  operatorSecurityHeaders,
  operatorSubjectId,
} from "../../../operatorSecurityHeaders";
import {
  canProposeIdentity,
  canRequestPromotion,
  canReviewIdentity,
  canReviewPromotion,
  denialNote,
  evaluateIntakePermission,
  getIntakeRoleProfile,
} from "../intakePermissions";
import {
  parseCanonicalIntakeOperatorSession,
  parseIntakeOperatorSession,
  unavailableIntakeOperatorSession,
} from "../intakeOperatorSession";
import { buildOperatorNetworkClient } from "../../operatorNetworkClient";

const mutationContext = {
  resourceInScope: true,
  isOwner: true,
  isAssigned: true,
  sourceInScope: true,
  purposeDeclared: true,
  fieldClassification: "INTERNAL" as const,
  workflowState: "NEEDS_REVIEW",
};

const highRiskContext = {
  ...mutationContext,
  riskLevel: "HIGH" as const,
};

const REQUIRED_ROLES: Array<{
  roleId: OperatorRoleId;
  mode:
    | "own-assigned"
    | "managed-scope"
    | "source-data"
    | "governance-read-only"
    | "purpose-bound"
    | "masked-read-only";
  readOnly: boolean;
  masked: boolean;
}> = [
  { roleId: "expansion-staff", mode: "own-assigned", readOnly: false, masked: false },
  { roleId: "expansion-manager", mode: "managed-scope", readOnly: false, masked: false },
  { roleId: "data-steward", mode: "source-data", readOnly: false, masked: false },
  { roleId: "governance-reviewer", mode: "governance-read-only", readOnly: true, masked: false },
  { roleId: "privacy-officer", mode: "purpose-bound", readOnly: true, masked: false },
  { roleId: "permission-limited", mode: "masked-read-only", readOnly: true, masked: true },
];

describe("Assisted Listing Intake role matrix", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it("publishes all six required roles as selectable Network workspace modes", () => {
    expect(REQUIRED_INTAKE_ROLE_IDS).toEqual(REQUIRED_ROLES.map(({ roleId }) => roleId));

    for (const expected of REQUIRED_ROLES) {
      const navigationRole = getOperatorRole(expected.roleId);
      const profile = getIntakeRoleProfile(expected.roleId);

      expect(navigationRole.id).toBe(expected.roleId);
      expect(navigationRole.allowedWorkspaces).toContain("network");
      expect(navigationRole.intakeMode).toBe(expected.mode);
      expect(navigationRole.intakeModeLabel).toBeTruthy();
      expect(profile).toMatchObject(expected);
      expect(
        evaluateIntakePermission("view", expected.roleId, {
          resourceInScope: true,
        }).allowed,
      ).toBe(true);
    }
  });

  it("keeps required local roles reachable when the shell response is older", () => {
    const remoteManager = {
      ...getOperatorRole("expansion-manager"),
      label: "Remote Expansion Manager",
      intakeMode: undefined,
      intakeModeLabel: undefined,
    };
    const merged = mergeOperatorRoles([remoteManager]);

    expect(merged.find(({ id }) => id === "expansion-manager")).toMatchObject({
      label: "Remote Expansion Manager",
      intakeMode: "managed-scope",
    });
    expect(REQUIRED_INTAKE_ROLE_IDS.every((id) => merged.some((role) => role.id === id))).toBe(true);
    expect(merged).toHaveLength(OPERATOR_ROLES.length);
  });

  it("preserves the current deep-link target for every intake role switch", () => {
    for (const roleId of REQUIRED_INTAKE_ROLE_IDS) {
      expect(planOperatorRoleSwitch(roleId, "network")).toEqual({
        workspaceId: "network",
        preserveDeepLink: true,
      });
    }
  });

  it("enforces own-or-assigned scope for Expansion staff", () => {
    expect(
      evaluateIntakePermission("correct", "expansion-staff", {
        ...mutationContext,
        isOwner: true,
        isAssigned: false,
      }),
    ).toMatchObject({ allowed: true, mode: "own-assigned" });

    expect(
      evaluateIntakePermission("correct", "expansion-staff", {
        ...mutationContext,
        isOwner: false,
        isAssigned: false,
      }),
    ).toMatchObject({ allowed: false, reasonCode: "OWNERSHIP_REQUIRED" });
  });

  it("enforces source scope for Data steward", () => {
    expect(
      evaluateIntakePermission("correct", "data-steward", mutationContext),
    ).toMatchObject({ allowed: true, mode: "source-data" });
    expect(
      evaluateIntakePermission("correct", "data-steward", {
        ...mutationContext,
        sourceInScope: false,
      }),
    ).toMatchObject({ allowed: false, reasonCode: "SOURCE_SCOPE_DENIED" });
    expect(evaluateIntakePermission("requestPromotion", "data-steward")).toMatchObject({
      allowed: false,
      reasonCode: "ROLE_DENIED",
    });
  });

  it("requires declared purpose for restricted Privacy officer evidence", () => {
    expect(
      evaluateIntakePermission("viewRestrictedEvidence", "privacy-officer", {
        fieldClassification: "RESTRICTED",
      }),
    ).toMatchObject({ allowed: false, reasonCode: "PURPOSE_REQUIRED" });
    expect(
      evaluateIntakePermission("viewRestrictedEvidence", "privacy-officer", {
        purposeDeclared: true,
        fieldClassification: "RESTRICTED",
      }),
    ).toMatchObject({ allowed: true, purposeBound: true });
    expect(evaluateIntakePermission("reopenQuarantine", "privacy-officer")).toMatchObject({
      allowed: false,
      reasonCode: "ROLE_DENIED",
      readOnly: true,
    });
  });

  it("keeps Governance reviewer and permission-limited modes read-only", () => {
    expect(evaluateIntakePermission("correct", "governance-reviewer")).toMatchObject({
      allowed: false,
      reasonCode: "ROLE_DENIED",
      readOnly: true,
    });
    expect(evaluateIntakePermission("correct", "permission-limited")).toMatchObject({
      allowed: false,
      reasonCode: "ROLE_DENIED",
      readOnly: true,
      masked: true,
    });
    expect(
      evaluateIntakePermission("correct", "expansion-manager", {
        ...mutationContext,
        fieldMasked: true,
        maskingReasonCode: "FIELD_MASKED",
      }),
    ).toMatchObject({
      allowed: false,
      reasonCode: "FIELD_MASKED",
    });
  });

  it("exposes exact backend denial reason codes in UI notes", () => {
    expect(
      denialNote("correct", "expansion-staff", {
        ...mutationContext,
        isOwner: false,
        isAssigned: false,
      }),
    ).toContain("OWNERSHIP_REQUIRED");
    expect(denialNote("reviewPromotion", "governance-reviewer")).toContain("ROLE_DENIED");
  });

  it("separates ordinary identity proposers from independent reviewers", () => {
    expect(canProposeIdentity("expansion-staff", mutationContext)).toBe(true);
    expect(
      canReviewIdentity("expansion-staff", "staff-1", "staff-2", highRiskContext),
    ).toMatchObject({
      allowed: false,
      reasonCode: "ROLE_DENIED",
    });
    expect(
      canReviewIdentity("expansion-manager", "staff-1", "manager-1", highRiskContext),
    ).toMatchObject({
      allowed: true,
      reasonCode: null,
    });
    expect(
      canReviewIdentity("expansion-manager", "manager-1", "manager-1", highRiskContext),
    ).toMatchObject({
      allowed: false,
      reasonCode: "SELF_REVIEW_DENIED",
    });
  });

  it("separates promotion requesters from independent manager reviewers", () => {
    expect(canRequestPromotion("expansion-staff", mutationContext)).toBe(true);
    expect(
      canReviewPromotion("expansion-staff", "staff-1", "staff-2", highRiskContext),
    ).toMatchObject({
      allowed: false,
      reasonCode: "ROLE_DENIED",
    });
    expect(
      canReviewPromotion("expansion-manager", "staff-1", "manager-1", highRiskContext),
    ).toMatchObject({
      allowed: true,
      reasonCode: null,
    });
    expect(
      canReviewPromotion("expansion-manager", "manager-1", "manager-1", highRiskContext),
    ).toMatchObject({
      allowed: false,
      reasonCode: "SELF_REVIEW_DENIED",
    });
    expect(
      evaluateIntakePermission("executePromotion", "expansion-manager", {
        ...highRiskContext,
        proposerSubjectId: "staff-1",
        reviewerSubjectId: "manager-1",
      }),
    ).toMatchObject({ allowed: true, reasonCode: null });
    expect(
      evaluateIntakePermission("executePromotion", "expansion-staff", {
        ...highRiskContext,
        proposerSubjectId: "staff-1",
        reviewerSubjectId: "manager-1",
      }),
    ).toMatchObject({ allowed: false, reasonCode: "ROLE_DENIED" });
  });

  it("maps each intake mode to backend-recognized role headers", () => {
    expect(OPERATOR_API_ROLES).toMatchObject({
      "expansion-staff": "expansion_user",
      "expansion-manager": "expansion_user,site_reviewer",
      "data-steward": "data_owner,expansion_user",
      "governance-reviewer": "auditor",
      "privacy-officer": "finance_legal,auditor",
      "permission-limited": "auditor",
    });

    for (const roleId of REQUIRED_INTAKE_ROLE_IDS) {
      expect(operatorSecurityHeaders(roleId, `subject-${roleId}`)).toMatchObject({
        "X-Operator-Role": roleId,
        "X-Roles": OPERATOR_API_ROLES[roleId],
        "X-Subject-Id": `subject-${roleId}`,
        "X-Tenant-Id": "tenant-a",
      });
    }
  });

  it("keeps a signed-in subject stable across role changes for self-review enforcement", () => {
    window.sessionStorage.setItem("oday.operator.subject", "human-42");

    expect(operatorSubjectId("expansion-staff")).toBe("human-42");
    expect(operatorSecurityHeaders("expansion-manager")["X-Subject-Id"]).toBe("human-42");
  });

  it("fails closed for an unmapped role instead of granting operations_manager", () => {
    expect(operatorSecurityHeaders("unknown-role", "subject-x")["X-Roles"]).toBe("");
  });

  it("never applies legacy tenant or mapped-role fallbacks to an authoritative client", () => {
    expect(
      operatorSecurityHeaders("expansion-manager", "operator-42", {
        authoritative: true,
      }),
    ).toMatchObject({
      "X-Operator-Role": "expansion-manager",
      "X-Roles": "",
      "X-Subject-Id": "operator-42",
      "X-Tenant-Id": "",
    });
    expect(
      buildOperatorNetworkClient("expansion-manager", "operator-42", {
        authoritative: true,
        tenantId: "tenant-a",
        systemRoles: [],
      }),
    ).toBeNull();
  });

  it("fails closed when a mutation is missing authoritative scope or workflow context", () => {
    expect(evaluateIntakePermission("submit", "expansion-manager")).toMatchObject({
      allowed: false,
      reasonCode: "AUTHORIZATION_CONTEXT_REQUIRED",
    });
    expect(
      evaluateIntakePermission("correct", "expansion-manager", {
        resourceInScope: true,
      }),
    ).toMatchObject({
      allowed: false,
      reasonCode: "AUTHORIZATION_CONTEXT_REQUIRED",
    });
  });

  it("preserves authoritative denial and masking reason codes", () => {
    expect(
      evaluateIntakePermission("submit", "expansion-manager", {
        ...mutationContext,
        serverAllowed: false,
        serverReasonCode: "ASSIGNMENT_SCOPE_DENIED",
      }),
    ).toMatchObject({
      allowed: false,
      reasonCode: "ASSIGNMENT_SCOPE_DENIED",
    });
    expect(
      evaluateIntakePermission("correct", "expansion-manager", {
        ...mutationContext,
        fieldMasked: true,
        maskingReasonCode: "FIELD_MASKED",
      }),
    ).toMatchObject({
      allowed: false,
      reasonCode: "FIELD_MASKED",
    });
  });

  it("accepts only a complete authoritative bootstrap session", () => {
    expect(
      parseIntakeOperatorSession(
        {
          meta: {
            role: { id: "expansion-manager" },
            session: {
              subject_id: "operator-42",
              tenant_id: "tenant-a",
              system_roles: ["expansion_user", "site_reviewer"],
              authorization_context: {
                allowed_actions: [
                  "view",
                  "submit",
                  "correct",
                  "requestPromotion",
                  "reviewPromotion",
                  "executePromotion",
                  "replayScore",
                ],
                resource_scope: {
                  resource_in_scope: true,
                  ownership_mode: "MANAGED",
                  source_ids: ["src-591"],
                },
                purpose_declared: true,
                purpose_code: "EXPANSION_REVIEW",
                masking_reason_code: "FIELD_MASKED",
              },
            },
          },
        },
      ),
    ).toMatchObject({
      status: "ready",
      roleId: "expansion-manager",
      subjectId: "operator-42",
      tenantId: "tenant-a",
      source: "operator-bootstrap",
    });

    expect(
      parseIntakeOperatorSession({
        meta: { role: { id: "expansion-manager" } },
      }),
    ).toEqual(unavailableIntakeOperatorSession());

    expect(
      parseIntakeOperatorSession({
        meta: {
          role: { id: "expansion-manager" },
          session: {
            subject_id: "operator-42",
            tenant_id: "tenant-a",
            system_roles: ["expansion_user", "site_reviewer"],
          },
          authorization_context: {
            allowed_actions: ["view"],
            resource_scope: {
              resource_in_scope: true,
              ownership_mode: "MANAGED",
            },
          },
        },
      }),
    ).toEqual(unavailableIntakeOperatorSession("AUTHORIZATION_ACTIONS_UNAVAILABLE"));

    expect(
      parseIntakeOperatorSession({
        meta: {
          role: { id: "expansion-manager" },
          session: {
            subject_id: "operator-42",
            tenant_id: "tenant-a",
            authorization_context: {
              allowed_actions: ["view"],
              resource_scope: {
                resource_in_scope: true,
                ownership_mode: "MANAGED",
              },
            },
          },
        },
      }),
    ).toEqual(unavailableIntakeOperatorSession());

    expect(
      parseIntakeOperatorSession({
        meta: {
          role: { id: "expansion-manager" },
          session: {
            subject_id: "operator-42",
            tenant_id: "tenant-a",
            system_roles: ["expansion_user", "site_reviewer"],
            authorization_context: {
              allowed_actions: ["view"],
              resource_scope: {
                resource_in_scope: false,
                ownership_mode: "MANAGED",
              },
            },
          },
        },
      }),
    ).toMatchObject({
      status: "denied",
      denialReasonCode: "RESOURCE_SCOPE_DENIED",
    });
  });

  it("recognizes all six roles only from complete authoritative bootstrap sessions", () => {
    for (const roleId of REQUIRED_INTAKE_ROLE_IDS) {
      const ownershipMode =
        roleId === "expansion-staff"
          ? "OWN"
          : roleId === "data-steward"
            ? "SOURCE_DATA"
            : roleId === "governance-reviewer" ||
                roleId === "privacy-officer" ||
                roleId === "permission-limited"
              ? "READ_ONLY"
              : "MANAGED";
      expect(
        parseIntakeOperatorSession({
          meta: {
            role: { id: roleId },
            session: {
              subject_id: `subject-${roleId}`,
              tenant_id: "tenant-a",
              system_roles: OPERATOR_API_ROLES[roleId].split(","),
              authorization_context: {
                allowed_actions: ["view"],
                resource_scope: {
                  resource_in_scope: true,
                  ownership_mode: ownershipMode,
                },
              },
            },
          },
        }),
      ).toMatchObject({
        status: "ready",
        roleId,
        subjectId: `subject-${roleId}`,
      });
    }
  });

  it("builds every durable role session from the canonical intake bootstrap", () => {
    for (const roleId of REQUIRED_INTAKE_ROLE_IDS) {
      const session = parseCanonicalIntakeOperatorSession({
        tenant_id: "tenant-a",
        subject_id: `subject-${roleId}`,
        role_mode: roleId,
        scope: {
          tenant_id: "tenant-a",
          brand_ids: [],
          region_ids: [],
          assigned_area_ids: [],
          heat_zone_ids: [],
        },
        heat_zones: [],
        selected_heat_zone_id: null,
        intake_methods: ["URL", "MANUAL", "CSV", "APPROVED_FEED"],
        intake_states: ["SUBMITTED", "READY"],
        match_outcomes: [
          "NEW",
          "EXACT_DUPLICATE",
          "REVISION",
          "POSSIBLE_MATCH",
          "QUARANTINED",
        ],
        assignment_states: [
          "ASSIGNED",
          "CLAIMED",
          "TRANSFERRED",
          "ESCALATED",
          "COMPLETED",
        ],
        sla_states: [
          "ON_TRACK",
          "DUE_SOON",
          "OVERDUE",
          "BREACHED",
          "PAUSED",
          "COMPLETED",
        ],
        saved_views: [],
        commands: {
          assign: {
            method: "PUT",
            path_template: "/api/v1/intakes/{intake_id}/assignment",
            requires_if_match: true,
            requires_idempotency_key: true,
          },
          claim: {
            method: "POST",
            path_template:
              "/api/v1/assignments/{assignment_id}/actions/claim",
            requires_if_match: true,
            requires_idempotency_key: true,
          },
          transfer: {
            method: "POST",
            path_template:
              "/api/v1/assignments/{assignment_id}/actions/transfer",
            requires_if_match: true,
            requires_idempotency_key: true,
          },
          complete: {
            method: "POST",
            path_template:
              "/api/v1/assignments/{assignment_id}/actions/complete",
            requires_if_match: true,
            requires_idempotency_key: true,
          },
        },
      });
      expect(session).toMatchObject({
        status: "ready",
        roleId,
        subjectId: `subject-${roleId}`,
        tenantId: "tenant-a",
        source: "intake-bootstrap",
      });
      expect(session.allowedActions).toContain("view");
      expect(session.systemRoles).toEqual([roleId]);
    }
  });
});
