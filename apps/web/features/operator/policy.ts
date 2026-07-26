import { NAV_WORKSPACES, OPERATOR_ROLES } from "./fixtures";
import type {
  ActionAvailability,
  Approval,
  OperatorActionKey,
  OperatorCapability,
  OperatorRole,
  OperatorRoleId,
  WorkspaceKey,
} from "./types";

export const ACTION_CAPABILITY_REQUIREMENTS: Record<OperatorActionKey, OperatorCapability> = {
  "issue.triage": "issue:triage",
  "issue.assign": "issue:assign",
  "issue.execute": "issue:execute",
  "issue.close": "issue:close",
  "evidence.camera.open": "evidence:camera",
  "review.reply": "review:reply",
  "growth.draft": "growth:draft",
  "growth.submitApproval": "growth:submitApproval",
  "network.sourceListings": "network:sourceListings",
  "network.scoreCandidate": "network:scoreCandidate",
  "network.submitReview": "network:submitReview",
  "rebalance.submit": "rebalance:submit",
  "approval.decide": "approval:decide",
  "audit.read": "audit:read",
};

export const WORKSPACE_DISABLED_REASONS: Record<WorkspaceKey, string> = {
  today: "Today is available to every Operator Console role.",
  storeOps: "This role is not assigned to Store Ops incident ownership.",
  growth: "Growth workspace is limited to marketing and cross-functional approval roles.",
  network: "Network workspace is limited to expansion, operations, and audit review roles.",
  govern: "Govern workspace requires approval or audit responsibility.",
};

export function getOperatorRole(roleId: OperatorRoleId): OperatorRole {
  return OPERATOR_ROLES.find((role) => role.id === roleId) ?? OPERATOR_ROLES[0];
}

export function getRoleWorkspaces(roleId: OperatorRoleId): WorkspaceKey[] {
  return getOperatorRole(roleId).workspaces;
}

export function hasCapability(roleId: OperatorRoleId, capability: OperatorCapability): boolean {
  return getOperatorRole(roleId).capabilities.includes(capability);
}

export function canAccessWorkspace(roleId: OperatorRoleId, workspaceId: WorkspaceKey): boolean {
  const workspace = NAV_WORKSPACES.find((item) => item.id === workspaceId);
  return Boolean(workspace && hasCapability(roleId, workspace.requiredCapability));
}

export function getWorkspacePolicy(roleId: OperatorRoleId, workspaceId: WorkspaceKey): ActionAvailability {
  const workspace = NAV_WORKSPACES.find((item) => item.id === workspaceId);
  if (!workspace) {
    return {
      allowed: false,
      reason: "Unknown workspace.",
    };
  }

  if (hasCapability(roleId, workspace.requiredCapability)) {
    return {
      allowed: true,
      requiredCapability: workspace.requiredCapability,
    };
  }

  return {
    allowed: false,
    reason: WORKSPACE_DISABLED_REASONS[workspaceId],
    requiredCapability: workspace.requiredCapability,
  };
}

export function getActionPolicy(roleId: OperatorRoleId, actionKey: OperatorActionKey): ActionAvailability {
  const requiredCapability = ACTION_CAPABILITY_REQUIREMENTS[actionKey];
  if (hasCapability(roleId, requiredCapability)) {
    return {
      allowed: true,
      requiredCapability,
    };
  }

  return {
    allowed: false,
    reason: getActionDisabledReason(actionKey),
    requiredCapability,
  };
}

export function canUseCameraEvidence(roleId: OperatorRoleId): boolean {
  return hasCapability(roleId, "evidence:camera");
}

export function canAccessGrowth(roleId: OperatorRoleId): boolean {
  return canAccessWorkspace(roleId, "growth");
}

export function canAccessNetwork(roleId: OperatorRoleId): boolean {
  return canAccessWorkspace(roleId, "network");
}

export function canAccessGovern(roleId: OperatorRoleId): boolean {
  return canAccessWorkspace(roleId, "govern");
}

export function canDecideApproval(roleId: OperatorRoleId, approval: Approval): ActionAvailability {
  const basePolicy = getActionPolicy(roleId, "approval.decide");
  if (!basePolicy.allowed) return basePolicy;

  if (!approval.requiredRoleIds.includes(roleId)) {
    return {
      allowed: false,
      reason: "This approval requires one of the assigned approver roles.",
      requiredCapability: "approval:decide",
    };
  }

  if (approval.status !== "pending") {
    return {
      allowed: false,
      reason: "Only pending approvals can be decided.",
      requiredCapability: "approval:decide",
    };
  }

  return {
    allowed: true,
    requiredCapability: "approval:decide",
  };
}

export function requiresDecisionReason(nextStatus: "approved" | "returned" | "rejected"): boolean {
  return nextStatus === "returned" || nextStatus === "rejected";
}

export function getActionDisabledReason(actionKey: OperatorActionKey): string {
  switch (actionKey) {
    case "issue.triage":
      return "Triage requires Store Ops ownership for this role.";
    case "issue.assign":
      return "Owner assignment is restricted to Store Ops leads and support leads.";
    case "issue.execute":
      return "Execution actions require operations or facilities ownership.";
    case "issue.close":
      return "Closing an issue requires outcome ownership.";
    case "evidence.camera.open":
      return "Camera evidence requires purpose-based access and an eligible role.";
    case "review.reply":
      return "Public review replies are restricted to customer support roles.";
    case "growth.draft":
      return "Growth draft creation is restricted to marketing roles.";
    case "growth.submitApproval":
      return "Growth approval handoff requires campaign ownership.";
    case "network.sourceListings":
      return "Listing source actions require Network workspace responsibility.";
    case "network.scoreCandidate":
      return "Candidate scoring requires Network or audit review responsibility.";
    case "network.submitReview":
      return "Site review submission is restricted to expansion and operations roles.";
    case "rebalance.submit":
      return "Rebalance submission requires expansion or operations ownership.";
    case "approval.decide":
      return "Approval decisions require Govern responsibility.";
    case "audit.read":
      return "Audit trail access requires governance visibility.";
    default:
      return "This action is not available to the selected role.";
  }
}
