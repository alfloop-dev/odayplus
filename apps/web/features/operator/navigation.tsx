export type WorkspaceId = "today" | "store" | "growth" | "network" | "govern";

export type WorkspaceNavItem = {
  id: WorkspaceId;
  label: string;
  shortLabel: string;
  description: string;
};

export type OperatorRoleId =
  | "ops-lead"
  | "cs-lead"
  | "field-lead"
  | "marketing-manager"
  | "expansion-manager"
  | "pm-audit";

export type OperatorRole = {
  id: OperatorRoleId;
  label: string;
  subtitle: string;
  allowedWorkspaces: WorkspaceId[];
};

export const WORKSPACES: WorkspaceNavItem[] = [
  {
    id: "today",
    label: "今日工作",
    shortLabel: "Today",
    description: "Today",
  },
  {
    id: "store",
    label: "門市營運",
    shortLabel: "Store",
    description: "Store Ops",
  },
  {
    id: "growth",
    label: "營收成長",
    shortLabel: "Growth",
    description: "Growth",
  },
  {
    id: "network",
    label: "展店與店網",
    shortLabel: "Network",
    description: "Network",
  },
  {
    id: "govern",
    label: "治理稽核",
    shortLabel: "Govern",
    description: "Govern",
  },
];

export const OPERATOR_ROLES: OperatorRole[] = [
  {
    id: "ops-lead",
    label: "營運主管",
    subtitle: "全域監控、跨域指派與核准",
    allowedWorkspaces: ["today", "store", "growth", "network", "govern"],
  },
  {
    id: "cs-lead",
    label: "客服主管",
    subtitle: "評論、客服案件與門市回覆",
    allowedWorkspaces: ["today", "store", "govern"],
  },
  {
    id: "field-lead",
    label: "工務主任",
    subtitle: "設備、現場維修與執行回報",
    allowedWorkspaces: ["today", "store"],
  },
  {
    id: "marketing-manager",
    label: "行銷經理",
    subtitle: "活動、分群、定價建議",
    allowedWorkspaces: ["today", "growth", "govern"],
  },
  {
    id: "expansion-manager",
    label: "展店經理",
    subtitle: "HeatZone、候選點與 SiteScore",
    allowedWorkspaces: ["today", "network", "govern"],
  },
  {
    id: "pm-audit",
    label: "PM／稽核",
    subtitle: "模型、決策追蹤與稽核線索",
    allowedWorkspaces: ["today", "store", "network", "govern"],
  },
];

export const DEFAULT_OPERATOR_ROLE_ID: OperatorRoleId = "ops-lead";
export const DEFAULT_WORKSPACE_ID: WorkspaceId = "today";

export function getOperatorRole(roleId: string | null | undefined): OperatorRole {
  return OPERATOR_ROLES.find((role) => role.id === roleId) ?? OPERATOR_ROLES[0];
}

export function getWorkspace(workspaceId: string | null | undefined): WorkspaceNavItem {
  return WORKSPACES.find((workspace) => workspace.id === workspaceId) ?? WORKSPACES[0];
}

export function isWorkspaceAllowed(role: OperatorRole, workspaceId: WorkspaceId) {
  return role.allowedWorkspaces.includes(workspaceId);
}
