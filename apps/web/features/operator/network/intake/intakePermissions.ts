import type { IntakeRoleMode, OperatorRoleId } from "../../navigation";

export type IntakeAction =
  | "view"
  | "submit"
  | "correct"
  | "decide"
  | "retry"
  | "promote";

export type IntakePermissionAction =
  | IntakeAction
  | "assign"
  | "viewEvidence"
  | "viewRestrictedEvidence"
  | "proposeIdentity"
  | "reviewIdentity"
  | "requestPromotion"
  | "reviewPromotion"
  | "reopenQuarantine"
  | "exportEvidence";

export type IntakeDenialReasonCode =
  | "ROLE_DENIED"
  | "OWNERSHIP_REQUIRED"
  | "ASSIGNMENT_SCOPE_DENIED"
  | "SOURCE_SCOPE_DENIED"
  | "FIELD_MASKED"
  | "DATA_CLASSIFICATION_DENIED"
  | "PURPOSE_REQUIRED"
  | "SECOND_ACTOR_REQUIRED"
  | "SELF_REVIEW_DENIED";

export type IntakePermissionContext = {
  isOwner?: boolean;
  isAssigned?: boolean;
  sourceInScope?: boolean;
  purposeDeclared?: boolean;
  fieldMasked?: boolean;
  proposerSubjectId?: string | null;
  reviewerSubjectId?: string | null;
};

export type IntakePermissionDecision = {
  allowed: boolean;
  reasonCode: IntakeDenialReasonCode | null;
  mode: IntakeRoleMode | "no-access";
  scopeLabel: string;
  readOnly: boolean;
  masked: boolean;
  purposeBound: boolean;
};

export type IntakeRoleProfile = {
  roleId: OperatorRoleId;
  mode: IntakeRoleMode;
  scopeLabel: string;
  readOnly: boolean;
  masked: boolean;
  purposeBound: boolean;
  actions: ReadonlySet<IntakePermissionAction>;
};

const actions = (...values: IntakePermissionAction[]) =>
  new Set<IntakePermissionAction>(values);

export const INTAKE_ROLE_PROFILES: Partial<Record<OperatorRoleId, IntakeRoleProfile>> = {
  "expansion-staff": {
    roleId: "expansion-staff",
    mode: "own-assigned",
    scopeLabel: "僅限自有或已指派收件",
    readOnly: false,
    masked: false,
    purposeBound: false,
    actions: actions(
      "view",
      "submit",
      "correct",
      "retry",
      "promote",
      "viewEvidence",
      "proposeIdentity",
      "requestPromotion",
      "exportEvidence",
    ),
  },
  "expansion-manager": {
    roleId: "expansion-manager",
    mode: "managed-scope",
    scopeLabel: "品牌／區域／assigned area／HeatZone 管理範圍",
    readOnly: false,
    masked: false,
    purposeBound: false,
    actions: actions(
      "view",
      "submit",
      "correct",
      "decide",
      "retry",
      "promote",
      "assign",
      "viewEvidence",
      "proposeIdentity",
      "reviewIdentity",
      "requestPromotion",
      "reviewPromotion",
      "reopenQuarantine",
      "exportEvidence",
    ),
  },
  "data-steward": {
    roleId: "data-steward",
    mode: "source-data",
    scopeLabel: "核准的 source／data domain 範圍",
    readOnly: false,
    masked: false,
    purposeBound: false,
    actions: actions(
      "view",
      "submit",
      "correct",
      "decide",
      "retry",
      "assign",
      "viewEvidence",
      "proposeIdentity",
      "reviewIdentity",
      "reopenQuarantine",
      "exportEvidence",
    ),
  },
  "governance-reviewer": {
    roleId: "governance-reviewer",
    mode: "governance-read-only",
    scopeLabel: "tenant 內治理／audit evidence 唯讀",
    readOnly: true,
    masked: false,
    purposeBound: true,
    actions: actions("view", "viewEvidence", "exportEvidence"),
  },
  "privacy-officer": {
    roleId: "privacy-officer",
    mode: "purpose-bound",
    scopeLabel: "具目的聲明的 restricted evidence／privacy 範圍",
    readOnly: true,
    masked: false,
    purposeBound: true,
    actions: actions(
      "view",
      "viewEvidence",
      "viewRestrictedEvidence",
      "exportEvidence",
    ),
  },
  "permission-limited": {
    roleId: "permission-limited",
    mode: "masked-read-only",
    scopeLabel: "已遮罩欄位的唯讀收件範圍",
    readOnly: true,
    masked: true,
    purposeBound: false,
    actions: actions("view"),
  },
  "pm-audit": {
    roleId: "pm-audit",
    mode: "governance-read-only",
    scopeLabel: "舊版 PM／audit 唯讀範圍",
    readOnly: true,
    masked: false,
    purposeBound: true,
    actions: actions("view", "viewEvidence", "exportEvidence"),
  },
};

const NO_ACCESS_PROFILE = {
  mode: "no-access" as const,
  scopeLabel: "沒有 Assisted Listing Intake 存取範圍",
  readOnly: true,
  masked: true,
  purposeBound: false,
};

export function getIntakeRoleProfile(roleId: OperatorRoleId): IntakeRoleProfile | null {
  return INTAKE_ROLE_PROFILES[roleId] ?? null;
}

function denied(
  profile: IntakeRoleProfile | null,
  reasonCode: IntakeDenialReasonCode,
): IntakePermissionDecision {
  return {
    allowed: false,
    reasonCode,
    mode: profile?.mode ?? NO_ACCESS_PROFILE.mode,
    scopeLabel: profile?.scopeLabel ?? NO_ACCESS_PROFILE.scopeLabel,
    readOnly: profile?.readOnly ?? NO_ACCESS_PROFILE.readOnly,
    masked: profile?.masked ?? NO_ACCESS_PROFILE.masked,
    purposeBound: profile?.purposeBound ?? NO_ACCESS_PROFILE.purposeBound,
  };
}

export function evaluateIntakePermission(
  action: IntakePermissionAction,
  roleId: OperatorRoleId,
  context: IntakePermissionContext = {},
): IntakePermissionDecision {
  const profile = getIntakeRoleProfile(roleId);
  if (!profile || !profile.actions.has(action)) return denied(profile, "ROLE_DENIED");

  if (
    profile.mode === "own-assigned" &&
    (context.isOwner !== undefined || context.isAssigned !== undefined) &&
    context.isOwner !== true &&
    context.isAssigned !== true
  ) {
    return denied(profile, "OWNERSHIP_REQUIRED");
  }

  if (profile.mode === "source-data" && context.sourceInScope === false) {
    return denied(profile, "SOURCE_SCOPE_DENIED");
  }

  if (context.fieldMasked && action !== "view" && action !== "viewEvidence") {
    return denied(profile, "DATA_CLASSIFICATION_DENIED");
  }

  if (
    action === "viewRestrictedEvidence" &&
    profile.purposeBound &&
    context.purposeDeclared !== true
  ) {
    return denied(profile, "PURPOSE_REQUIRED");
  }

  if (action === "reviewIdentity" || action === "reviewPromotion") {
    if (!context.reviewerSubjectId) return denied(profile, "SECOND_ACTOR_REQUIRED");
    if (
      context.proposerSubjectId &&
      context.proposerSubjectId === context.reviewerSubjectId
    ) {
      return denied(profile, "SELF_REVIEW_DENIED");
    }
  }

  return {
    allowed: true,
    reasonCode: null,
    mode: profile.mode,
    scopeLabel: profile.scopeLabel,
    readOnly: profile.readOnly,
    masked: profile.masked,
    purposeBound: profile.purposeBound,
  };
}

export function canPerform(action: IntakeAction, roleId: OperatorRoleId): boolean {
  return evaluateIntakePermission(action, roleId).allowed;
}

export function canView(roleId: OperatorRoleId): boolean {
  return canPerform("view", roleId);
}

export function isReadOnly(roleId: OperatorRoleId): boolean {
  return getIntakeRoleProfile(roleId)?.readOnly ?? false;
}

export function canProposeIdentity(roleId: OperatorRoleId): boolean {
  return evaluateIntakePermission("proposeIdentity", roleId).allowed;
}

export function canReviewIdentity(
  roleId: OperatorRoleId,
  proposerSubjectId: string | null | undefined,
  reviewerSubjectId: string | null | undefined,
): IntakePermissionDecision {
  return evaluateIntakePermission("reviewIdentity", roleId, {
    proposerSubjectId,
    reviewerSubjectId,
  });
}

export function canRequestPromotion(roleId: OperatorRoleId): boolean {
  return evaluateIntakePermission("requestPromotion", roleId).allowed;
}

export function canReviewPromotion(
  roleId: OperatorRoleId,
  proposerSubjectId: string | null | undefined,
  reviewerSubjectId: string | null | undefined,
): IntakePermissionDecision {
  return evaluateIntakePermission("reviewPromotion", roleId, {
    proposerSubjectId,
    reviewerSubjectId,
  });
}

export function denialNote(
  action: IntakePermissionAction,
  roleId: OperatorRoleId,
  context: IntakePermissionContext = {},
): string | null {
  const decision = evaluateIntakePermission(action, roleId, context);
  if (decision.allowed || !decision.reasonCode) return null;
  return `${decision.scopeLabel}；後端拒絕代碼：${decision.reasonCode}`;
}

export const READ_ONLY_NOTE =
  "唯讀模式 — 可檢視授權範圍內的收件、證據與稽核資料；寫入動作由後端以 ROLE_DENIED 拒絕。";

export const NO_ACCESS_NOTE =
  "權限不足 — 此角色沒有 Assisted Listing Intake 存取權（ROLE_DENIED）。";

export const ACTION_DENIED_NOTE: Record<Exclude<IntakeAction, "view">, string> = {
  submit: "不可送出新的收件（ROLE_DENIED）。",
  correct: "不可修正此欄位（ROLE_DENIED；遮罩欄位為 DATA_CLASSIFICATION_DENIED）。",
  decide: "不可審查此身份決策（ROLE_DENIED／SECOND_ACTOR_REQUIRED）。",
  retry: "不可重試此收件（ROLE_DENIED／OWNERSHIP_REQUIRED）。",
  promote: "不可執行此晉升動作（ROLE_DENIED／SELF_REVIEW_DENIED）。",
};
