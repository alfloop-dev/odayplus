"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { Button, Chip, StatusBadge, type Tone } from "./components";
import { OPERATOR_ROLE_IDS, type OperatorRoleId, type Severity } from "./types";
import { operatorSecurityHeaders } from "./operatorSecurityHeaders";
import styles from "./storeOpsWorkflows.module.css";
import type {
  StoreOpsActionPayload,
  StoreOpsActionType,
  StoreOpsAssignPayload,
  StoreOpsCameraPurposePayload,
  StoreOpsChecklistStatus,
  StoreOpsEscalatePayload,
  StoreOpsEscalationTarget,
  StoreOpsEvidenceStrength,
  StoreOpsFieldReportPayload,
  StoreOpsFollowUpTarget,
  StoreOpsOutcomePayload,
  StoreOpsOutcomeStatus,
  StoreOpsReplyChannel,
  StoreOpsReplyDecision,
  StoreOpsReplyReviewPayload,
  StoreOpsTriageCategory,
  StoreOpsTriageDecision,
  StoreOpsTriagePayload,
  StoreOpsTransferPayload,
  StoreOpsUrgency,
  StoreOpsWorkflowCallbacks,
  StoreOpsWorkflowDialogType,
  StoreOpsWorkflowDialogsProps,
  StoreOpsWorkflowIssue,
  StoreOpsWorkflowPayloadBase,
} from "./storeOpsWorkflowTypes";
import { STORE_OPS_REFRESH_EVENT } from "./storeOpsWorkflowTypes";

export type {
  StoreOpsActionPayload,
  StoreOpsActionType,
  StoreOpsAssignPayload,
  StoreOpsCameraPurposePayload,
  StoreOpsChecklistStatus,
  StoreOpsEscalatePayload,
  StoreOpsEscalationTarget,
  StoreOpsEvidenceStrength,
  StoreOpsFieldReportPayload,
  StoreOpsFollowUpTarget,
  StoreOpsOutcomePayload,
  StoreOpsOutcomeStatus,
  StoreOpsReplyChannel,
  StoreOpsReplyDecision,
  StoreOpsReplyReviewPayload,
  StoreOpsTriageCategory,
  StoreOpsTriageDecision,
  StoreOpsTriagePayload,
  StoreOpsTransferPayload,
  StoreOpsUrgency,
  StoreOpsWorkflowCallbacks,
  StoreOpsWorkflowDialogType,
  StoreOpsWorkflowDialogsProps,
  StoreOpsWorkflowIssue,
  StoreOpsWorkflowPayloadMap,
  StoreOpsWorkflowSubmitEvent,
} from "./storeOpsWorkflowTypes";

const fallbackIssue: StoreOpsWorkflowIssue = {
  id: "ISS-LOCAL-000",
  title: "Local fallback store ops issue",
  storeId: "store-local",
  storeName: "Fallback Store",
  status: "new",
  severity: "medium",
  source: "multiSignal",
  ownerRoleId: "opsLead",
  ownerName: "Store Ops Lead",
  slaDueAt: "2026-07-05 12:00",
  createdAt: "2026-07-05 08:00",
  updatedAt: "2026-07-05 08:20",
  evidenceIds: [],
  summary: "Fallback issue used when Store Ops workflow dialogs render before issue data is connected.",
};

const dialogMeta: Record<StoreOpsWorkflowDialogType, { eyebrow: string; title: string }> = {
  triage: { eyebrow: "Triage", title: "Triage Issue" },
  assign: { eyebrow: "Ownership", title: "Assign Owner" },
  action: { eyebrow: "Execution", title: "Create Action" },
  fieldReport: { eyebrow: "Field Report", title: "Submit Field Report" },
  outcome: { eyebrow: "Outcome", title: "Outcome Review" },
  escalate: { eyebrow: "Escalation", title: "Escalate Issue" },
  cameraPurpose: { eyebrow: "Evidence", title: "Camera Purpose" },
  replyReview: { eyebrow: "Customer Reply", title: "Reply Review" },
  transfer: { eyebrow: "Handoff", title: "Transfer Issue" },
};

const dialogScreenLabels: Record<StoreOpsWorkflowDialogType, string> = {
  triage: "Dialog Triage",
  assign: "Dialog Assign",
  action: "Dialog Create Action",
  fieldReport: "Drawer Field Report",
  outcome: "Dialog Outcome Review",
  escalate: "Dialog Escalate",
  cameraPurpose: "Dialog Camera Purpose",
  replyReview: "Dialog Reply Review",
  transfer: "Dialog Transfer",
};

const roleLabels: Record<OperatorRoleId, string> = {
  opsLead: "Store Ops Lead",
  supportLead: "Support Lead",
  facilitiesLead: "Facilities Lead",
  marketingManager: "Marketing Manager",
  expansionManager: "Expansion Manager",
  auditPm: "PM / Audit",
};

const roleOptions = OPERATOR_ROLE_IDS.map((roleId) => ({ label: roleLabels[roleId], value: roleId }));

const workflowActionEndpoints: Partial<Record<StoreOpsWorkflowDialogType, string>> = {
  triage: "triage",
  assign: "assign",
  action: "actions",
  fieldReport: "field-report",
  outcome: "outcome",
  escalate: "escalate",
  replyReview: "reply-review",
  transfer: "transfer",
};

async function submitStoreOpsWorkflow(
  type: StoreOpsWorkflowDialogType,
  payload: StoreOpsWorkflowPayloadBase & Record<string, unknown>,
) {
  if (typeof window === "undefined") return;

  const roleId = (window.sessionStorage.getItem("oday.operator.role") as OperatorRoleId | null) || "opsLead";
  const correlationId = `corr-store-ops-${Math.random().toString(36).slice(2, 11)}`;
  const idempotencyKey = `idem-store-ops-${payload.issueId}-${type}-${Math.random().toString(36).slice(2, 11)}`;
  const path =
    type === "cameraPurpose"
      ? `/api/v1/operator/store-ops/issues/${payload.issueId}/camera-purpose`
      : workflowActionEndpoints[type]
        ? `/api/v1/operator/store-ops/issues/${payload.issueId}/${workflowActionEndpoints[type]}`
        : "";

  if (!path) return;

  try {
    const response = await fetch(path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
        "X-Correlation-ID": correlationId,
        ...operatorSecurityHeaders(roleId),
      },
      body: JSON.stringify({
        ...payload,
        actorRoleId: roleId,
        actorName: roleLabels[roleId] ?? "Operator",
      }),
    });
    const body = await response.json().catch(() => ({}));
    window.dispatchEvent(
      new CustomEvent(STORE_OPS_REFRESH_EVENT, {
        detail: {
          ok: response.ok,
          status: response.status,
          type,
          issueId: payload.issueId,
          body,
        },
      }),
    );
  } catch (error) {
    window.dispatchEvent(
      new CustomEvent(STORE_OPS_REFRESH_EVENT, {
        detail: {
          ok: false,
          type,
          issueId: payload.issueId,
          error: error instanceof Error ? error.message : "Store Ops API request failed",
        },
      }),
    );
  }
}

const severityOptions: Array<{ label: string; value: Severity }> = [
  { label: "Low", value: "low" },
  { label: "Medium", value: "medium" },
  { label: "High", value: "high" },
  { label: "Critical", value: "critical" },
];

export function StoreOpsWorkflowDialogs({
  activeDialog,
  callbacks,
  issue,
  onClose,
}: StoreOpsWorkflowDialogsProps) {
  if (!activeDialog) {
    return null;
  }

  const effectiveIssue = issue ?? fallbackIssue;
  const meta = dialogMeta[activeDialog];
  const frameClass = activeDialog === "fieldReport" ? styles.drawer : styles.dialog;

  return (
    <div className={styles.overlay} data-screen-label={dialogScreenLabels[activeDialog]}>
      <section
        aria-labelledby={`store-ops-workflow-${activeDialog}-title`}
        className={frameClass}
        role="dialog"
        aria-modal="true"
      >
        <header className={styles.header}>
          <div className={styles.headerTitle}>
            <p className={styles.eyebrow}>{meta.eyebrow}</p>
            <h2 id={`store-ops-workflow-${activeDialog}-title`}>{meta.title}</h2>
          </div>
          <div className={styles.headerActions}>
            <StatusBadge tone={severityTone(effectiveIssue.severity)}>{effectiveIssue.severity}</StatusBadge>
            <Button onClick={onClose} size="sm" variant="ghost">
              Close
            </Button>
          </div>
        </header>

        <IssueContext issue={effectiveIssue} />

        <div className={styles.body}>
          <DialogContent activeDialog={activeDialog} callbacks={callbacks} issue={effectiveIssue} onClose={onClose} />
        </div>
      </section>
    </div>
  );
}

function DialogContent({
  activeDialog,
  callbacks,
  issue,
  onClose,
}: {
  activeDialog: StoreOpsWorkflowDialogType;
  callbacks?: StoreOpsWorkflowCallbacks;
  issue: StoreOpsWorkflowIssue;
  onClose: () => void;
}) {
  const key = `${activeDialog}-${issue.id}`;

  if (activeDialog === "triage") {
    return <TriageForm callbacks={callbacks} issue={issue} key={key} onClose={onClose} />;
  }
  if (activeDialog === "assign") {
    return <AssignForm callbacks={callbacks} issue={issue} key={key} onClose={onClose} />;
  }
  if (activeDialog === "action") {
    return <ActionForm callbacks={callbacks} issue={issue} key={key} onClose={onClose} />;
  }
  if (activeDialog === "fieldReport") {
    return <FieldReportForm callbacks={callbacks} issue={issue} key={key} onClose={onClose} />;
  }
  if (activeDialog === "outcome") {
    return <OutcomeForm callbacks={callbacks} issue={issue} key={key} onClose={onClose} />;
  }
  if (activeDialog === "escalate") {
    return <EscalateForm callbacks={callbacks} issue={issue} key={key} onClose={onClose} />;
  }
  if (activeDialog === "cameraPurpose") {
    return <CameraPurposeForm callbacks={callbacks} issue={issue} key={key} onClose={onClose} />;
  }
  if (activeDialog === "replyReview") {
    return <ReplyReviewForm callbacks={callbacks} issue={issue} key={key} onClose={onClose} />;
  }
  return <TransferForm callbacks={callbacks} issue={issue} key={key} onClose={onClose} />;
}

function IssueContext({ issue }: { issue: StoreOpsWorkflowIssue }) {
  return (
    <div className={styles.issueContext}>
      <div className={styles.issueText}>
        <span>
          {issue.id} / {issue.storeName}
        </span>
        <strong>{issue.title}</strong>
        <p>{issue.summary}</p>
      </div>
      <div className={styles.issueBadges}>
        <Chip tone="info">{issue.status}</Chip>
        <Chip>{issue.source}</Chip>
      </div>
    </div>
  );
}

type WorkflowFormProps = {
  callbacks?: StoreOpsWorkflowCallbacks;
  issue: StoreOpsWorkflowIssue;
  onClose: () => void;
};

function TriageForm({ callbacks, issue, onClose }: WorkflowFormProps) {
  const [severity, setSeverity] = useState<Severity>(issue.severity);
  const [category, setCategory] = useState<StoreOpsTriageCategory>("multiSignal");
  const [evidenceStrength, setEvidenceStrength] = useState<StoreOpsEvidenceStrength>("usable");
  const [decision, setDecision] = useState<StoreOpsTriageDecision>("accept");
  const [observationWindow, setObservationWindow] = useState("2 hours");
  const [needEvidence, setNeedEvidence] = useState(false);
  const [demoFastForward, setDemoFastForward] = useState(false);
  const [notes, setNotes] = useState(issue.summary);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload: StoreOpsTriagePayload = {
      ...basePayload(issue),
      severity,
      category,
      evidenceStrength,
      decision,
      observationWindow: observationWindow.trim(),
      needEvidence,
      demoFastForward,
      notes: notes.trim(),
    };
    callbacks?.onTriage?.(payload);
    callbacks?.onSubmit?.({ type: "triage", payload });
    void submitStoreOpsWorkflow("triage", payload);
    onClose();
  }

  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.grid}>
        <SelectField<Severity> label="Severity" onChange={setSeverity} options={severityOptions} value={severity} />
        <SelectField<StoreOpsTriageCategory>
          label="Category"
          onChange={setCategory}
          options={[
            { label: "Service", value: "service" },
            { label: "Cleanliness", value: "cleanliness" },
            { label: "Staffing", value: "staffing" },
            { label: "Device", value: "device" },
            { label: "Payment", value: "payment" },
            { label: "Multi-signal", value: "multiSignal" },
          ]}
          value={category}
        />
        <SelectField<StoreOpsEvidenceStrength>
          label="Evidence strength"
          onChange={setEvidenceStrength}
          options={[
            { label: "Weak", value: "weak" },
            { label: "Usable", value: "usable" },
            { label: "Strong", value: "strong" },
          ]}
          value={evidenceStrength}
        />
        <SelectField<StoreOpsTriageDecision>
          label="Decision"
          onChange={setDecision}
          options={[
            { label: "Accept triage", value: "accept" },
            { label: "Need evidence", value: "needEvidence" },
            { label: "Demo fast-forward", value: "fastForward" },
          ]}
          value={decision}
        />
        <TextField label="Observation window" onChange={setObservationWindow} value={observationWindow} />
        <TextAreaField className={styles.fullWidth} label="Triage notes" onChange={setNotes} value={notes} />
      </div>
      <div className={styles.checkGrid}>
        <CheckboxField
          checked={needEvidence}
          description="Return the issue to waiting evidence before assignment."
          label="Need evidence"
          onChange={setNeedEvidence}
        />
        <CheckboxField
          checked={demoFastForward}
          description="Allow the demo shell to move past observation timers."
          label="Demo fast-forward"
          onChange={setDemoFastForward}
        />
      </div>
      <DialogActions onCancel={onClose} primaryLabel="Submit Triage" />
    </form>
  );
}

function AssignForm({ callbacks, issue, onClose }: WorkflowFormProps) {
  const [ownerRoleId, setOwnerRoleId] = useState<OperatorRoleId>(issue.ownerRoleId);
  const [ownerName, setOwnerName] = useState(issue.ownerName);
  const [slaDueAt, setSlaDueAt] = useState(issue.slaDueAt);
  const [handoffNote, setHandoffNote] = useState(`Take ownership of ${issue.id} and confirm next action.`);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload: StoreOpsAssignPayload = {
      ...basePayload(issue),
      ownerRoleId,
      ownerName: ownerName.trim(),
      slaDueAt: slaDueAt.trim(),
      handoffNote: handoffNote.trim(),
    };
    callbacks?.onAssign?.(payload);
    callbacks?.onSubmit?.({ type: "assign", payload });
    void submitStoreOpsWorkflow("assign", payload);
    onClose();
  }

  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.grid}>
        <SelectField<OperatorRoleId> label="Owner role" onChange={setOwnerRoleId} options={roleOptions} value={ownerRoleId} />
        <TextField label="Owner name" onChange={setOwnerName} required value={ownerName} />
        <TextField label="SLA due" onChange={setSlaDueAt} required value={slaDueAt} />
        <TextAreaField className={styles.fullWidth} label="Handoff note" onChange={setHandoffNote} value={handoffNote} />
      </div>
      <DialogActions onCancel={onClose} primaryLabel="Assign Owner" />
    </form>
  );
}

function ActionForm({ callbacks, issue, onClose }: WorkflowFormProps) {
  const [actionType, setActionType] = useState<StoreOpsActionType>("cleaningCheck");
  const [title, setTitle] = useState(`Resolve ${issue.title}`);
  const [instructions, setInstructions] = useState("Complete the field action and attach evidence if available.");
  const [checklistItems, setChecklistItems] = useState("Confirm owner\nComplete action\nAttach evidence");
  const [needEvidence, setNeedEvidence] = useState(true);
  const [requiresApproval, setRequiresApproval] = useState(false);
  const [observationWindow, setObservationWindow] = useState("4 hours");
  const [remoteRestartAuditNote, setRemoteRestartAuditNote] = useState("");
  const [error, setError] = useState("");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const auditNote = remoteRestartAuditNote.trim();
    if (actionType === "remoteRestart" && !auditNote) {
      setError("Remote restart requires an audit note.");
      return;
    }

    const payload: StoreOpsActionPayload = {
      ...basePayload(issue),
      actionType,
      title: title.trim(),
      instructions: instructions.trim(),
      checklistItems: splitList(checklistItems),
      needEvidence,
      requiresApproval,
      observationWindow: observationWindow.trim(),
      remoteRestartAuditNote: auditNote || undefined,
    };
    callbacks?.onCreateAction?.(payload);
    callbacks?.onSubmit?.({ type: "action", payload });
    void submitStoreOpsWorkflow("action", payload);
    onClose();
  }

  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.grid}>
        <SelectField
          label="Action type"
          onChange={(value) => {
            setActionType(value);
            setError("");
          }}
          options={[
            { label: "Staff briefing", value: "staffBriefing" },
            { label: "Cleaning check", value: "cleaningCheck" },
            { label: "Customer callback", value: "customerCallback" },
            { label: "IoT restart", value: "iotRestart" },
            { label: "Approval request", value: "approvalRequest" },
            { label: "Remote restart", value: "remoteRestart" },
          ]}
          value={actionType}
        />
        <TextField label="Observation window" onChange={setObservationWindow} value={observationWindow} />
        <TextField className={styles.fullWidth} label="Action title" onChange={setTitle} required value={title} />
        <TextAreaField className={styles.fullWidth} label="Instructions" onChange={setInstructions} value={instructions} />
        <TextAreaField
          className={styles.fullWidth}
          hint="One item per line."
          label="Checklist"
          onChange={setChecklistItems}
          value={checklistItems}
        />
        <TextAreaField
          className={styles.fullWidth}
          hint="Required when action type is Remote restart."
          label="Remote restart audit note"
          onChange={(value) => {
            setRemoteRestartAuditNote(value);
            setError("");
          }}
          value={remoteRestartAuditNote}
        />
      </div>
      <div className={styles.checkGrid}>
        <CheckboxField
          checked={needEvidence}
          description="Keep the action open until proof is attached."
          label="Need evidence"
          onChange={setNeedEvidence}
        />
        <CheckboxField
          checked={requiresApproval}
          description="Route completion to Govern before closure."
          label="Approval request"
          onChange={setRequiresApproval}
        />
      </div>
      {error ? <div className={styles.error}>{error}</div> : null}
      <DialogActions onCancel={onClose} primaryLabel="Create Action" />
    </form>
  );
}

function FieldReportForm({ callbacks, issue, onClose }: WorkflowFormProps) {
  const [reportedBy, setReportedBy] = useState(issue.ownerName);
  const [observedAt, setObservedAt] = useState(issue.updatedAt);
  const [summary, setSummary] = useState("");
  const [checklistStatus, setChecklistStatus] = useState<StoreOpsChecklistStatus>("complete");
  const [attachmentNames, setAttachmentNames] = useState("field-photo-placeholder.jpg");
  const [blocker, setBlocker] = useState("");
  const [error, setError] = useState("");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedBlocker = blocker.trim();
    if (checklistStatus === "blocked" && !trimmedBlocker) {
      setError("Blocked field reports require a blocker note.");
      return;
    }

    const payload: StoreOpsFieldReportPayload = {
      ...basePayload(issue),
      reportedBy: reportedBy.trim(),
      observedAt: observedAt.trim(),
      summary: summary.trim(),
      checklistStatus,
      attachmentNames: splitList(attachmentNames),
      blocker: trimmedBlocker || undefined,
    };
    callbacks?.onFieldReport?.(payload);
    callbacks?.onSubmit?.({ type: "fieldReport", payload });
    void submitStoreOpsWorkflow("fieldReport", payload);
    onClose();
  }

  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.grid}>
        <TextField label="Reported by" onChange={setReportedBy} required value={reportedBy} />
        <TextField label="Observed at" onChange={setObservedAt} required value={observedAt} />
        <SelectField
          label="Checklist status"
          onChange={(value) => {
            setChecklistStatus(value);
            setError("");
          }}
          options={[
            { label: "Complete", value: "complete" },
            { label: "Partial", value: "partial" },
            { label: "Blocked", value: "blocked" },
          ]}
          value={checklistStatus}
        />
        <TextAreaField
          className={styles.fullWidth}
          hint="Placeholder filenames are OK for this slice."
          label="Attachments"
          onChange={setAttachmentNames}
          value={attachmentNames}
        />
        <TextAreaField className={styles.fullWidth} label="Report summary" onChange={setSummary} required value={summary} />
        <TextAreaField
          className={styles.fullWidth}
          hint="Required when checklist status is blocked."
          label="Blocker"
          onChange={(value) => {
            setBlocker(value);
            setError("");
          }}
          value={blocker}
        />
      </div>
      {error ? <div className={styles.error}>{error}</div> : null}
      <DialogActions onCancel={onClose} primaryLabel="Submit Report" />
    </form>
  );
}

function OutcomeForm({ callbacks, issue, onClose }: WorkflowFormProps) {
  const [outcome, setOutcome] = useState<StoreOpsOutcomeStatus>("effective");
  const [impactSummary, setImpactSummary] = useState("");
  const [evidenceSummary, setEvidenceSummary] = useState("");
  const [followUpTarget, setFollowUpTarget] = useState<StoreOpsFollowUpTarget>("storeOps");
  const [followUpAction, setFollowUpAction] = useState("");
  const [closeIssue, setCloseIssue] = useState(true);
  const [error, setError] = useState("");
  const needsFollowUp = outcome === "ineffective" || outcome === "inconclusive";

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedFollowUp = followUpAction.trim();
    if (needsFollowUp && !trimmedFollowUp) {
      setError("Ineffective or inconclusive outcomes require a follow-up action.");
      return;
    }

    const payload: StoreOpsOutcomePayload = {
      ...basePayload(issue),
      outcome,
      impactSummary: impactSummary.trim(),
      evidenceSummary: evidenceSummary.trim(),
      closeIssue: needsFollowUp ? false : closeIssue,
      followUpTarget: needsFollowUp ? followUpTarget : undefined,
      followUpAction: trimmedFollowUp || undefined,
    };
    callbacks?.onOutcome?.(payload);
    callbacks?.onSubmit?.({ type: "outcome", payload });
    void submitStoreOpsWorkflow("outcome", payload);
    onClose();
  }

  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.grid}>
        <SelectField
          label="Outcome"
          onChange={(value) => {
            setOutcome(value);
            setError("");
          }}
          options={[
            { label: "Effective", value: "effective" },
            { label: "Ineffective", value: "ineffective" },
            { label: "Inconclusive", value: "inconclusive" },
          ]}
          value={outcome}
        />
        <SelectField<StoreOpsFollowUpTarget>
          label="Follow-up target"
          onChange={setFollowUpTarget}
          options={[
            { label: "Store Ops", value: "storeOps" },
            { label: "Growth", value: "growth" },
            { label: "Network", value: "network" },
            { label: "Govern", value: "govern" },
          ]}
          value={followUpTarget}
        />
        <TextAreaField className={styles.fullWidth} label="Impact summary" onChange={setImpactSummary} required value={impactSummary} />
        <TextAreaField className={styles.fullWidth} label="Evidence summary" onChange={setEvidenceSummary} value={evidenceSummary} />
        <TextAreaField
          className={styles.fullWidth}
          hint="Required for ineffective or inconclusive outcomes."
          label="Follow-up action"
          onChange={(value) => {
            setFollowUpAction(value);
            setError("");
          }}
          value={followUpAction}
        />
      </div>
      <CheckboxField
        checked={closeIssue}
        description={needsFollowUp ? "Disabled in payload until follow-up is resolved." : "Allow callback owner to close the issue."}
        label="Close issue after review"
        onChange={setCloseIssue}
      />
      {error ? <div className={styles.error}>{error}</div> : null}
      <DialogActions onCancel={onClose} primaryLabel="Submit Outcome" />
    </form>
  );
}

function EscalateForm({ callbacks, issue, onClose }: WorkflowFormProps) {
  const [target, setTarget] = useState<StoreOpsEscalationTarget>("growth");
  const [urgency, setUrgency] = useState<StoreOpsUrgency>("high");
  const [reason, setReason] = useState("");
  const [requestedOutcome, setRequestedOutcome] = useState("Create downstream review object and return recommendation.");
  const [notifyOwner, setNotifyOwner] = useState(true);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload: StoreOpsEscalatePayload = {
      ...basePayload(issue),
      target,
      urgency,
      reason: reason.trim(),
      requestedOutcome: requestedOutcome.trim(),
      notifyOwner,
    };
    callbacks?.onEscalate?.(payload);
    callbacks?.onSubmit?.({ type: "escalate", payload });
    void submitStoreOpsWorkflow("escalate", payload);
    onClose();
  }

  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.grid}>
        <SelectField<StoreOpsEscalationTarget>
          label="Target workspace"
          onChange={setTarget}
          options={[
            { label: "Growth", value: "growth" },
            { label: "Network", value: "network" },
            { label: "Govern", value: "govern" },
          ]}
          value={target}
        />
        <SelectField<StoreOpsUrgency>
          label="Urgency"
          onChange={setUrgency}
          options={[
            { label: "Normal", value: "normal" },
            { label: "High", value: "high" },
            { label: "Critical", value: "critical" },
          ]}
          value={urgency}
        />
        <TextAreaField className={styles.fullWidth} label="Escalation reason" onChange={setReason} required value={reason} />
        <TextAreaField
          className={styles.fullWidth}
          label="Requested outcome"
          onChange={setRequestedOutcome}
          required
          value={requestedOutcome}
        />
      </div>
      <CheckboxField
        checked={notifyOwner}
        description="Include current owner in the callback payload."
        label="Notify current owner"
        onChange={setNotifyOwner}
      />
      <DialogActions onCancel={onClose} primaryLabel="Escalate" />
    </form>
  );
}

function CameraPurposeForm({ callbacks, issue, onClose }: WorkflowFormProps) {
  const [purpose, setPurpose] = useState("");
  const [cameraLocation, setCameraLocation] = useState(`${issue.storeName} counter camera`);
  const [timeWindow, setTimeWindow] = useState("Last 30 minutes");
  const [retentionHours, setRetentionHours] = useState(24);
  const [privacyAcknowledged, setPrivacyAcknowledged] = useState(false);
  const [auditNote, setAuditNote] = useState(`Purpose review for ${issue.id}`);
  const [error, setError] = useState("");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedPurpose = purpose.trim();
    if (!trimmedPurpose) {
      setError("Camera purpose is required.");
      return;
    }
    if (!privacyAcknowledged) {
      setError("Privacy and audit warning must be acknowledged.");
      return;
    }

    const payload: StoreOpsCameraPurposePayload = {
      ...basePayload(issue),
      purpose: trimmedPurpose,
      cameraLocation: cameraLocation.trim(),
      timeWindow: timeWindow.trim(),
      retentionHours,
      privacyAcknowledged,
      auditNote: auditNote.trim(),
    };
    callbacks?.onCameraPurpose?.(payload);
    callbacks?.onSubmit?.({ type: "cameraPurpose", payload });
    void submitStoreOpsWorkflow("cameraPurpose", payload);
    onClose();
  }

  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.warning}>
        Camera evidence is privacy-scoped. Opening footage requires a declared purpose and will be written to the
        audit trail with actor, issue, time window, and retention metadata.
      </div>
      <div className={styles.grid}>
        <TextAreaField
          className={styles.fullWidth}
          hint="Required before any camera evidence can be opened."
          label="Purpose"
          onChange={(value) => {
            setPurpose(value);
            setError("");
          }}
          required
          value={purpose}
        />
        <TextField label="Camera location" onChange={setCameraLocation} required value={cameraLocation} />
        <TextField label="Time window" onChange={setTimeWindow} required value={timeWindow} />
        <NumberField label="Retention hours" max={72} min={1} onChange={setRetentionHours} value={retentionHours} />
        <TextAreaField className={styles.fullWidth} label="Audit note" onChange={setAuditNote} value={auditNote} />
      </div>
      <CheckboxField
        checked={privacyAcknowledged}
        description="I understand this access is logged and purpose-limited."
        label="Acknowledge privacy and audit warning"
        onChange={(value) => {
          setPrivacyAcknowledged(value);
          setError("");
        }}
      />
      {error ? <div className={styles.error}>{error}</div> : null}
      <DialogActions onCancel={onClose} primaryLabel="Record Purpose" />
    </form>
  );
}

function ReplyReviewForm({ callbacks, issue, onClose }: WorkflowFormProps) {
  const [channel, setChannel] = useState<StoreOpsReplyChannel>("google");
  const [decision, setDecision] = useState<StoreOpsReplyDecision>("approve");
  const [draftReply, setDraftReply] = useState("Thank you for flagging this. The store team has reviewed the issue and taken action.");
  const [reviewerNote, setReviewerNote] = useState("");
  const [publishAfterApproval, setPublishAfterApproval] = useState(false);
  const [error, setError] = useState("");
  const needsReason = decision === "return" || decision === "reject";

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedNote = reviewerNote.trim();
    if (needsReason && !trimmedNote) {
      setError("Return or reject requires a reviewer note.");
      return;
    }

    const payload: StoreOpsReplyReviewPayload = {
      ...basePayload(issue),
      channel,
      decision,
      draftReply: draftReply.trim(),
      reviewerNote: trimmedNote || undefined,
      publishAfterApproval,
    };
    callbacks?.onReplyReview?.(payload);
    callbacks?.onSubmit?.({ type: "replyReview", payload });
    void submitStoreOpsWorkflow("replyReview", payload);
    onClose();
  }

  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.grid}>
        <SelectField<StoreOpsReplyChannel>
          label="Reply channel"
          onChange={setChannel}
          options={[
            { label: "Google review", value: "google" },
            { label: "Customer service", value: "customerService" },
          ]}
          value={channel}
        />
        <SelectField
          label="Decision"
          onChange={(value) => {
            setDecision(value);
            setError("");
          }}
          options={[
            { label: "Approve", value: "approve" },
            { label: "Return", value: "return" },
            { label: "Reject", value: "reject" },
          ]}
          value={decision}
        />
        <TextAreaField className={styles.fullWidth} label="Draft reply" onChange={setDraftReply} required value={draftReply} />
        <TextAreaField
          className={styles.fullWidth}
          hint="Required for return or reject."
          label="Reviewer note"
          onChange={(value) => {
            setReviewerNote(value);
            setError("");
          }}
          value={reviewerNote}
        />
      </div>
      <CheckboxField
        checked={publishAfterApproval}
        description="Callback payload marks this as ready for Google reply publish."
        label="Publish after approval"
        onChange={setPublishAfterApproval}
      />
      {error ? <div className={styles.error}>{error}</div> : null}
      <DialogActions onCancel={onClose} primaryLabel="Submit Review" />
    </form>
  );
}

function TransferForm({ callbacks, issue, onClose }: WorkflowFormProps) {
  const [targetRoleId, setTargetRoleId] = useState<OperatorRoleId>("supportLead");
  const [targetOwnerName, setTargetOwnerName] = useState("");
  const [reason, setReason] = useState("");
  const [handoffNote, setHandoffNote] = useState(issue.summary);
  const [keepWatching, setKeepWatching] = useState(true);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload: StoreOpsTransferPayload = {
      ...basePayload(issue),
      targetRoleId,
      targetOwnerName: targetOwnerName.trim(),
      reason: reason.trim(),
      handoffNote: handoffNote.trim(),
      keepWatching,
    };
    callbacks?.onTransfer?.(payload);
    callbacks?.onSubmit?.({ type: "transfer", payload });
    void submitStoreOpsWorkflow("transfer", payload);
    onClose();
  }

  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.grid}>
        <SelectField<OperatorRoleId> label="Target role" onChange={setTargetRoleId} options={roleOptions} value={targetRoleId} />
        <TextField label="Target owner" onChange={setTargetOwnerName} required value={targetOwnerName} />
        <TextAreaField className={styles.fullWidth} label="Transfer reason" onChange={setReason} required value={reason} />
        <TextAreaField className={styles.fullWidth} label="Handoff note" onChange={setHandoffNote} value={handoffNote} />
      </div>
      <CheckboxField
        checked={keepWatching}
        description="Keep the source team subscribed to updates."
        label="Keep watching"
        onChange={setKeepWatching}
      />
      <DialogActions onCancel={onClose} primaryLabel="Transfer" />
    </form>
  );
}

function DialogActions({ onCancel, primaryLabel }: { onCancel: () => void; primaryLabel: string }) {
  return (
    <div className={styles.actions}>
      <Button onClick={onCancel} variant="secondary">
        取消
      </Button>
      <Button type="submit" variant="primary">
        {primaryLabel}
      </Button>
    </div>
  );
}

function TextField({
  className,
  hint,
  label,
  onChange,
  required,
  value,
}: {
  className?: string;
  hint?: string;
  label: string;
  onChange: (value: string) => void;
  required?: boolean;
  value: string;
}) {
  return (
    <label className={className ? `${styles.field} ${className}` : styles.field}>
      <span>{label}</span>
      <input onChange={(event) => onChange(event.target.value)} required={required} value={value} />
      {hint ? <small>{hint}</small> : null}
    </label>
  );
}

function NumberField({
  label,
  max,
  min,
  onChange,
  value,
}: {
  label: string;
  max?: number;
  min?: number;
  onChange: (value: number) => void;
  value: number;
}) {
  return (
    <label className={styles.field}>
      <span>{label}</span>
      <input
        max={max}
        min={min}
        onChange={(event) => onChange(Number(event.target.value))}
        required
        type="number"
        value={value}
      />
    </label>
  );
}

function TextAreaField({
  className,
  hint,
  label,
  onChange,
  required,
  value,
}: {
  className?: string;
  hint?: string;
  label: string;
  onChange: (value: string) => void;
  required?: boolean;
  value: string;
}) {
  return (
    <label className={className ? `${styles.field} ${className}` : styles.field}>
      <span>{label}</span>
      <textarea onChange={(event) => onChange(event.target.value)} required={required} value={value} />
      {hint ? <small>{hint}</small> : null}
    </label>
  );
}

function SelectField<TValue extends string>({
  label,
  onChange,
  options,
  value,
}: {
  label: string;
  onChange: (value: TValue) => void;
  options: Array<{ label: string; value: TValue }>;
  value: TValue;
}) {
  return (
    <label className={styles.field}>
      <span>{label}</span>
      <select onChange={(event) => onChange(event.target.value as TValue)} value={value}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function CheckboxField({
  checked,
  description,
  label,
  onChange,
}: {
  checked: boolean;
  description?: string;
  label: string;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className={styles.checkbox}>
      <input checked={checked} onChange={(event) => onChange(event.target.checked)} type="checkbox" />
      <span>
        {label}
        {description ? <small>{description}</small> : null}
      </span>
    </label>
  );
}

function basePayload(issue: StoreOpsWorkflowIssue): StoreOpsWorkflowPayloadBase {
  return {
    issueId: issue.id,
    issueTitle: issue.title,
    storeId: issue.storeId,
    storeName: issue.storeName,
  };
}

function splitList(value: string) {
  return value
    .split(/\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function severityTone(severity: Severity): Tone {
  if (severity === "critical") {
    return "danger";
  }
  if (severity === "high") {
    return "warning";
  }
  if (severity === "medium") {
    return "info";
  }
  return "neutral";
}
