import type { Issue, OperatorRoleId, Severity } from "./types";

export const STORE_OPS_REFRESH_EVENT = "oday:store-ops-refresh";

export const STORE_OPS_WORKFLOW_DIALOG_TYPES = [
  "triage",
  "assign",
  "action",
  "fieldReport",
  "outcome",
  "escalate",
  "cameraPurpose",
  "replyReview",
  "transfer",
] as const;

export type StoreOpsWorkflowDialogType = (typeof STORE_OPS_WORKFLOW_DIALOG_TYPES)[number];
export type StoreOpsWorkflowIssue = Issue;

export type StoreOpsWorkflowPayloadBase = {
  issueId: string;
  issueTitle: string;
  storeId: string;
  storeName: string;
};

export type StoreOpsEvidenceStrength = "weak" | "usable" | "strong";
export type StoreOpsTriageCategory = "service" | "cleanliness" | "staffing" | "device" | "payment" | "multiSignal";
export type StoreOpsTriageDecision = "accept" | "needEvidence" | "fastForward";

export type StoreOpsTriagePayload = StoreOpsWorkflowPayloadBase & {
  severity: Severity;
  category: StoreOpsTriageCategory;
  evidenceStrength: StoreOpsEvidenceStrength;
  decision: StoreOpsTriageDecision;
  observationWindow: string;
  needEvidence: boolean;
  demoFastForward: boolean;
  notes: string;
};

export type StoreOpsAssignPayload = StoreOpsWorkflowPayloadBase & {
  ownerRoleId: OperatorRoleId;
  ownerName: string;
  slaDueAt: string;
  handoffNote: string;
};

export type StoreOpsActionType =
  | "staffBriefing"
  | "cleaningCheck"
  | "customerCallback"
  | "iotRestart"
  | "approvalRequest"
  | "remoteRestart";

export type StoreOpsActionPayload = StoreOpsWorkflowPayloadBase & {
  actionType: StoreOpsActionType;
  title: string;
  instructions: string;
  checklistItems: string[];
  needEvidence: boolean;
  requiresApproval: boolean;
  observationWindow: string;
  remoteRestartAuditNote?: string;
};

export type StoreOpsChecklistStatus = "complete" | "partial" | "blocked";

export type StoreOpsFieldReportPayload = StoreOpsWorkflowPayloadBase & {
  reportedBy: string;
  observedAt: string;
  summary: string;
  checklistStatus: StoreOpsChecklistStatus;
  attachmentNames: string[];
  blocker?: string;
};

export type StoreOpsOutcomeStatus = "effective" | "ineffective" | "inconclusive";
export type StoreOpsFollowUpTarget = "storeOps" | "growth" | "network" | "govern";

export type StoreOpsOutcomePayload = StoreOpsWorkflowPayloadBase & {
  outcome: StoreOpsOutcomeStatus;
  impactSummary: string;
  evidenceSummary: string;
  closeIssue: boolean;
  followUpTarget?: StoreOpsFollowUpTarget;
  followUpAction?: string;
};

export type StoreOpsEscalationTarget = "growth" | "network" | "govern";
export type StoreOpsUrgency = "normal" | "high" | "critical";

export type StoreOpsEscalatePayload = StoreOpsWorkflowPayloadBase & {
  target: StoreOpsEscalationTarget;
  urgency: StoreOpsUrgency;
  reason: string;
  requestedOutcome: string;
  notifyOwner: boolean;
};

export type StoreOpsCameraPurposePayload = StoreOpsWorkflowPayloadBase & {
  purpose: string;
  cameraLocation: string;
  timeWindow: string;
  retentionHours: number;
  privacyAcknowledged: boolean;
  auditNote: string;
};

export type StoreOpsReplyChannel = "google" | "customerService";
export type StoreOpsReplyDecision = "approve" | "return" | "reject";

export type StoreOpsReplyReviewPayload = StoreOpsWorkflowPayloadBase & {
  channel: StoreOpsReplyChannel;
  decision: StoreOpsReplyDecision;
  draftReply: string;
  reviewerNote?: string;
  publishAfterApproval: boolean;
};

export type StoreOpsTransferPayload = StoreOpsWorkflowPayloadBase & {
  targetRoleId: OperatorRoleId;
  targetOwnerName: string;
  reason: string;
  handoffNote: string;
  keepWatching: boolean;
};

export type StoreOpsWorkflowPayloadMap = {
  triage: StoreOpsTriagePayload;
  assign: StoreOpsAssignPayload;
  action: StoreOpsActionPayload;
  fieldReport: StoreOpsFieldReportPayload;
  outcome: StoreOpsOutcomePayload;
  escalate: StoreOpsEscalatePayload;
  cameraPurpose: StoreOpsCameraPurposePayload;
  replyReview: StoreOpsReplyReviewPayload;
  transfer: StoreOpsTransferPayload;
};

export type StoreOpsWorkflowSubmitEvent = {
  [DialogType in keyof StoreOpsWorkflowPayloadMap]: {
    type: DialogType;
    payload: StoreOpsWorkflowPayloadMap[DialogType];
  };
}[keyof StoreOpsWorkflowPayloadMap];

export type StoreOpsWorkflowCallbacks = {
  onSubmit?: (event: StoreOpsWorkflowSubmitEvent) => void;
  onTriage?: (payload: StoreOpsTriagePayload) => void;
  onAssign?: (payload: StoreOpsAssignPayload) => void;
  onCreateAction?: (payload: StoreOpsActionPayload) => void;
  onFieldReport?: (payload: StoreOpsFieldReportPayload) => void;
  onOutcome?: (payload: StoreOpsOutcomePayload) => void;
  onEscalate?: (payload: StoreOpsEscalatePayload) => void;
  onCameraPurpose?: (payload: StoreOpsCameraPurposePayload) => void;
  onReplyReview?: (payload: StoreOpsReplyReviewPayload) => void;
  onTransfer?: (payload: StoreOpsTransferPayload) => void;
};

export type StoreOpsWorkflowDialogsProps = {
  activeDialog: StoreOpsWorkflowDialogType | null;
  issue?: StoreOpsWorkflowIssue;
  onClose: () => void;
  callbacks?: StoreOpsWorkflowCallbacks;
};
