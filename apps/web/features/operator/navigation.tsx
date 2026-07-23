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
  | "expansion-staff"
  | "expansion-manager"
  | "data-steward"
  | "governance-reviewer"
  | "privacy-officer"
  | "permission-limited"
  | "pm-audit";

export type IntakeRoleMode =
  | "own-assigned"
  | "managed-scope"
  | "source-data"
  | "governance-read-only"
  | "purpose-bound"
  | "masked-read-only";

export type OperatorRole = {
  id: OperatorRoleId;
  label: string;
  subtitle: string;
  allowedWorkspaces: WorkspaceId[];
  intakeMode?: IntakeRoleMode;
  intakeModeLabel?: string;
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
    id: "expansion-staff",
    label: "展店專員",
    subtitle: "自有／已指派收件、補件與提案",
    allowedWorkspaces: ["today", "network"],
    intakeMode: "own-assigned",
    intakeModeLabel: "自有／已指派 · 提案者",
  },
  {
    id: "expansion-manager",
    label: "展店經理",
    subtitle: "管理範圍、獨立審查與 Candidate Site",
    allowedWorkspaces: ["today", "network", "govern"],
    intakeMode: "managed-scope",
    intakeModeLabel: "管理範圍 · 獨立審查",
  },
  {
    id: "data-steward",
    label: "資料管理員",
    subtitle: "來源／資料範圍、解析校正與身份處理",
    allowedWorkspaces: ["today", "network", "govern"],
    intakeMode: "source-data",
    intakeModeLabel: "來源／資料範圍 · 校正",
  },
  {
    id: "governance-reviewer",
    label: "治理審查員",
    subtitle: "治理證據、決策與稽核唯讀",
    allowedWorkspaces: ["today", "network", "govern"],
    intakeMode: "governance-read-only",
    intakeModeLabel: "治理範圍 · 唯讀",
  },
  {
    id: "privacy-officer",
    label: "隱私主管",
    subtitle: "目的綁定的受限證據與隱私作業",
    allowedWorkspaces: ["today", "network", "govern"],
    intakeMode: "purpose-bound",
    intakeModeLabel: "目的綁定 · Restricted",
  },
  {
    id: "permission-limited",
    label: "受限檢視者",
    subtitle: "欄位遮罩與唯讀收件檢視",
    allowedWorkspaces: ["today", "network"],
    intakeMode: "masked-read-only",
    intakeModeLabel: "FIELD_MASKED · 唯讀",
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
export const REQUIRED_INTAKE_ROLE_IDS = [
  "expansion-staff",
  "expansion-manager",
  "data-steward",
  "governance-reviewer",
  "privacy-officer",
  "permission-limited",
] as const satisfies readonly OperatorRoleId[];

export function getOperatorRole(roleId: string | null | undefined): OperatorRole {
  return OPERATOR_ROLES.find((role) => role.id === roleId) ?? OPERATOR_ROLES[0];
}

export function getWorkspace(workspaceId: string | null | undefined): WorkspaceNavItem {
  return WORKSPACES.find((workspace) => workspace.id === workspaceId) ?? WORKSPACES[0];
}

export function isWorkspaceAllowed(role: OperatorRole, workspaceId: WorkspaceId) {
  return role.allowedWorkspaces.includes(workspaceId);
}

export function mergeOperatorRoles(remoteRoles: OperatorRole[]): OperatorRole[] {
  const remoteById = new Map(remoteRoles.map((role) => [role.id, role]));
  return OPERATOR_ROLES.map((localRole) => {
    const remoteRole = remoteById.get(localRole.id);
    return remoteRole
      ? {
          ...localRole,
          ...remoteRole,
          intakeMode: localRole.intakeMode,
          intakeModeLabel: localRole.intakeModeLabel,
        }
      : localRole;
  });
}

export function planOperatorRoleSwitch(
  roleId: OperatorRoleId,
  currentWorkspaceId: WorkspaceId,
): { workspaceId: WorkspaceId; preserveDeepLink: boolean } {
  const nextRole = getOperatorRole(roleId);
  if (isWorkspaceAllowed(nextRole, currentWorkspaceId)) {
    return { workspaceId: currentWorkspaceId, preserveDeepLink: true };
  }
  return { workspaceId: DEFAULT_WORKSPACE_ID, preserveDeepLink: false };
}
