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
      expect(evaluateIntakePermission("view", expected.roleId).allowed).toBe(true);
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
        isOwner: true,
        isAssigned: false,
      }),
    ).toMatchObject({ allowed: true, mode: "own-assigned" });

    expect(
      evaluateIntakePermission("correct", "expansion-staff", {
        isOwner: false,
        isAssigned: false,
      }),
    ).toMatchObject({ allowed: false, reasonCode: "OWNERSHIP_REQUIRED" });
  });

  it("enforces source scope for Data steward", () => {
    expect(
      evaluateIntakePermission("correct", "data-steward", { sourceInScope: true }),
    ).toMatchObject({ allowed: true, mode: "source-data" });
    expect(
      evaluateIntakePermission("correct", "data-steward", { sourceInScope: false }),
    ).toMatchObject({ allowed: false, reasonCode: "SOURCE_SCOPE_DENIED" });
    expect(evaluateIntakePermission("requestPromotion", "data-steward")).toMatchObject({
      allowed: false,
      reasonCode: "ROLE_DENIED",
    });
  });

  it("requires declared purpose for restricted Privacy officer evidence", () => {
    expect(
      evaluateIntakePermission("viewRestrictedEvidence", "privacy-officer"),
    ).toMatchObject({ allowed: false, reasonCode: "PURPOSE_REQUIRED" });
    expect(
      evaluateIntakePermission("viewRestrictedEvidence", "privacy-officer", {
        purposeDeclared: true,
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
      evaluateIntakePermission("correct", "expansion-manager", { fieldMasked: true }),
    ).toMatchObject({
      allowed: false,
      reasonCode: "DATA_CLASSIFICATION_DENIED",
    });
  });

  it("exposes exact backend denial reason codes in UI notes", () => {
    expect(
      denialNote("correct", "expansion-staff", { isOwner: false, isAssigned: false }),
    ).toContain("OWNERSHIP_REQUIRED");
    expect(denialNote("reviewPromotion", "governance-reviewer")).toContain("ROLE_DENIED");
  });

  it("separates ordinary identity proposers from independent reviewers", () => {
    expect(canProposeIdentity("expansion-staff")).toBe(true);
    expect(canReviewIdentity("expansion-staff", "staff-1", "staff-2")).toMatchObject({
      allowed: false,
      reasonCode: "ROLE_DENIED",
    });
    expect(canReviewIdentity("expansion-manager", "staff-1", "manager-1")).toMatchObject({
      allowed: true,
      reasonCode: null,
    });
    expect(canReviewIdentity("expansion-manager", "manager-1", "manager-1")).toMatchObject({
      allowed: false,
      reasonCode: "SELF_REVIEW_DENIED",
    });
  });

  it("separates promotion requesters from independent manager reviewers", () => {
    expect(canRequestPromotion("expansion-staff")).toBe(true);
    expect(canReviewPromotion("expansion-staff", "staff-1", "staff-2")).toMatchObject({
      allowed: false,
      reasonCode: "ROLE_DENIED",
    });
    expect(canReviewPromotion("expansion-manager", "staff-1", "manager-1")).toMatchObject({
      allowed: true,
      reasonCode: null,
    });
    expect(canReviewPromotion("expansion-manager", "manager-1", "manager-1")).toMatchObject({
      allowed: false,
      reasonCode: "SELF_REVIEW_DENIED",
    });
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
});
