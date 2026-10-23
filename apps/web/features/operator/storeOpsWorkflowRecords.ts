import { canonicalStoredReviewJSON, nativeSubmissionStrip, validStoredReviewSource, type ReviewHistory, type StoredReviewSource } from "./storeOpsPersistedReviews";
/** Exact admitted native v3 typed manual submissions; all operational truth flags remain unexecuted/unverified. */
export type WorkflowSubmissions = {
  "triage": { severity: "low" | "medium" | "high" | "critical" | null; category: "service" | "cleanliness" | "staffing" | "device" | "payment" | "multiSignal" | null; evidenceStrength: "weak" | "usable" | "strong" | null; decision: "accept" | "needEvidence" | "fastForward" | null; observationWindow: string | null; needEvidence: boolean | null; demoFastForward: boolean | null; notes: string | null };
  "assign": { ownerRoleId: string | null; ownerName: string | null; slaDueAt: string | null; handoffNote: string | null };
  "actions": { actionType: "staffBriefing" | "cleaningCheck" | "customerCallback" | "iotRestart" | "approvalRequest" | "remoteRestart" | null; title: string | null; instructions: string | null; checklistItems: string[] | null; needEvidence: boolean | null; requiresApproval: boolean | null; observationWindow: string | null; remoteRestartAuditNote: string | null };
  "field-report": { reportedBy: string | null; observedAt: string | null; summary: string | null; checklistStatus: "complete" | "partial" | "blocked" | null; attachmentNames: string[] | null; blocker: string | null };
  "escalate": { target: "growth" | "network" | "govern" | null; urgency: "normal" | "high" | "critical" | null; reason: string | null; requestedOutcome: string | null; notifyOwner: boolean | null };
  "transfer": { targetRoleId: string | null; targetOwnerName: string | null; reason: string | null; handoffNote: string | null; keepWatching: boolean | null };
  "camera-purpose": { purpose: string; cameraLocation: string | null; timeWindow: string | null; retentionHours: number | null; privacyAcknowledged: boolean | null; auditNote: string | null };
};
export type WorkflowFlags = {
  "triage": { clockAdvanced: false };
  "assign": { assigneeIdentityStatus: "not_verified" };
  "actions": { executionStatus: "not_executed"; approvalRequestStatus: "not_created" };
  "field-report": { reporterIdentityStatus: "not_verified"; attachmentEvidenceStatus: "not_verified" };
  "escalate": { notificationStatus: "not_sent"; routingStatus: "not_dispatched" };
  "transfer": { assigneeIdentityStatus: "not_verified"; watchSubscriptionStatus: "not_created" };
  "camera-purpose": {  };
};
export type WorkflowAction = keyof WorkflowSubmissions;
export type WorkflowRecord = { [A in WorkflowAction]: StoredReviewSource & { action: A; submission: WorkflowSubmissions[A] } & WorkflowFlags[A] }[WorkflowAction];
type FieldRule = { kind: string; nullable: boolean; values?: readonly unknown[] };
export const WORKFLOW_RECORD_RULES: Record<WorkflowAction, { fields: Record<string, FieldRule>; flags: Record<string, unknown> }> = {
  "triage": {
    "fields": {
      "severity": {
        "kind": "enum",
        "values": [
          "low",
          "medium",
          "high",
          "critical"
        ],
        "nullable": true
      },
      "category": {
        "kind": "enum",
        "values": [
          "service",
          "cleanliness",
          "staffing",
          "device",
          "payment",
          "multiSignal"
        ],
        "nullable": true
      },
      "evidenceStrength": {
        "kind": "enum",
        "values": [
          "weak",
          "usable",
          "strong"
        ],
        "nullable": true
      },
      "decision": {
        "kind": "enum",
        "values": [
          "accept",
          "needEvidence",
          "fastForward"
        ],
        "nullable": true
      },
      "observationWindow": {
        "kind": "string",
        "nullable": true
      },
      "needEvidence": {
        "kind": "boolean",
        "nullable": true
      },
      "demoFastForward": {
        "kind": "boolean",
        "nullable": true
      },
      "notes": {
        "kind": "string",
        "nullable": true
      }
    },
    "flags": {
      "clockAdvanced": false
    }
  },
  "assign": {
    "fields": {
      "ownerRoleId": {
        "kind": "string",
        "nullable": true
      },
      "ownerName": {
        "kind": "string",
        "nullable": true
      },
      "slaDueAt": {
        "kind": "string",
        "nullable": true
      },
      "handoffNote": {
        "kind": "string",
        "nullable": true
      }
    },
    "flags": {
      "assigneeIdentityStatus": "not_verified"
    }
  },
  "actions": {
    "fields": {
      "actionType": {
        "kind": "enum",
        "values": [
          "staffBriefing",
          "cleaningCheck",
          "customerCallback",
          "iotRestart",
          "approvalRequest",
          "remoteRestart"
        ],
        "nullable": true
      },
      "title": {
        "kind": "string",
        "nullable": true
      },
      "instructions": {
        "kind": "string",
        "nullable": true
      },
      "checklistItems": {
        "kind": "list",
        "nullable": true
      },
      "needEvidence": {
        "kind": "boolean",
        "nullable": true
      },
      "requiresApproval": {
        "kind": "boolean",
        "nullable": true
      },
      "observationWindow": {
        "kind": "string",
        "nullable": true
      },
      "remoteRestartAuditNote": {
        "kind": "string",
        "nullable": true
      }
    },
    "flags": {
      "executionStatus": "not_executed",
      "approvalRequestStatus": "not_created"
    }
  },
  "field-report": {
    "fields": {
      "reportedBy": {
        "kind": "string",
        "nullable": true
      },
      "observedAt": {
        "kind": "string",
        "nullable": true
      },
      "summary": {
        "kind": "string",
        "nullable": true
      },
      "checklistStatus": {
        "kind": "enum",
        "values": [
          "complete",
          "partial",
          "blocked"
        ],
        "nullable": true
      },
      "attachmentNames": {
        "kind": "list",
        "nullable": true
      },
      "blocker": {
        "kind": "string",
        "nullable": true
      }
    },
    "flags": {
      "reporterIdentityStatus": "not_verified",
      "attachmentEvidenceStatus": "not_verified"
    }
  },
  "escalate": {
    "fields": {
      "target": {
        "kind": "enum",
        "values": [
          "growth",
          "network",
          "govern"
        ],
        "nullable": true
      },
      "urgency": {
        "kind": "enum",
        "values": [
          "normal",
          "high",
          "critical"
        ],
        "nullable": true
      },
      "reason": {
        "kind": "string",
        "nullable": true
      },
      "requestedOutcome": {
        "kind": "string",
        "nullable": true
      },
      "notifyOwner": {
        "kind": "boolean",
        "nullable": true
      }
    },
    "flags": {
      "notificationStatus": "not_sent",
      "routingStatus": "not_dispatched"
    }
  },
  "transfer": {
    "fields": {
      "targetRoleId": {
        "kind": "string",
        "nullable": true
      },
      "targetOwnerName": {
        "kind": "string",
        "nullable": true
      },
      "reason": {
        "kind": "string",
        "nullable": true
      },
      "handoffNote": {
        "kind": "string",
        "nullable": true
      },
      "keepWatching": {
        "kind": "boolean",
        "nullable": true
      }
    },
    "flags": {
      "assigneeIdentityStatus": "not_verified",
      "watchSubscriptionStatus": "not_created"
    }
  },
  "camera-purpose": {
    "fields": {
      "purpose": {
        "kind": "string",
        "nullable": false
      },
      "cameraLocation": {
        "kind": "string",
        "nullable": true
      },
      "timeWindow": {
        "kind": "string",
        "nullable": true
      },
      "retentionHours": {
        "kind": "integer",
        "nullable": true
      },
      "privacyAcknowledged": {
        "kind": "boolean",
        "nullable": true
      },
      "auditNote": {
        "kind": "string",
        "nullable": true
      }
    },
    "flags": {}
  }
};
type Value = Record<string, unknown>;
const object = (v: unknown): v is Value => !!v && typeof v === "object" && !Array.isArray(v);
const exact = (v: Value, keys: string[]) => Object.keys(v).length === keys.length && keys.every(key => Object.hasOwn(v, key));
const sourceKeys = ["id", "issueId", "storeId", "actorSubjectId", "actorName", "actorRoleId", "actorEvidence", "recordedAt", "correlationId", "workflowAuditId", "auditEventId", "sourceIssueUpdatedAt", "evidenceIds", "submission", "action"];
const dialogActions: Record<string, WorkflowAction> = { triage: "triage", assign: "assign", action: "actions", fieldReport: "field-report", escalate: "escalate", transfer: "transfer", cameraPurpose: "camera-purpose" };
export const workflowActionForDialog = (dialog: string): WorkflowAction | null => dialogActions[dialog] ?? null;
function field(value: unknown, rule: FieldRule): boolean {
  if (value === null) return rule.nullable;
  if (rule.kind === "enum") return rule.values!.some(v => typeof value === typeof v && value === v);
  if (rule.kind === "string") return typeof value === "string";
  if (rule.kind === "boolean") return typeof value === "boolean";
  if (rule.kind === "integer") return typeof value === "number" && Number.isSafeInteger(value) && value >= 1 && value <= 72;
  return Array.isArray(value) && value.every(v => typeof v === "string");
}
function valid(v: unknown, owner: { id: string; storeId: string }): v is WorkflowRecord {
  if (!object(v) || typeof v.action !== "string" || !Object.hasOwn(WORKFLOW_RECORD_RULES, v.action)) return false;
  const rules = WORKFLOW_RECORD_RULES[v.action as WorkflowAction];
  if (!exact(v, [...sourceKeys, ...Object.keys(rules.flags)]) || !validStoredReviewSource(v, owner)
    || !Object.entries(rules.flags).every(([key, value]) => v[key] === value) || !object(v.submission)
    || !exact(v.submission, Object.keys(rules.fields))) return false;
  const submission = v.submission;
  if (!Object.entries(rules.fields).every(([key, rule]) => field(submission[key], rule))) return false;
  return v.action !== "camera-purpose" || typeof submission.purpose === "string" && nativeSubmissionStrip(submission.purpose).length > 0;
}
export function latestWorkflowRecord<A extends WorkflowAction>(issue: { id: string; storeId: string; workflowRecords?: unknown }, action: A): ReviewHistory<Extract<WorkflowRecord, { action: A }>> {
  if (issue.workflowRecords === undefined) return { status: "absent", latest: null };
  if (!Array.isArray(issue.workflowRecords)) return { status: "unavailable", latest: null };
  const seen = new Set<string>(); const matches: WorkflowRecord[] = [];
  for (const row of issue.workflowRecords) {
    if (!valid(row, issue) || seen.has(row.id)) return { status: "unavailable", latest: null };
    seen.add(row.id); if (row.action === action) matches.push(row);
  }
  return matches.length ? { status: "available", latest: matches[matches.length - 1] as Extract<WorkflowRecord, { action: A }> } : { status: "absent", latest: null };
}
export function normalizeWorkflowSubmission(action: WorkflowAction, payload: Value): Value {
  return Object.fromEntries(Object.entries(WORKFLOW_RECORD_RULES[action].fields).map(([key, rule]) => {
    const value = payload[key] ?? null;
    return [key, typeof value === "string" && rule.kind === "string" ? nativeSubmissionStrip(value)
      : Array.isArray(value) && rule.kind === "list" ? value.map(v => typeof v === "string" ? nativeSubmissionStrip(v) : v) : value];
  }));
}
/** Business timestamp text stays exactly native; no timezone or calendar is inferred here. */
export const hasExplicitWorkflowOffset = (v: string) => {
  const match = /(Z|[+-]\d{2}(?::?\d{2})?(?::?\d{2})?(?:[.,]\d+)?)$/.exec(v.trim());
  // Presence only: the native parser validates the complete original calendar and offset.
  return !!match && match.index >= 10;
};
export async function confirmsWorkflowRecord(body: unknown, action: WorkflowAction, payload: Value, requestCID: string, idempotencyKey: string): Promise<boolean> {
  if (!object(body) || !object(body.issue) || body.issue.id !== payload.issueId || body.issue.storeId !== payload.storeId
    || typeof body.idempotentReplay !== "boolean" || !object(body.auditEvent)) return false;
  const history = latestWorkflowRecord({ id: String(payload.issueId), storeId: String(payload.storeId), workflowRecords: body.issue.workflowRecords }, action);
  if (history.status !== "available") return false;
  const audit = body.auditEvent, metadata = audit.metadata;
  const rows = (body.issue.workflowRecords as WorkflowRecord[]).filter(row => row.workflowAuditId === audit.id && row.action === action);
  if (rows.length !== 1 || audit.targetType !== "issue" || audit.targetId !== payload.issueId || !object(metadata)
    || audit.action !== (action === "camera-purpose" ? "evidence.camera_purpose.recorded" : "issue." + action)
    || metadata.issueId !== payload.issueId || metadata.workflowAuditId !== audit.id || metadata.idempotencyKey !== idempotencyKey) return false;
  const record = rows[0];
  if (record.id !== audit.id || record.auditEventId === record.workflowAuditId || record.actorName !== audit.actorName || record.actorRoleId !== audit.actorRoleId
    || !body.idempotentReplay && record.correlationId !== requestCID
    || canonicalStoredReviewJSON(record.submission) !== canonicalStoredReviewJSON(normalizeWorkflowSubmission(action, payload))) return false;
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonicalStoredReviewJSON(record)));
  return metadata.reviewRecordHash === Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, "0")).join("");
}
