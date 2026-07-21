/**
 * GENERATED FILE — DO NOT EDIT.
 *
 * Source:    packages/openapi-client/openapi.json
 * Generator: scripts/openapi/generate_client.py
 *
 * Regenerate with:
 *   python3 scripts/openapi/export_openapi.py     # refresh the artifact from the app
 *   python3 scripts/openapi/generate_client.py    # refresh this file
 *
 * CI runs `scripts/openapi/check_drift.py`, which fails if this file does not
 * match the artifact, or the artifact does not match the live API.
 */

/* eslint-disable */


/** OpenAPI 3.1.0 — ODay Plus Assisted Listing Intake API v1.1.3 */

export const API_VERSION = "1.1.3";



export type ApiError = {
  code: "AUTHENTICATION_REQUIRED" | "ROLE_DENIED" | "TENANT_SCOPE_DENIED" | "SCOPE_DENIED" | "OWNERSHIP_REQUIRED" | "ASSIGNMENT_SCOPE_DENIED" | "SOURCE_SCOPE_DENIED" | "FIELD_MASKED" | "DATA_CLASSIFICATION_DENIED" | "PURPOSE_REQUIRED" | "PRECONDITION_REQUIRED" | "VERSION_CONFLICT" | "WORKFLOW_STATE_DENIED" | "OWNER_CONFLICT" | "SECOND_ACTOR_REQUIRED" | "SELF_REVIEW_DENIED" | "RISK_ACKNOWLEDGEMENT_REQUIRED" | "SOURCE_POLICY_DENIED" | "SOURCE_POLICY_UNKNOWN" | "SOURCE_AUTH_REQUIRED" | "LEGAL_HOLD_CONFLICT" | "RETENTION_NOT_REACHED" | "RESIDENCY_DENIED" | "EXPORT_APPROVAL_REQUIRED" | "PURGE_APPROVAL_REQUIRED" | "QUARANTINE_RELEASE_DENIED" | "PROMOTION_APPROVAL_REQUIRED" | "RESTRICTED_EXPORT_DENIED" | "BREAK_GLASS_DENIED" | "DEPENDENCY_CONFLICT" | "DUPLICATE_CANDIDATE" | "IDEMPOTENCY_KEY_REUSED" | "RETRY_BUDGET_EXHAUSTED" | "CHECKPOINT_UNAVAILABLE" | "JOB_FENCE_REJECTED" | "SLA_PAUSE_DENIED" | "DECISION_INCOMPLETE" | "BACKPRESSURE_ACTIVE" | "RATE_LIMITED" | "RESOURCE_NOT_FOUND" | "VALIDATION_FAILED" | "FIELD_REQUIRED" | "CURSOR_INVALID" | "CURSOR_EXPIRED" | "INTERNAL_ERROR";
  message: string;
  retryable: boolean;
  correlation_id: string;
  reason_code?: string | null;
  field_errors?: {
    field: string;
    code: string;
    message: string;
  }[];
  current_version?: number | null;
  retry_after_seconds?: number | null;
  /** Authoritative server timestamp for the error response. */
  occurred_at: string;
  /** Machine-readable client guidance; null when no safe automated action exists. */
  next_action: "RETRY" | "REFRESH" | "CORRECT_INPUT" | "REQUEST_ACCESS" | "CONTACT_SUPPORT" | "WAIT" | null;
};

export type AssignmentReceipt = {
  assignment_id: string;
  status: "ASSIGNED" | "CLAIMED" | "TRANSFERRED" | "ESCALATED" | "COMPLETED";
  owner_subject_id: string;
  due_at: string;
  version: number;
  audit_event_id: string;
};

export type AssignmentRequest = {
  owner_subject_id: string;
  owner_role: string;
  due_at: string;
  reason: string;
  handoff_note?: string | null;
};

export type AssignmentTransferRequest = {
  target_owner_subject_id: string;
  target_owner_role: string;
  reason: string;
  handoff_note: string;
  due_at?: string | null;
};

export type AuditReference = {
  audit_event_id: string;
  action: string;
  occurred_at: string;
  result: "ALLOWED" | "DENIED" | "SUCCEEDED" | "FAILED" | "MASKED";
  reason_code?: string | null;
};

export type BatchIntakeReceipt = {
  batch_id: string;
  submitted_at: string;
  accepted_count: number;
  rejected_count: number;
  rows: BatchRowReceipt[];
  correlation_id: string;
};

export type BatchIntakeRequest = {
  batch_id: string;
  method: "MANUAL" | "CSV" | "APPROVED_FEED";
  scope: ScopeContext;
  rows: ManualIntakeRow[];
};

export type BatchRowReceipt = {
  row_index: number;
  client_row_id?: string | null;
  status: "ACCEPTED" | "REJECTED" | "REPLAYED";
  intake_id?: string | null;
  error?: ApiError | null;
};

export type CandidateReassignment = {
  candidate_site_id: string;
  disposition: "KEEP_HISTORICAL" | "REASSIGN" | "REQUIRE_REVIEW";
  target_property_id?: string | null;
};

export type ConflictError = ApiError & {
  current_version?: number | null;
  current_state?: string | null;
  current_owner_subject_id?: string | null;
  retry_with_etag?: string | null;
};

export type CorrectionReceipt = {
  correction_id: string;
  status: "PROPOSED" | "APPLIED" | "PENDING_REVIEW";
  intake_id: string;
  listing_revision_id?: string | null;
  version: number;
  audit_event_id: string;
  correlation_id: string;
};

export type CorrectionRequest = {
  field_path: string;
  corrected_value: unknown;
  reason: string;
  expected_effective_value_sha256?: string | null;
  risk_acknowledged?: boolean;
};

export type DecisionReceipt = {
  decision_id: string;
  status: "PENDING_REVIEW" | "APPROVED" | "REJECTED" | "EXECUTING" | "EXECUTED" | "FAILED" | "REVERSAL_PENDING" | "REVERSED";
  resource_versions: Record<string, number>;
  job_id?: string | null;
  audit_event_id: string;
  correlation_id: string;
};

export type FieldClassification = "PUBLIC" | "INTERNAL" | "CONFIDENTIAL" | "RESTRICTED";

export type FieldValue = {
  field_path: string;
  parsed?: unknown;
  normalized?: unknown;
  corrected?: unknown;
  effective?: unknown;
  confidence?: number | null;
  classification: FieldClassification;
  masked: boolean;
  mask_reason_code?: string | null;
};

export type IdentityPartition = {
  target_property_id: string | null;
  source_identity_edge_ids: string[];
};

export type IntakeDetail = IntakeSummary & {
  original_url: string | null;
  canonical_url: string | null;
  policy_state: SourcePolicyState | null;
  source_snapshot_id?: string | null;
  parser_run_id?: string | null;
  match_case_id?: string | null;
  processing_history: TransitionReceipt[];
  fields: FieldValue[];
  audit: AuditReference[];
};

export type IntakePage = {
  items: IntakeSummary[];
  next_cursor?: string | null;
  page_size: number;
  total_count: number;
  total_count_accuracy?: "EXACT" | "ESTIMATED";
  snapshot_time: string;
  query_fingerprint: string;
};

export type IntakeState = "SUBMITTED" | "CHECKING_IDENTITY" | "CHECKING_SOURCE_POLICY" | "AWAITING_ASSISTED_ENTRY" | "RETRIEVING" | "PARSING" | "MATCHING" | "NEEDS_REVIEW" | "READY" | "QUARANTINED" | "FAILED" | "CANCELLED";

export type IntakeSubmissionReceipt = {
  intake_id: string;
  state: IntakeState;
  version: number;
  job_id: string;
  duplicate_hint?: string | null;
  correlation_id: string;
  submitted_at: string;
};

export type IntakeSummary = {
  intake_id: string;
  state: IntakeState;
  intake_method: "URL" | "MANUAL" | "CSV" | "APPROVED_FEED" | "OPERATOR_SNAPSHOT";
  source_id?: string | null;
  match_outcome?: MatchOutcome | null;
  submitted_by?: string;
  assigned_to?: string | null;
  due_at?: string | null;
  submitted_at: string;
  updated_at: string;
  version: number;
  scope: ScopeContext;
  masked_fields?: string[];
};

export type JobReceipt = {
  job_id: string;
  status: "QUEUED" | "RUNNING" | "RETRYING" | "SUCCEEDED" | "FAILED" | "CANCELLED" | "DEAD_LETTER";
  checkpoint: string;
  attempt: number;
  version: number;
  correlation_id: string;
};

export type ManualIntakeRow = {
  source_id?: string;
  source_listing_id?: string | null;
  address_raw: string;
  rent_amount?: number | null;
  currency?: string;
  area_ping?: number | null;
  floor?: string | null;
  original_url?: string | null;
};

export type MatchDecisionRequest = {
  decision_type: "CREATE" | "REVISE" | "DUPLICATE" | "QUARANTINE" | "REJECT" | "REOPEN" | "MERGE" | "SPLIT" | "UNMERGE";
  target_property_id?: string | null;
  target_listing_id?: string | null;
  reason: string;
  risk_acknowledged?: boolean;
  requested_second_reviewer_id?: string | null;
};

export type MatchOutcome = "NEW" | "EXACT_DUPLICATE" | "REVISION" | "POSSIBLE_MATCH" | "QUARANTINED";

export type MergeRequest = {
  source_property_ids: string[];
  target_property_id: string;
  expected_property_versions?: Record<string, number>;
  candidate_reassignment_plan?: CandidateReassignment[];
  reason: string;
  risk_acknowledged: true;
};

export type PromotionDecisionReceipt = {
  promotion_decision_id: string;
  intake_id: string;
  listing_id: string;
  status: "REQUESTED" | "VALIDATING" | "PENDING_REVIEW" | "REJECTED" | "APPROVED" | "CANDIDATE_CREATING" | "CANDIDATE_CREATED" | "SCORE_QUEUED" | "COMPLETED" | "FAILED" | "SCORE_FAILED";
  decision_type: "STANDARD" | "LEGACY_RECONCILED";
  reviewer_subject_id?: string | null;
  candidate_site_id?: string | null;
  site_score_job_id?: string | null;
  version: number;
  audit_event_id: string;
  correlation_id: string;
};

export type PromotionRequest = {
  target_format_code: string;
  reason: string;
  gate_snapshot_sha256: string;
  risk_acknowledged?: boolean;
  requested_reviewer_id?: string | null;
};

export type ReasonCommand = {
  reason: string;
};

export type RetryRequest = {
  checkpoint: "RETRIEVING" | "PARSING" | "MATCHING" | "CANDIDATE_CREATING" | "SCORE_QUEUED";
  reason: string;
  override_retry_budget?: boolean;
  risk_acknowledged?: boolean;
};

export type ReviewDecisionRequest = {
  decision: "APPROVE" | "REJECT";
  reason: string;
  risk_acknowledged?: boolean;
  requested_changes?: string[];
};

export type RiskReasonCommand = ReasonCommand & {
  risk_acknowledged: true;
  incident_or_change_id?: string | null;
};

export type SavedView = SavedViewRequest & {
  saved_view_id: string;
  owner_subject_id: string;
  created_at: string;
  version: number;
};

export type SavedViewRequest = {
  name: string;
  resource: "intake";
  query: Record<string, unknown>;
  visibility?: "PRIVATE" | "ROLE" | "TENANT";
  shared_role?: string | null;
};

export type ScopeContext = {
  tenant_id: string;
  brand_id?: string | null;
  region_id?: string | null;
  assigned_area_id?: string | null;
  heat_zone_id?: string | null;
};

export type SlaPauseRequest = {
  reason: string;
  expected_resume_at: string;
};

export type SlaReceipt = {
  sla_instance_id: string;
  state: "ON_TRACK" | "DUE_SOON" | "OVERDUE" | "BREACHED" | "PAUSED" | "COMPLETED";
  due_at: string;
  due_soon_at?: string | null;
  paused_duration_seconds: number;
  active_pause_interval_id?: string | null;
  version: number;
  audit_event_id: string;
  correlation_id: string;
};

export type SourcePolicyState = "APPROVED_RETRIEVAL" | "ASSISTED_ENTRY_ONLY" | "AUTH_REQUIRED" | "SOURCE_BLOCKED" | "POLICY_UNKNOWN";

export type SplitRequest = {
  source_property_id: string;
  source_property_version?: number;
  partitions: IdentityPartition[];
  reason: string;
  risk_acknowledged: true;
};

export type TransitionReceipt = {
  transition_id: string;
  from_state: string | null;
  to_state: string;
  occurred_at: string;
  actor: string;
  reason_code?: string | null;
  version_after: number;
};

export type UnmergeRequest = {
  original_decision_id: string;
  replacement_edges: IdentityPartition[];
  reason: string;
  risk_acknowledged: true;
};

export type UrlIntakeRequest = {
  original_url: string;
  scope: ScopeContext;
  owner_subject_id?: string | null;
  purpose?: string;
};



/** Every versioned operation the API serves, and its methods. */
export const API_PATHS = {
  "/api/v1/assignments/{assignment_id}/actions/claim": ["POST"],
  "/api/v1/assignments/{assignment_id}/actions/complete": ["POST"],
  "/api/v1/assignments/{assignment_id}/actions/transfer": ["POST"],
  "/api/v1/identity-decisions/{decision_id}": ["GET"],
  "/api/v1/identity-decisions/{decision_id}/actions/reverse": ["POST"],
  "/api/v1/identity-decisions/{decision_id}/actions/review": ["POST"],
  "/api/v1/identity/merge": ["POST"],
  "/api/v1/identity/split": ["POST"],
  "/api/v1/identity/unmerge": ["POST"],
  "/api/v1/intake-batches": ["POST"],
  "/api/v1/intakes": ["GET"],
  "/api/v1/intakes/url": ["POST"],
  "/api/v1/intakes/{intake_id}": ["GET"],
  "/api/v1/intakes/{intake_id}/actions/cancel": ["POST"],
  "/api/v1/intakes/{intake_id}/actions/quarantine": ["POST"],
  "/api/v1/intakes/{intake_id}/actions/reopen": ["POST"],
  "/api/v1/intakes/{intake_id}/assignment": ["PUT"],
  "/api/v1/intakes/{intake_id}/corrections": ["POST"],
  "/api/v1/intakes/{intake_id}/promotion-requests": ["POST"],
  "/api/v1/jobs/{job_id}/retry": ["POST"],
  "/api/v1/match-cases/{match_case_id}/decisions": ["POST"],
  "/api/v1/promotion-decisions/{promotion_decision_id}": ["GET"],
  "/api/v1/promotion-decisions/{promotion_decision_id}/actions/review": ["POST"],
  "/api/v1/saved-views": ["GET", "POST"],
  "/api/v1/sla-instances/{sla_instance_id}/actions/pause": ["POST"],
  "/api/v1/sla-instances/{sla_instance_id}/actions/resume": ["POST"],
} as const;

export type ApiPath = keyof typeof API_PATHS;
