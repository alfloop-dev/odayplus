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



/** ActorDecisionFacts */
export type ActorDecisionFacts = {
  role_mode: "expansion-staff" | "expansion-manager" | "data-steward" | "governance-reviewer" | "privacy-officer" | "permission-limited";
  allowed_actions: string[];
  denied_action_reasons: Record<string, string>;
  scope: Record<string, unknown>;
  masking: Record<string, unknown>;
  purpose: Record<string, unknown>;
  second_actor: Record<string, unknown>;
};

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

/** AssignmentLifecycleSnapshot */
export type AssignmentLifecycleSnapshot = {
  assignment_id: string;
  intake_id?: string | null;
  status: string;
  owner_subject_id?: string | null;
  queue_id?: string | null;
  due_at?: string | null;
  version: number;
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

/** AuditReference */
export type AuditReference = {
  audit_event_id: string;
  action: string;
  occurred_at: string;
  result: AuditResult;
  reason_code?: string | null;
  actor?: string | null;
  actor_role?: string | null;
  before?: unknown | null;
  after?: unknown | null;
  source_snapshot_id?: string | null;
  parser_version?: string | null;
  related_ids?: Record<string, unknown>;
  correlation_id?: string | null;
  resource_version?: number | null;
  evidence_state?: string | null;
};

/** AuditResult */
export type AuditResult = "ALLOWED" | "DENIED" | "SUCCEEDED" | "FAILED" | "MASKED";

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

/** CandidateImpact */
export type CandidateImpact = {
  candidate_site_id?: string | null;
  disposition?: string | null;
  source_property_id?: string | null;
  target_property_id?: string | null;
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

/** DecisionActorReference */
export type DecisionActorReference = {
  subject_id: string;
  role_id: string;
};

/** DecisionEffectReceipt */
export type DecisionEffectReceipt = {
  receipt_id: string;
  decision_id: string;
  status: string;
  identity_edge_ids: string[];
  runtime_receipt?: MutationReceiptRecord | null;
  audit_event_id: string;
  correlation_id: string;
  version: number;
  issued_at: string;
  evidence_state: string;
};

/** DecisionLifecycleSnapshot */
export type DecisionLifecycleSnapshot = {
  decision_id?: string | null;
  receipt_id?: string | null;
  status: string;
  action?: string | null;
  version: number;
  proposer?: string | null;
  reviewer?: string | null;
  graph_plan?: MatchGraphPlan | null;
  correlation_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

/** DecisionReceipt */
export type DecisionReceipt = {
  decision_id: string;
  status: DecisionStatus;
  resource_versions: Record<string, number>;
  job_id?: string | null;
  audit_event_id: string;
  correlation_id: string;
  version: number;
  action?: string | null;
  proposer?: string | null;
  reviewer?: string | null;
  reason?: string | null;
  graph_plan?: MatchGraphPlan | null;
  effect_receipt?: DecisionEffectReceipt | null;
  reverses_decision_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  status_history?: Record<string, unknown>[];
  lifecycle_contract?: Record<string, unknown>;
};

/** DecisionStatus */
export type DecisionStatus = "DRAFT" | "PENDING_REVIEW" | "APPROVED" | "REJECTED" | "EXECUTING" | "EXECUTED" | "FAILED" | "REVERSAL_PENDING" | "REVERSED" | "SUPERSEDED";

export type FieldClassification = "PUBLIC" | "INTERNAL" | "CONFIDENTIAL" | "RESTRICTED";

/** FieldValue */
export type FieldValue = {
  field_path: string;
  classification: FieldClassification;
  masked: boolean;
  parsed?: unknown | null;
  normalized?: unknown | null;
  corrected?: unknown | null;
  effective?: unknown | null;
  confidence?: number | null;
  mask_reason_code?: string | null;
  correction_actor?: string | null;
  correction_actor_role?: string | null;
  correction_reason?: string | null;
  corrected_at?: string | null;
  source_snapshot_id?: string | null;
  parser_run_id?: string | null;
  parser_version?: string | null;
};

/** IdentityGraphEdge */
export type IdentityGraphEdge = {
  edge_id: string;
  relation: string;
  status: string;
  source_property_id?: string | null;
  target_property_id?: string | null;
  property_id?: string | null;
  listing_id?: string | null;
  intake_id?: string | null;
  decision_id?: string | null;
  supersedes_edge_ids?: string[];
};

/** IdentityGraphNode */
export type IdentityGraphNode = {
  node_id: string;
  node_type: string;
  status: string;
};

/** IdentityGraphSnapshot */
export type IdentityGraphSnapshot = {
  version: number;
  nodes: IdentityGraphNode[];
  edges: IdentityGraphEdge[];
};

export type IdentityPartition = {
  target_property_id: string | null;
  source_identity_edge_ids: string[];
};

/** IdentityRedirect */
export type IdentityRedirect = {
  from_property_id: string;
  to_property_id: string;
  reason: string;
  status: string;
};

export type InboxLocationSummary = {
  address?: string | null;
  district?: string | null;
  assigned_area_id?: string | null;
  heat_zone_id?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  confidence?: number | null;
  source?: string | null;
};

export type InboxMaskingSummary = {
  restricted_data: boolean;
  has_masked_fields: boolean;
  masked_fields?: string[];
  reason_codes?: string[];
};

/** IntakeDetail */
export type IntakeDetail = {
  intake_id: string;
  state: IntakeState;
  intake_method: IntakeMethod;
  source_id?: string | null;
  original_url: string | null;
  canonical_url: string | null;
  policy_state: SourcePolicyState | null;
  match_outcome?: MatchOutcome | null;
  submitted_by?: string;
  assigned_to?: string | null;
  assignment_id?: string | null;
  assignment_status?: string | null;
  assignment_version?: number | null;
  owner_subject_id?: string | null;
  queue_id?: string | null;
  sla_instance_id?: string | null;
  sla_state?: string | null;
  sla_version?: number | null;
  due_at?: string | null;
  last_observed_at?: string | null;
  submitted_at: string;
  updated_at: string;
  version: number;
  scope: ScopeContext;
  issue?: string | null;
  next_action?: string | null;
  retryable?: boolean;
  quarantined?: boolean;
  failed?: boolean;
  location: InboxLocationSummary;
  masking: InboxMaskingSummary;
  masked_fields?: string[];
  correlation_id: string;
  source_snapshot_id?: string | null;
  parser_run_id?: string | null;
  match_case_id?: string | null;
  match_case_version?: number | null;
  match_case?: MatchCaseDetail | null;
  processing_history: TransitionReceipt[];
  fields: FieldValue[];
  audit: AuditReference[];
  evidence: SourceEvidenceDetail;
  sla_receipt?: string | null;
  lifecycle: LifecycleAggregate;
};

/** IntakeMethod */
export type IntakeMethod = "URL" | "MANUAL" | "CSV" | "APPROVED_FEED" | "OPERATOR_SNAPSHOT";

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

/** IntakeSubmissionReceipt */
export type IntakeSubmissionReceipt = {
  intake_id: string;
  state: IntakeState;
  version: number;
  job_id?: string | null;
  correlation_id: string;
  submitted_at: string;
  duplicate_hint?: string | null;
  identity_outcome?: "EXACT_DUPLICATE" | null;
  existing_listing_id?: string | null;
  navigation_target?: string | null;
  submission_receipt_id?: string | null;
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
  original_url?: string | null;
  canonical_url?: string | null;
  policy_state?: SourcePolicyState | null;
  assignment_id?: string | null;
  assignment_status?: "ASSIGNED" | "CLAIMED" | "TRANSFERRED" | "ESCALATED" | "COMPLETED" | null;
  assignment_version?: number | null;
  owner_subject_id?: string | null;
  queue_id?: string | null;
  sla_instance_id?: string | null;
  sla_state?: "ON_TRACK" | "DUE_SOON" | "OVERDUE" | "BREACHED" | "PAUSED" | "COMPLETED" | null;
  sla_version?: number | null;
  last_observed_at?: string | null;
  issue?: string | null;
  next_action?: string | null;
  retryable?: boolean;
  quarantined?: boolean;
  failed?: boolean;
  location: InboxLocationSummary;
  masking: InboxMaskingSummary;
};

/** JobLifecycleSnapshot */
export type JobLifecycleSnapshot = {
  job_id: string;
  status: string;
  attempt?: number | null;
  checkpoint?: string | null;
  next_retry_at?: string | null;
  fence_token?: number | string | null;
  version?: number | null;
};

export type JobReceipt = {
  job_id: string;
  status: "QUEUED" | "RUNNING" | "RETRYING" | "SUCCEEDED" | "FAILED" | "CANCELLED" | "DEAD_LETTER";
  checkpoint: string;
  attempt: number;
  version: number;
  correlation_id: string;
};

/** LifecycleAggregate */
export type LifecycleAggregate = {
  intake_id: string;
  version: number;
  etag: string;
  actor_facts: ActorDecisionFacts;
  assignment?: AssignmentLifecycleSnapshot | null;
  sla?: SlaLifecycleSnapshot | null;
  decisions: DecisionLifecycleSnapshot[];
  promotion?: PromotionLifecycleSnapshot | null;
  job?: JobLifecycleSnapshot | null;
  assignment_history: LifecycleReceiptRecord[];
  sla_history: LifecycleReceiptRecord[];
  decision_history: LifecycleReceiptRecord[];
  promotion_history: LifecycleReceiptRecord[];
  job_history: LifecycleReceiptRecord[];
  mutation_receipts: LifecycleReceiptRecord[];
  latest_decision_receipt?: DecisionLifecycleSnapshot | null;
  submission_receipt?: SubmissionLifecycleReceipt | null;
};

/** LifecycleReceiptRecord */
export type LifecycleReceiptRecord = {
  receipt_id?: string | null;
  category: "assignment" | "sla" | "decision" | "promotion" | "job" | "intake";
  action?: string | null;
  resource_id?: string | null;
  resource_version?: number | null;
  status?: string | null;
  actor?: string | null;
  correlation_id?: string | null;
  occurred_at?: string | null;
  receipt: MutationReceiptRecord;
};

/** LineageImpact */
export type LineageImpact = {
  append_only: boolean;
  source_evidence_preserved: boolean;
  superseded_edge_ids: string[];
  affected_decision_ids: string[];
  summary: string;
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

/** MatchCaseDetail */
export type MatchCaseDetail = {
  match_case_id: string;
  version: number;
  intake_id: string;
  outcome: MatchOutcome;
  confidence: number;
  target_listing_id?: string | null;
  summary: string;
  comparison_fields: MatchComparisonField[];
  signals: MatchSignal[];
  graph_plan: MatchGraphPlan;
  source_snapshot_id?: string | null;
  parser_version?: string | null;
  created_at: string;
  updated_at: string;
};

/** MatchComparisonField */
export type MatchComparisonField = {
  field_path: string;
  label: string;
  submitted_value?: unknown;
  existing_value?: unknown;
  agrees: boolean;
  detail?: string | null;
};

export type MatchDecisionRequest = {
  decision_type: "CREATE" | "REVISE" | "DUPLICATE" | "QUARANTINE" | "REJECT" | "REOPEN" | "MERGE" | "SPLIT" | "UNMERGE";
  target_property_id?: string | null;
  target_listing_id?: string | null;
  reason: string;
  risk_acknowledged?: boolean;
  requested_second_reviewer_id?: string | null;
};

/** MatchGraphPlan */
export type MatchGraphPlan = {
  plan_id: string;
  plan_type: string;
  status: string;
  operations: Record<string, unknown>[];
  permitted_decision_types?: string[];
  requires_human_decision?: boolean;
  before_graph: IdentityGraphSnapshot;
  after_graph: IdentityGraphSnapshot;
  redirects: IdentityRedirect[];
  candidate_impacts: CandidateImpact[];
  lineage_impact: LineageImpact;
  proposer?: DecisionActorReference | null;
  reviewer?: DecisionActorReference | null;
  expected_graph_version: number;
  original_decision?: OriginalDecisionReference | null;
  generated_at: string;
};

export type MatchOutcome = "NEW" | "EXACT_DUPLICATE" | "REVISION" | "POSSIBLE_MATCH" | "QUARANTINED";

/** MatchSignal */
export type MatchSignal = {
  key: string;
  label: string;
  agrees: boolean;
  detail: string;
};

export type MergeRequest = {
  source_property_ids: string[];
  target_property_id: string;
  expected_property_versions?: Record<string, number>;
  candidate_reassignment_plan?: CandidateReassignment[];
  reason: string;
  risk_acknowledged: true;
};

/** MutationReceiptRecord */
export type MutationReceiptRecord = {
  receipt_id?: string | null;
  transition_id?: string | null;
  assignment_id?: string | null;
  sla_instance_id?: string | null;
  job_id?: string | null;
  promotion_decision_id?: string | null;
  decision_id?: string | null;
  intake_id?: string | null;
  listing_id?: string | null;
  listing_revision_id?: string | null;
  identity_edge_id?: string | null;
  candidate_site_id?: string | null;
  site_score_job_id?: string | null;
  status?: string | null;
  state?: string | null;
  from_state?: string | null;
  to_state?: string | null;
  action?: string | null;
  version?: number | null;
  version_after?: number | null;
  audit_event_id?: string | null;
  correlation_id?: string | null;
  actor?: string | null;
  reason?: string | null;
  checkpoint?: string | null;
  attempt?: number | null;
  retryable?: boolean | null;
  occurred_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  issued_at?: string | null;
};

/** OriginalDecisionReference */
export type OriginalDecisionReference = {
  decision_id: string;
  action?: string | null;
  status?: string | null;
  version?: number | null;
};

/** PromotionDecisionReceipt */
export type PromotionDecisionReceipt = {
  promotion_decision_id: string;
  intake_id: string;
  listing_id: string;
  status: PromotionStatus;
  decision_type: PromotionDecisionType;
  version: number;
  audit_event_id: string;
  correlation_id: string;
  candidate_site_id?: string | null;
  proposer_subject_id: string;
  reviewer_subject_id?: string | null;
  site_score_job_id?: string | null;
  status_history?: Record<string, unknown>[];
};

/** PromotionDecisionType */
export type PromotionDecisionType = "STANDARD" | "LEGACY_RECONCILED";

/** PromotionLifecycleSnapshot */
export type PromotionLifecycleSnapshot = {
  promotion_decision_id: string;
  intake_id?: string | null;
  status: string;
  candidate_site_id?: string | null;
  site_score_job_id?: string | null;
  version: number;
};

export type PromotionRequest = {
  target_format_code: string;
  reason: string;
  gate_snapshot_sha256: string;
  risk_acknowledged?: boolean;
  requested_reviewer_id?: string | null;
};

/** PromotionStatus */
export type PromotionStatus = "REQUESTED" | "VALIDATING" | "PENDING_REVIEW" | "REJECTED" | "APPROVED" | "CANDIDATE_CREATING" | "CANDIDATE_CREATED" | "SCORE_QUEUED" | "COMPLETED" | "FAILED" | "SCORE_FAILED";

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

/** SlaLifecycleSnapshot */
export type SlaLifecycleSnapshot = {
  sla_instance_id: string;
  state: string;
  due_at?: string | null;
  paused_duration_seconds?: number | null;
  version: number;
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

/** SourceEvidenceDetail */
export type SourceEvidenceDetail = {
  original_url?: string | null;
  canonical_url?: string | null;
  source_id?: string | null;
  policy_state?: SourcePolicyState | null;
  policy_reason?: string | null;
  policy_version?: string | null;
  policy_evaluated_at?: string | null;
  policy_expires_at?: string | null;
  source_snapshot_id?: string | null;
  captured_at?: string | null;
  parser_run_id?: string | null;
  parser_version?: string | null;
  correlation_id: string;
  freshness_state: "CURRENT" | "STALE" | "NOT_CAPTURED";
  resource_version: number;
  etag: string;
};

export type SourcePolicyState = "APPROVED_RETRIEVAL" | "ASSISTED_ENTRY_ONLY" | "AUTH_REQUIRED" | "SOURCE_BLOCKED" | "POLICY_UNKNOWN";

export type SplitRequest = {
  source_property_id: string;
  source_property_version?: number;
  partitions: IdentityPartition[];
  reason: string;
  risk_acknowledged: true;
};

/** SubmissionLifecycleReceipt */
export type SubmissionLifecycleReceipt = {
  receipt_id: string;
  receipt_type: string;
  intake_id: string;
  state: string;
  existing_listing_id?: string | null;
  navigation_target?: string | null;
  correlation_id: string;
  issued_at: string;
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
  "/api/v1/intakes/{intake_id}/promotion-decision": ["GET"],
  "/api/v1/intakes/{intake_id}/promotion-requests": ["POST"],
  "/api/v1/jobs/{job_id}/receipt": ["GET"],
  "/api/v1/jobs/{job_id}/retry": ["POST"],
  "/api/v1/match-cases/{match_case_id}/decisions": ["POST"],
  "/api/v1/promotion-decisions/{promotion_decision_id}": ["GET"],
  "/api/v1/promotion-decisions/{promotion_decision_id}/actions/review": ["POST"],
  "/api/v1/saved-views": ["GET", "POST"],
  "/api/v1/sla-instances/{sla_instance_id}/actions/pause": ["POST"],
  "/api/v1/sla-instances/{sla_instance_id}/actions/resume": ["POST"],
} as const;

export type ApiPath = keyof typeof API_PATHS;
