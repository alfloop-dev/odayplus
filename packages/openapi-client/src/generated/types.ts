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


/** OpenAPI 3.1.0 — ODay Plus API v0.1.0 */

export const API_VERSION = "0.1.0";



/** AVMCasePayload */
export type AVMCasePayload = {
  asset_book_value: number;
  comparable_multiples?: number[];
  created_by: string;
  equipment_fair_value: number;
  forecast_gm_next_12m: number;
  gm_ttm: number;
  idempotency_key?: string | null;
  lease_liability?: number;
  liquidity_discount?: number;
  prediction_origin_time?: string | null;
  quality_score?: number;
  source_snapshot_ids?: string[];
  store_id: string;
  working_capital?: number;
};

/** ActionPayload */
export type ActionPayload = {
  action_spec?: Record<string, unknown>;
  actor: string;
};

/** ActorPayload */
export type ActorPayload = {
  actor: string;
};

/** AdLiftIncrementalityJobPayload */
export type AdLiftIncrementalityJobPayload = {
  campaigns?: Record<string, unknown>[];
  generated_at?: string | null;
  idempotency_key?: string | null;
};

/** ApiError */
export type ApiError = {
  code: "AUTHENTICATION_REQUIRED" | "ROLE_DENIED" | "TENANT_SCOPE_DENIED" | "SCOPE_DENIED" | "OWNERSHIP_REQUIRED" | "ASSIGNMENT_SCOPE_DENIED" | "SOURCE_SCOPE_DENIED" | "FIELD_MASKED" | "DATA_CLASSIFICATION_DENIED" | "PURPOSE_REQUIRED" | "PRECONDITION_REQUIRED" | "VERSION_CONFLICT" | "WORKFLOW_STATE_DENIED" | "OWNER_CONFLICT" | "SECOND_ACTOR_REQUIRED" | "SELF_REVIEW_DENIED" | "RISK_ACKNOWLEDGEMENT_REQUIRED" | "SOURCE_POLICY_DENIED" | "SOURCE_POLICY_UNKNOWN" | "SOURCE_AUTH_REQUIRED" | "LEGAL_HOLD_CONFLICT" | "RETENTION_NOT_REACHED" | "RESIDENCY_DENIED" | "EXPORT_APPROVAL_REQUIRED" | "PURGE_APPROVAL_REQUIRED" | "QUARANTINE_RELEASE_DENIED" | "PROMOTION_APPROVAL_REQUIRED" | "RESTRICTED_EXPORT_DENIED" | "BREAK_GLASS_DENIED" | "DEPENDENCY_CONFLICT" | "DUPLICATE_CANDIDATE" | "IDEMPOTENCY_KEY_REUSED" | "RETRY_BUDGET_EXHAUSTED" | "CHECKPOINT_UNAVAILABLE" | "JOB_FENCE_REJECTED" | "SLA_PAUSE_DENIED" | "DECISION_INCOMPLETE" | "BACKPRESSURE_ACTIVE" | "RATE_LIMITED" | "RESOURCE_NOT_FOUND" | "VALIDATION_FAILED" | "FIELD_REQUIRED" | "CURSOR_INVALID" | "CURSOR_EXPIRED" | "INTERNAL_ERROR";
  correlation_id: string;
  current_version?: number | null;
  field_errors?: FieldError[];
  message: string;
  next_action: "RETRY" | "REFRESH" | "CORRECT_INPUT" | "REQUEST_ACCESS" | "CONTACT_SUPPORT" | "WAIT" | null;
  occurred_at: string;
  reason_code?: string | null;
  retry_after_seconds?: number | null;
  retryable: boolean;
};

/** POST /operator/growth/approvals/{id}/decision — approve / reject. */
export type ApprovalDecisionPayload = {
  actorName?: string | null;
  actorRoleId?: string | null;
  decision: string;
  reason?: string;
};

/** Write body for POST /operator/approvals/{approval_id}/decision.

reason is required for all decisions to enforce audit traceability.
High-risk approvals additionally validate non-empty reason at the
service layer. */
export type ApprovalDecisionRequest = {
  actorName?: string | null;
  actorRoleId: string;
  reason: string;
  status: "approved" | "returned" | "rejected";
};

/** Response envelope for approval decision writes. */
export type ApprovalDecisionResponse = {
  approvalId: string;
  auditEventId: string;
  correlationId?: string | null;
  newStatus: string;
};

/** AssignmentReceipt */
export type AssignmentReceipt = {
  assignment_id: string;
  audit_event_id: string;
  due_at: string;
  owner_subject_id: string;
  status: AssignmentStatus;
  version: number;
};

/** AssignmentRequest */
export type AssignmentRequest = {
  due_at: string;
  handoff_note?: string | null;
  owner_role: string;
  owner_subject_id: string;
  reason: string;
};

/** AssignmentStatus */
export type AssignmentStatus = "ASSIGNED" | "CLAIMED" | "TRANSFERRED" | "ESCALATED" | "COMPLETED";

/** AssignmentTransferRequest */
export type AssignmentTransferRequest = {
  due_at?: string | null;
  handoff_note: string;
  reason: string;
  target_owner_role: string;
  target_owner_subject_id: string;
};

/** AuditReference */
export type AuditReference = {
  action: string;
  audit_event_id: string;
  occurred_at: string;
  reason_code?: string | null;
  result: AuditResult;
};

/** AuditResult */
export type AuditResult = "ALLOWED" | "DENIED" | "SUCCEEDED" | "FAILED" | "MASKED";

/** BatchIntakeMethod */
export type BatchIntakeMethod = "MANUAL" | "CSV" | "APPROVED_FEED";

/** BatchIntakeReceipt */
export type BatchIntakeReceipt = {
  accepted_count: number;
  batch_id: string;
  correlation_id: string;
  rejected_count: number;
  rows: BatchRowReceipt[];
  submitted_at: string;
};

/** BatchIntakeRequest */
export type BatchIntakeRequest = {
  batch_id: string;
  method: BatchIntakeMethod;
  rows: ManualIntakeRow[];
  scope: ScopeContext;
};

/** BatchRowReceipt */
export type BatchRowReceipt = {
  client_row_id?: string | null;
  error?: ApiError | null;
  intake_id?: string | null;
  row_index: number;
  status: BatchRowStatus;
};

/** BatchRowStatus */
export type BatchRowStatus = "ACCEPTED" | "REJECTED" | "REPLAYED";

/** CandidateDisposition */
export type CandidateDisposition = "KEEP_HISTORICAL" | "REASSIGN" | "REQUIRE_REVIEW";

/** CandidateReassignment */
export type CandidateReassignment = {
  candidate_site_id: string;
  disposition: CandidateDisposition;
  target_property_id?: string | null;
};

/** ClosePayload */
export type ClosePayload = {
  actor: string;
  disposition: string;
  follow_up?: boolean;
  follow_up_kind?: string | null;
  reason?: string;
};

/** ConflictError */
export type ConflictError = {
  code: "AUTHENTICATION_REQUIRED" | "ROLE_DENIED" | "TENANT_SCOPE_DENIED" | "SCOPE_DENIED" | "OWNERSHIP_REQUIRED" | "ASSIGNMENT_SCOPE_DENIED" | "SOURCE_SCOPE_DENIED" | "FIELD_MASKED" | "DATA_CLASSIFICATION_DENIED" | "PURPOSE_REQUIRED" | "PRECONDITION_REQUIRED" | "VERSION_CONFLICT" | "WORKFLOW_STATE_DENIED" | "OWNER_CONFLICT" | "SECOND_ACTOR_REQUIRED" | "SELF_REVIEW_DENIED" | "RISK_ACKNOWLEDGEMENT_REQUIRED" | "SOURCE_POLICY_DENIED" | "SOURCE_POLICY_UNKNOWN" | "SOURCE_AUTH_REQUIRED" | "LEGAL_HOLD_CONFLICT" | "RETENTION_NOT_REACHED" | "RESIDENCY_DENIED" | "EXPORT_APPROVAL_REQUIRED" | "PURGE_APPROVAL_REQUIRED" | "QUARANTINE_RELEASE_DENIED" | "PROMOTION_APPROVAL_REQUIRED" | "RESTRICTED_EXPORT_DENIED" | "BREAK_GLASS_DENIED" | "DEPENDENCY_CONFLICT" | "DUPLICATE_CANDIDATE" | "IDEMPOTENCY_KEY_REUSED" | "RETRY_BUDGET_EXHAUSTED" | "CHECKPOINT_UNAVAILABLE" | "JOB_FENCE_REJECTED" | "SLA_PAUSE_DENIED" | "DECISION_INCOMPLETE" | "BACKPRESSURE_ACTIVE" | "RATE_LIMITED" | "RESOURCE_NOT_FOUND" | "VALIDATION_FAILED" | "FIELD_REQUIRED" | "CURSOR_INVALID" | "CURSOR_EXPIRED" | "INTERNAL_ERROR";
  correlation_id: string;
  current_owner_subject_id?: string | null;
  current_state?: string | null;
  current_version?: number | null;
  field_errors?: FieldError[];
  message: string;
  next_action: "RETRY" | "REFRESH" | "CORRECT_INPUT" | "REQUEST_ACCESS" | "CONTACT_SUPPORT" | "WAIT" | null;
  occurred_at: string;
  reason_code?: string | null;
  retry_after_seconds?: number | null;
  retry_with_etag?: string | null;
  retryable: boolean;
};

/** CorrectionReceipt */
export type CorrectionReceipt = {
  audit_event_id: string;
  correction_id: string;
  correlation_id: string;
  intake_id: string;
  listing_revision_id?: string | null;
  status: CorrectionStatus;
  version: number;
};

/** CorrectionRequest */
export type CorrectionRequest = {
  corrected_value: unknown;
  expected_effective_value_sha256?: string | null;
  field_path: string;
  reason: string;
  risk_acknowledged?: boolean;
};

/** CorrectionStatus */
export type CorrectionStatus = "PROPOSED" | "APPLIED" | "PENDING_REVIEW";

/** POST /operator/growth/actions — create draft body.

Supports all three creation entry points:
  • from PriceOps recommendation row  (sourceRecommendationId set)
  • from recommendations entry         (sourceRecommendationId set, payload-driven)
  • direct new-action entry            (no sourceRecommendationId) */
export type CreateActionPayload = {
  actorName?: string | null;
  actorRoleId?: string | null;
  budget?: number;
  channel?: string;
  kind?: string;
  name: string;
  objective: string;
  observationWindow?: string | null;
  observationWindowDays?: number;
  rationale?: string;
  rollbackPlan?: string;
  segmentId: string;
  sourceRecommendationId?: string | null;
  store?: string;
  targetLift: number;
};

/** DataRoomExportPayload */
export type DataRoomExportPayload = {
  actor: string;
  reason?: string;
};

/** DatasetSnapshotPayload */
export type DatasetSnapshotPayload = {
  dataset_snapshot_id?: string | null;
  require_training_eligible?: boolean;
  rows: Record<string, unknown>[];
};

/** DecisionReceipt */
export type DecisionReceipt = {
  audit_event_id: string;
  correlation_id: string;
  decision_id: string;
  job_id?: string | null;
  resource_versions: Record<string, number>;
  status: DecisionStatus;
};

/** DecisionStatus */
export type DecisionStatus = "PENDING_REVIEW" | "APPROVED" | "REJECTED" | "EXECUTING" | "EXECUTED" | "FAILED" | "REVERSAL_PENDING" | "REVERSED";

/** DecisionType */
export type DecisionType = "CREATE" | "REVISE" | "DUPLICATE" | "QUARANTINE" | "REJECT" | "REOPEN" | "MERGE" | "SPLIT" | "UNMERGE";

/** EligibilityPayload */
export type EligibilityPayload = {
  actor: string;
  eligible: boolean;
  reasons?: string[];
};

/** The single error contract for every endpoint. */
export type ErrorEnvelope = {
  /** Stable machine-readable code; clients branch on this. */
  code: string;
  /** Correlation ID; matches the X-Correlation-Id response header and audit log. */
  correlation_id?: string | null;
  /** Per-field or per-cause breakdown; empty when the error is not field-scoped. */
  details?: Record<string, unknown>[];
  /** Human-readable summary; safe to display to an operator. */
  message: string;
  /** What the caller should do next. */
  next_action: string;
  /** RFC3339 UTC timestamp of when the error was produced. */
  occurred_at: string;
};

/** Full error body: the structured envelope plus the legacy ``detail``.

Declared as a model so it lands in the OpenAPI artifact and therefore in the
generated TypeScript client, rather than being an undocumented dict. */
export type ErrorResponse = {
  /** Legacy detail, passed through exactly as the route produced it: a string, Pydantic's [{loc,msg,type}] array, or a route-specific object. Retained unchanged for existing consumers. New clients should read `error` instead. */
  detail: unknown;
  error: ErrorEnvelope;
};

/** EvaluatePayload */
export type EvaluatePayload = {
  actor: string;
  now?: string | null;
  replicated?: boolean;
};

/** EvidenceExportPayload */
export type EvidenceExportPayload = {
  authorization_id?: string | null;
  authorized_by?: string | null;
  build_version?: string;
  correlation_ids: string[];
  data_classification?: string;
  decision_cards: Record<string, unknown>[];
  environment?: string;
  expires_at?: string | null;
  export_scope: string;
  from_time: string;
  identity_boundary?: string | null;
  masking_profile?: string;
  program_id: string;
  purpose: string;
  purpose_scope?: string | null;
  requested_by: string;
  sensitive?: boolean;
  to_time: string;
};

/** EvidenceGovernancePayload */
export type EvidenceGovernancePayload = {
  correlation_id?: string | null;
  reason: string;
  role: string;
};

/** POST /operator/governance/evidence-package — export an Evidence Package. */
export type EvidencePackagePayload = {
  actorName?: string | null;
  contents?: string[];
  dateFrom: string;
  dateTo: string;
  format?: string;
  modules?: string[];
  retentionPolicy?: string | null;
  role?: string;
};

/** Write body for POST /operator/evidence/{evidence_id}/purpose.

privacyAcknowledged must be True for camera evidence kinds.
retentionHours must not exceed the policy ceiling (72 h default). */
export type EvidencePurposeRequest = {
  actorName?: string | null;
  actorRoleId: string;
  auditNote?: string | null;
  cameraLocation?: string | null;
  privacyAcknowledged?: boolean | null;
  purpose: string;
  retentionHours?: number | null;
  timeWindow?: string | null;
};

/** Response envelope for evidence purpose unlock. */
export type EvidencePurposeResponse = {
  auditEventId: string;
  correlationId?: string | null;
  evidenceId: string;
  purpose: string;
};

/** EvidenceRetentionPurgePayload */
export type EvidenceRetentionPurgePayload = {
  as_of: string;
  correlation_id?: string | null;
  reason: string;
  role: string;
};

/** ExecutePayload */
export type ExecutePayload = {
  executed_at?: string | null;
  executor: string;
};

/** ExportPayload */
export type ExportPayload = {
  authorizationId: string;
  authorizedBy: string;
  dataClassification?: string;
  destinationResidency?: string;
  maskingProfile?: string;
  purpose: string;
  sensitive?: boolean;
  subjectId: string;
  subjectType: string;
  tenantId: string;
};

/** FieldClassification */
export type FieldClassification = "PUBLIC" | "INTERNAL" | "CONFIDENTIAL" | "RESTRICTED";

/** FieldError */
export type FieldError = {
  code: string;
  field: string;
  message: string;
};

/** FieldValue */
export type FieldValue = {
  classification: FieldClassification;
  confidence?: number | null;
  corrected?: unknown | null;
  effective?: unknown | null;
  field_path: string;
  mask_reason_code?: string | null;
  masked: boolean;
  normalized?: unknown | null;
  parsed?: unknown | null;
};

/** FinanceApprovalPayload */
export type FinanceApprovalPayload = {
  actor: string;
  reason?: string;
  reserve_price?: number | null;
};

/** ForecastOpsAlertAcknowledgePayload */
export type ForecastOpsAlertAcknowledgePayload = {
  actor: string;
  note?: string | null;
};

/** ForecastOpsForecastJobPayload */
export type ForecastOpsForecastJobPayload = {
  idempotency_key?: string | null;
  inputs?: Record<string, unknown>[];
  prediction_origin_time?: string | null;
};

/** ForecastOpsHandoffExecutePayload */
export type ForecastOpsHandoffExecutePayload = {
  actor: string;
  intervention_id?: string | null;
};

/** ForecastOpsTimeseriesPayload */
export type ForecastOpsTimeseriesPayload = {
  observations?: Record<string, unknown>[];
};

/** Franchisee acknowledgement of a notification. */
export type FranchiseeAcknowledgeRequest = {
  notificationId: string;
  storeId?: string | null;
};

/** Franchisee field report. */
export type FranchiseeReportRequest = {
  category: string;
  message: string;
  storeId?: string | null;
};

/** HTTPValidationError */
export type HTTPValidationError = {
  detail?: ValidationError[];
};

/** HeatZoneScoreJobPayload */
export type HeatZoneScoreJobPayload = {
  features?: Record<string, unknown>[];
  idempotency_key?: string | null;
  prediction_origin_time?: string | null;
};

/** IdentityPartition */
export type IdentityPartition = {
  source_identity_edge_ids: string[];
  target_property_id: string | null;
};

/** IngestionRunPayload */
export type IngestionRunPayload = {
  idempotency_key?: string | null;
  provider_id?: string;
  schedule_id?: string;
  window_end?: string | null;
  window_start?: string | null;
};

/** IntakeCorrectPayload */
export type IntakeCorrectPayload = {
  actorName?: string | null;
  actorRoleId?: string;
  fields: Record<string, unknown>;
  reason?: string | null;
  riskAcknowledged?: boolean;
  riskSummary?: string | null;
};

/** IntakeDecidePayload */
export type IntakeDecidePayload = {
  action: string;
  actorName?: string | null;
  actorRoleId?: string;
  reason?: string | null;
  riskAcknowledged?: boolean;
  riskSummary?: string | null;
};

/** IntakeDetail */
export type IntakeDetail = {
  assigned_to?: string | null;
  assignment_id?: string | null;
  assignment_status?: string | null;
  audit: AuditReference[];
  canonical_url: string | null;
  due_at?: string | null;
  fields: FieldValue[];
  intake_id: string;
  intake_method: IntakeMethod;
  masked_fields?: string[];
  match_case_id?: string | null;
  match_outcome?: MatchOutcome | null;
  original_url: string | null;
  parser_run_id?: string | null;
  policy_state: SourcePolicyState | null;
  processing_history: TransitionReceipt[];
  scope: ScopeContext;
  sla_instance_id?: string | null;
  sla_receipt?: string | null;
  sla_state?: string | null;
  source_id?: string | null;
  source_snapshot_id?: string | null;
  state: IntakeState;
  submitted_at: string;
  submitted_by?: string;
  updated_at: string;
  version: number;
};

/** IntakeMethod */
export type IntakeMethod = "URL" | "MANUAL" | "CSV" | "APPROVED_FEED" | "OPERATOR_SNAPSHOT";

/** IntakePage */
export type IntakePage = {
  items: IntakeSummary[];
  next_cursor?: string | null;
  page_size: number;
  query_fingerprint: string;
  snapshot_time: string;
  total_count: number;
  total_count_accuracy?: TotalCountAccuracy;
};

/** Promotion carries no extra fields beyond the risk disclosure. */
export type IntakePromotePayload = {
  actorName?: string | null;
  actorRoleId?: string;
  reason?: string | null;
  riskAcknowledged?: boolean;
  riskSummary?: string | null;
};

/** IntakeSort */
export type IntakeSort = "submitted_at_desc" | "updated_at_desc" | "due_at_asc" | "status_asc";

/** IntakeState */
export type IntakeState = "SUBMITTED" | "CHECKING_IDENTITY" | "CHECKING_SOURCE_POLICY" | "AWAITING_ASSISTED_ENTRY" | "RETRIEVING" | "PARSING" | "MATCHING" | "NEEDS_REVIEW" | "READY" | "QUARANTINED" | "FAILED" | "CANCELLED";

/** IntakeSubmissionReceipt */
export type IntakeSubmissionReceipt = {
  correlation_id: string;
  duplicate_hint?: string | null;
  intake_id: string;
  job_id: string;
  state: IntakeState;
  submitted_at: string;
  version: number;
};

/** IntakeSubmitPayload */
export type IntakeSubmitPayload = {
  actorName?: string | null;
  actorRoleId?: string;
  heatZoneId?: string | null;
  url: string;
};

/** IntakeSummary */
export type IntakeSummary = {
  assigned_to?: string | null;
  due_at?: string | null;
  intake_id: string;
  intake_method: IntakeMethod;
  masked_fields?: string[];
  match_outcome?: MatchOutcome | null;
  scope: ScopeContext;
  source_id?: string | null;
  state: IntakeState;
  submitted_at: string;
  submitted_by?: string;
  updated_at: string;
  version: number;
};

/** Write body for POST /operator/issues/{issue_id}/{action_type}. */
export type IssueTransitionRequest = {
  actorName?: string | null;
  actorRoleId: string;
  issueId?: string | null;
  note?: string | null;
  status?: string | null;
};

/** Response envelope for issue transition writes. */
export type IssueTransitionResponse = {
  auditEventId: string;
  correlationId?: string | null;
  issueId: string;
  newStatus: string;
};

/** JobCreatePayload */
export type JobCreatePayload = {
  idempotency_key?: string | null;
  job_type: string;
  payload?: Record<string, unknown>;
};

/** JobReceipt */
export type JobReceipt = {
  attempt: number;
  checkpoint: string;
  correlation_id: string;
  job_id: string;
  status: JobStatus;
  version: number;
};

/** JobStatus */
export type JobStatus = "QUEUED" | "RUNNING" | "RETRYING" | "SUCCEEDED" | "FAILED" | "CANCELLED" | "DEAD_LETTER";

/** ListingImportPayload */
export type ListingImportPayload = {
  records?: Record<string, unknown>[];
  source_id?: string | null;
};

/** ManualIntakeRow */
export type ManualIntakeRow = {
  address_raw: string;
  area_ping?: number | null;
  currency?: string;
  floor?: string | null;
  original_url?: string | null;
  rent_amount?: number | null;
  source_id?: string;
  source_listing_id?: string | null;
};

/** MatchDecisionRequest */
export type MatchDecisionRequest = {
  decision_type: DecisionType;
  reason: string;
  requested_second_reviewer_id?: string | null;
  risk_acknowledged?: boolean;
  target_listing_id?: string | null;
  target_property_id?: string | null;
};

/** MatchOutcome */
export type MatchOutcome = "NEW" | "EXACT_DUPLICATE" | "REVISION" | "POSSIBLE_MATCH" | "QUARANTINED";

/** MergeRequest */
export type MergeRequest = {
  candidate_reassignment_plan?: CandidateReassignment[];
  expected_property_versions?: Record<string, number>;
  reason: string;
  risk_acknowledged: true;
  source_property_ids: string[];
  target_property_id: string;
};

/** ModelCardPayload */
export type ModelCardPayload = {
  algorithm: string;
  approvals?: Record<string, string>[];
  baseline: string;
  calibration_summary?: Record<string, unknown>;
  explainability_method?: string;
  feature_set_id: string;
  intended_use: string;
  known_biases?: string[];
  label_set_id: string;
  limitations?: string[];
  metrics_summary: Record<string, number>;
  not_intended_use: string;
  owner: string;
  privacy_review?: string;
  release_status?: string;
  risk_level?: string;
  rollback_conditions: string[];
  security_review?: string;
  segment_metrics?: Record<string, unknown>[];
  training_period: string;
  validation_period: string;
};

/** ModelVersionPayload */
export type ModelVersionPayload = {
  artifact_content: string;
  artifact_content_type?: string;
  artifact_kind?: string;
  artifact_metadata?: Record<string, unknown>;
  baseline_metrics: Record<string, number>;
  calibration_summary?: Record<string, unknown>;
  dataset_snapshot_id: string;
  feature_schema_version: string;
  git_sha?: string | null;
  label_version: string;
  metrics: Record<string, number>;
  min_training_records?: number;
  model_card: ModelCardPayload;
  monitoring_config?: Record<string, unknown>;
  rollback_target?: string | null;
  run_id?: string | null;
  segment_metrics?: SegmentMetricPayload[];
  stage?: string;
  thresholds: ThresholdPayload[];
  version: string;
};

/** MonitorGuardrailPayload */
export type MonitorGuardrailPayload = {
  max_value?: number | null;
  metric_name: string;
  min_value?: number | null;
  warning_max_value?: number | null;
  warning_min_value?: number | null;
};

/** NetPlanActorPayload */
export type NetPlanActorPayload = {
  actor?: string;
  occurred_at?: string | null;
  reason?: string;
};

/** NetPlanDecisionPayload */
export type NetPlanDecisionPayload = {
  actor_id: string;
  decided_at?: string | null;
  decision?: string;
  reason: string;
};

/** NetPlanExecutionPayload */
export type NetPlanExecutionPayload = {
  executed_at?: string | null;
  executed_by?: string;
};

/** NetPlanOutcomePayload */
export type NetPlanOutcomePayload = {
  actor?: string;
  actual_gross_margin: number;
  observed_at?: string | null;
  source_snapshot_ids?: string[];
};

/** NetPlanScenarioPayload */
export type NetPlanScenarioPayload = {
  candidate_sites?: Record<string, unknown>[];
  constraints: Record<string, unknown>;
  created_at?: string | null;
  existing_stores?: Record<string, unknown>[];
  planning_horizon: string;
  scenario_id?: string | null;
  scenario_name: string;
  tenant_id: string;
};

/** NetPlanSolvePayload */
export type NetPlanSolvePayload = {
  actor?: string;
  alternative_limit?: number;
  reason?: string;
  solved_at?: string | null;
};

/** NetworkListingActorPayload */
export type NetworkListingActorPayload = {
  actorName?: string | null;
  actorRoleId?: string;
  reason?: string | null;
};

/** NetworkListingMergePayload */
export type NetworkListingMergePayload = {
  actorName?: string | null;
  actorRoleId?: string;
  reason?: string | null;
  riskAcknowledged?: boolean;
  riskSummary?: string | null;
  targetListingId: string;
};

/** NetworkScoringActorPayload */
export type NetworkScoringActorPayload = {
  actorName?: string | null;
  actorRoleId?: string;
};

/** NetworkScoringBatchPayload */
export type NetworkScoringBatchPayload = {
  actorName?: string | null;
  actorRoleId?: string;
  candidateIds?: string[] | null;
};

/** NetworkScoringComparePayload */
export type NetworkScoringComparePayload = {
  actorName?: string | null;
  actorRoleId?: string;
  candidateIds: string[];
};

/** Per-role notification delivery preferences. */
export type NotificationPreferencesRequest = {
  channels: Record<string, boolean>;
  digest?: string;
  severityFloor?: string;
};

/** OpenCasePayload */
export type OpenCasePayload = {
  action_spec?: Record<string, unknown>;
  created_by: string;
  expected_outcome: string;
  idempotency_key?: string | null;
  kind: string;
  planned_end: string;
  planned_start: string;
  store_id: string;
  trigger_ref?: string;
};

/** OpenDecisionPayload */
export type OpenDecisionPayload = {
  created_by: string;
  report_id: string;
};

/** PlaceHoldPayload */
export type PlaceHoldPayload = {
  approvedBy: string;
  reason: string;
  subjectId: string;
  subjectType: string;
  tenantId: string;
};

/** PriceOpsActivationPayload */
export type PriceOpsActivationPayload = {
  executed_at?: string | null;
  executor?: string;
  intervention_type?: string;
  label_maturity_time?: string | null;
  measurement_method?: string;
};

/** PriceOpsActorPayload */
export type PriceOpsActorPayload = {
  actor?: string;
  occurred_at?: string | null;
  reason?: string;
};

/** PriceOpsApprovalPayload */
export type PriceOpsApprovalPayload = {
  actor_id: string;
  approved_at?: string | null;
  decision?: string;
  reason: string;
};

/** PriceOpsEvaluationPayload */
export type PriceOpsEvaluationPayload = {
  actor?: string;
  actual_gross_margin: number;
  evidence_level?: string;
  generated_at?: string | null;
  measurement_method?: string;
  negative_impact_threshold?: number;
  outcome_window_end?: string | null;
  outcome_window_start?: string | null;
};

/** PriceOpsObservationPayload */
export type PriceOpsObservationPayload = {
  actor?: string;
  start_time?: string | null;
  stop_conditions?: Record<string, unknown>;
};

/** PriceOpsOptimizerJobPayload */
export type PriceOpsOptimizerJobPayload = {
  idempotency_key?: string | null;
  optimized_at?: string | null;
  plans: PriceOpsPlanPayload[];
};

/** PriceOpsPlanItemPayload */
export type PriceOpsPlanItemPayload = {
  baseline_demand: number;
  confidence?: number | null;
  current_price: number;
  elasticity_value?: number | null;
  horizon?: string;
  item_id?: string | null;
  machine_type: string;
  margin_floor_ratio?: number;
  max_decrease_pct?: number;
  max_increase_pct?: number;
  max_price?: number | null;
  min_price?: number | null;
  prediction_origin_time?: string | null;
  price_demand_observations?: Record<string, number>[] | null;
  price_ladder_step?: number;
  store_id: string;
  unit_cost: number;
};

/** PriceOpsPlanPayload */
export type PriceOpsPlanPayload = {
  created_at?: string | null;
  idempotency_key?: string | null;
  items: PriceOpsPlanItemPayload[];
  plan_id?: string | null;
  tenant_id: string;
};

/** PromotionDecisionReceipt */
export type PromotionDecisionReceipt = {
  audit_event_id: string;
  candidate_site_id?: string | null;
  correlation_id: string;
  decision_type: PromotionDecisionType;
  intake_id: string;
  listing_id: string;
  promotion_decision_id: string;
  proposer_subject_id: string;
  reviewer_subject_id?: string | null;
  site_score_job_id?: string | null;
  status: PromotionStatus;
  version: number;
};

/** PromotionDecisionType */
export type PromotionDecisionType = "STANDARD" | "LEGACY_RECONCILED";

/** PromotionRequest */
export type PromotionRequest = {
  gate_snapshot_sha256: string;
  reason: string;
  requested_reviewer_id?: string | null;
  risk_acknowledged?: boolean;
  target_format_code: string;
};

/** PromotionStatus */
export type PromotionStatus = "REQUESTED" | "VALIDATING" | "PENDING_REVIEW" | "REJECTED" | "APPROVED" | "CANDIDATE_CREATING" | "CANDIDATE_CREATED" | "SCORE_QUEUED" | "COMPLETED" | "FAILED" | "SCORE_FAILED";

/** PurgePayload */
export type PurgePayload = {
  approvedBy: string;
  dryRun?: boolean;
  reason: string;
  subjectId: string;
  subjectType: string;
  tenantId: string;
};

/** ReasonCommand */
export type ReasonCommand = {
  reason: string;
};

/** RebalanceActorPayload */
export type RebalanceActorPayload = {
  actorName?: string | null;
  actorRoleId?: string;
  reason?: string | null;
  simulateUnavailable?: boolean;
};

/** RebalanceSubmitPayload */
export type RebalanceSubmitPayload = {
  actorName?: string | null;
  actorRoleId?: string;
  reason: string;
  simulateUnavailable?: boolean;
};

/** ReleaseHoldPayload */
export type ReleaseHoldPayload = {
  approvedBy: string;
  reason: string;
  subjectId: string;
  subjectType: string;
  tenantId: string;
};

/** ReleaseMonitorPayload */
export type ReleaseMonitorPayload = {
  evaluated_by?: string;
  guardrails: MonitorGuardrailPayload[];
  observed_metrics: Record<string, number>;
};

/** ReleasePayload */
export type ReleasePayload = {
  affected_modules?: string[];
  approval_id: string;
  approved_by?: string;
  fail_criteria: string[];
  model_name: string;
  monitoring_window: string;
  reason: string;
  release_type: string;
  requested_by?: string;
  rollback_target?: string | null;
  success_criteria: string[];
  version: string;
};

/** RetryCheckpoint */
export type RetryCheckpoint = "RETRIEVING" | "PARSING" | "MATCHING" | "CANDIDATE_CREATING" | "SCORE_QUEUED";

/** RetryRequest */
export type RetryRequest = {
  checkpoint: RetryCheckpoint;
  override_retry_budget?: boolean;
  reason: string;
  risk_acknowledged?: boolean;
};

/** ReviewDecision */
export type ReviewDecision = "APPROVE" | "REJECT";

/** POST /operator/network-reviews/{review_id}/decide. */
export type ReviewDecisionPayload = {
  actorName?: string | null;
  actorRoleId?: string;
  conditions?: string | null;
  decision: string;
  overrideAck?: boolean;
  reason?: string;
  requiredData?: string[];
};

/** ReviewDecisionRequest */
export type ReviewDecisionRequest = {
  decision: ReviewDecision;
  reason: string;
  requested_changes?: string[];
  risk_acknowledged?: boolean;
};

/** RiskReasonCommand */
export type RiskReasonCommand = {
  incident_or_change_id?: string | null;
  reason: string;
  risk_acknowledged: true;
};

/** Override a role's workspace grants (high-risk, audited). */
export type RoleWorkspacesRequest = {
  allowedWorkspaces: string[];
};

/** SavedView */
export type SavedView = {
  created_at: string;
  name: string;
  owner_subject_id: string;
  query: Record<string, unknown>;
  resource: "intake";
  saved_view_id: string;
  shared_role?: string | null;
  version: number;
  visibility?: SavedViewVisibility;
};

/** SavedViewRequest */
export type SavedViewRequest = {
  name: string;
  query: Record<string, unknown>;
  resource: "intake";
  shared_role?: string | null;
  visibility?: SavedViewVisibility;
};

/** SavedViewVisibility */
export type SavedViewVisibility = "PRIVATE" | "ROLE" | "TENANT";

/** ScopeContext */
export type ScopeContext = {
  assigned_area_id?: string | null;
  brand_id?: string | null;
  heat_zone_id?: string | null;
  region_id?: string | null;
  tenant_id: string;
};

/** SegmentMetricPayload */
export type SegmentMetricPayload = {
  metrics: Record<string, number>;
  record_count: number;
  segment_name: string;
  segment_value: string;
};

/** Workspace settings patch. */
export type SettingsRequest = {
  values: Record<string, unknown>;
};

/** SiteScoreScoreJobPayload */
export type SiteScoreScoreJobPayload = {
  features?: Record<string, unknown>[];
  idempotency_key?: string | null;
  prediction_origin_time?: string | null;
};

/** SlaPauseRequest */
export type SlaPauseRequest = {
  expected_resume_at: string;
  reason: string;
};

/** SlaReceipt */
export type SlaReceipt = {
  active_pause_interval_id?: string | null;
  audit_event_id: string;
  correlation_id: string;
  due_at: string;
  due_soon_at?: string | null;
  paused_duration_seconds: number;
  sla_instance_id: string;
  state: SlaState;
  version: number;
};

/** SlaState */
export type SlaState = "ON_TRACK" | "DUE_SOON" | "OVERDUE" | "BREACHED" | "PAUSED" | "COMPLETED";

/** SourcePolicyState */
export type SourcePolicyState = "APPROVED_RETRIEVAL" | "ASSISTED_ENTRY_ONLY" | "AUTH_REQUIRED" | "SOURCE_BLOCKED" | "POLICY_UNKNOWN";

/** SplitRequest */
export type SplitRequest = {
  partitions: IdentityPartition[];
  reason: string;
  risk_acknowledged: true;
  source_property_id: string;
  source_property_version?: number;
};

/** StoreOpsCameraPurposePayload */
export type StoreOpsCameraPurposePayload = {
  actorName?: string | null;
  actorRoleId?: string | null;
  auditNote?: string | null;
  cameraLocation?: string | null;
  issueId?: string | null;
  issueTitle?: string | null;
  privacyAcknowledged?: boolean;
  purpose: string;
  retentionHours?: number | null;
  storeId?: string | null;
  storeName?: string | null;
  timeWindow?: string | null;
};

/** StoreOpsTransitionPayload */
export type StoreOpsTransitionPayload = {
  actorName?: string | null;
  actorRoleId?: string | null;
  issueId?: string | null;
  issueTitle?: string | null;
  payload?: Record<string, unknown>;
  storeId?: string | null;
  storeName?: string | null;
};

/** Assign a Task Center task to a role/subject. */
export type TaskAssignRequest = {
  assigneeId: string;
  assigneeName?: string | null;
  slaDueAt?: string | null;
};

/** ThresholdPayload */
export type ThresholdPayload = {
  max_value?: number | null;
  metric_name: string;
  min_value?: number | null;
  warning_max_value?: number | null;
  warning_min_value?: number | null;
};

/** TotalCountAccuracy */
export type TotalCountAccuracy = "EXACT" | "ESTIMATED";

/** POST /operator/growth/actions/{id}/transition — lifecycle advance. */
export type TransitionActionPayload = {
  actorName?: string | null;
  actorRoleId?: string | null;
  targetStatus: string;
};

/** TransitionReceipt */
export type TransitionReceipt = {
  actor: string;
  from_state: string | null;
  occurred_at: string;
  reason_code?: string | null;
  to_state: string;
  transition_id: string;
  version_after: number;
};

/** UnmergeRequest */
export type UnmergeRequest = {
  original_decision_id: string;
  reason: string;
  replacement_edges: IdentityPartition[];
  risk_acknowledged: true;
};

/** UrlIntakeRequest */
export type UrlIntakeRequest = {
  original_url: string;
  owner_subject_id?: string | null;
  purpose?: string;
  scope: ScopeContext;
};

/** ValidationError */
export type ValidationError = {
  ctx?: Record<string, unknown>;
  input?: unknown;
  loc: (string | number)[];
  msg: string;
  type: string;
};

/** ConflictCheckPayload */
export type apps__api__app__routes__interventions__ConflictCheckPayload = {
  actor: string;
  allow_overlap?: boolean;
  reason?: string;
};

/** DecisionPayload */
export type apps__api__app__routes__interventions__DecisionPayload = {
  action: string;
  actor: string;
  reason?: string;
};

/** OutcomePayload */
export type apps__api__app__routes__interventions__OutcomePayload = {
  actor: string;
  ad_spend?: number;
  control_store_count?: number;
  evaluation_method?: string;
  has_control_group?: boolean;
  incremental_gross_margin?: number;
  incremental_revenue?: number;
  measurement_method?: string | null;
  pretrend_status?: string;
  randomized?: boolean;
  treatment_store_count?: number;
};

/** SubmitPayload */
export type apps__api__app__routes__interventions__SubmitPayload = {
  actor: string;
};

/** POST /operator/governance/decisions — approve / return / reject. */
export type apps__api__app__routes__operator_modules__governance__DecisionPayload = {
  action: string;
  actorName?: string | null;
  approvalId: string;
  reason?: string;
  role?: string;
};

/** POST /operator/growth/conflicts/check — run the five conflict checks. */
export type apps__api__app__routes__operator_modules__growth__ConflictCheckPayload = {
  budget?: number;
  channel?: string;
  excludeActionId?: string | null;
  kind?: string;
  observationWindow?: string;
  store?: string;
};

/** POST /operator/growth/actions/{id}/outcome — effectiveness writeback. */
export type apps__api__app__routes__operator_modules__growth__OutcomePayload = {
  actorName?: string | null;
  actorRoleId?: string | null;
  evidenceLevel?: string;
  observedLift?: number | null;
  outcome: string;
  rationale?: string;
  requiredAction: string;
};

/** POST /operator/growth/actions/{id}/submit — submit draft for approval. */
export type apps__api__app__routes__operator_modules__growth__SubmitPayload = {
  actorName?: string | null;
  actorRoleId?: string | null;
};

/** DecisionPayload */
export type apps__api__app__routes__sitescore__DecisionPayload = {
  action: string;
  actor: string;
  reason?: string;
};



/** Every versioned operation the API serves, and its methods. */
export const API_PATHS = {
  "/api/v1/adlift/incrementality-jobs": ["POST"],
  "/api/v1/adlift/incrementality-jobs/{job_id}": ["GET"],
  "/api/v1/adlift/reports": ["GET"],
  "/api/v1/adlift/reports/{campaign_id}": ["GET"],
  "/api/v1/assignments/{assignment_id}/actions/claim": ["POST"],
  "/api/v1/assignments/{assignment_id}/actions/complete": ["POST"],
  "/api/v1/assignments/{assignment_id}/actions/transfer": ["POST"],
  "/api/v1/audit/events": ["GET"],
  "/api/v1/audit/evidence/export": ["POST"],
  "/api/v1/audit/evidence/exports": ["GET"],
  "/api/v1/audit/evidence/exports/{export_id}": ["GET"],
  "/api/v1/audit/evidence/exports/{export_id}/legal-hold": ["POST"],
  "/api/v1/audit/evidence/retention/expired": ["GET"],
  "/api/v1/audit/evidence/retention/purge": ["POST"],
  "/api/v1/avm/cases": ["GET", "POST"],
  "/api/v1/avm/cases/{case_id}": ["GET"],
  "/api/v1/avm/cases/{case_id}/dataroom": ["GET", "POST"],
  "/api/v1/avm/cases/{case_id}/dataroom/export": ["POST"],
  "/api/v1/avm/cases/{case_id}/finance-approval": ["POST"],
  "/api/v1/avm/cases/{case_id}/normalize": ["POST"],
  "/api/v1/avm/cases/{case_id}/report": ["GET"],
  "/api/v1/avm/cases/{case_id}/reports": ["GET"],
  "/api/v1/avm/cases/{case_id}/value": ["POST"],
  "/api/v1/external-data/freshness": ["GET"],
  "/api/v1/external-data/ingestion-runs": ["GET", "POST"],
  "/api/v1/external-data/ingestion-runs/{run_id}": ["GET"],
  "/api/v1/external-data/quarantine": ["GET"],
  "/api/v1/forecastops/alerts": ["GET"],
  "/api/v1/forecastops/alerts/{alert_id}/acknowledge": ["POST"],
  "/api/v1/forecastops/forecast-jobs": ["POST"],
  "/api/v1/forecastops/forecast-jobs/{job_id}": ["GET"],
  "/api/v1/forecastops/forecast-outputs/{forecast_output_id}": ["GET"],
  "/api/v1/forecastops/forecasts": ["GET"],
  "/api/v1/forecastops/intervention-handoffs": ["GET"],
  "/api/v1/forecastops/intervention-handoffs/{handoff_id}/execute": ["POST"],
  "/api/v1/forecastops/prediction-runs/{prediction_run_id}": ["GET"],
  "/api/v1/forecastops/timeseries": ["GET", "POST"],
  "/api/v1/heatzones": ["GET"],
  "/api/v1/heatzones/map": ["GET"],
  "/api/v1/heatzones/score-jobs": ["POST"],
  "/api/v1/heatzones/snapshots/{snapshot_id}": ["GET"],
  "/api/v1/heatzones/{h3_index}": ["GET"],
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
  "/api/v1/interventions": ["GET", "POST"],
  "/api/v1/interventions/{intervention_id}": ["GET"],
  "/api/v1/interventions/{intervention_id}/action": ["POST"],
  "/api/v1/interventions/{intervention_id}/approve": ["POST"],
  "/api/v1/interventions/{intervention_id}/close": ["POST"],
  "/api/v1/interventions/{intervention_id}/conflict-check": ["POST"],
  "/api/v1/interventions/{intervention_id}/eligibility": ["POST"],
  "/api/v1/interventions/{intervention_id}/evaluate": ["POST"],
  "/api/v1/interventions/{intervention_id}/execute": ["POST"],
  "/api/v1/interventions/{intervention_id}/label": ["GET"],
  "/api/v1/interventions/{intervention_id}/outcomes": ["POST"],
  "/api/v1/interventions/{intervention_id}/submit": ["POST"],
  "/api/v1/jobs": ["POST"],
  "/api/v1/jobs/{job_id}": ["GET"],
  "/api/v1/jobs/{job_id}/receipt": ["GET"],
  "/api/v1/jobs/{job_id}/retry": ["POST"],
  "/api/v1/learninghub/dataset-snapshots": ["POST"],
  "/api/v1/learninghub/models": ["GET"],
  "/api/v1/learninghub/models/{model_name}": ["GET"],
  "/api/v1/learninghub/models/{model_name}/evidence": ["GET"],
  "/api/v1/learninghub/models/{model_name}/versions": ["POST"],
  "/api/v1/learninghub/oss-capabilities": ["GET"],
  "/api/v1/learninghub/releases": ["GET", "POST"],
  "/api/v1/learninghub/releases/{release_id}/monitor": ["POST"],
  "/api/v1/listings/candidates": ["GET"],
  "/api/v1/listings/import": ["POST"],
  "/api/v1/listings/import-jobs": ["POST"],
  "/api/v1/match-cases/{match_case_id}/decisions": ["POST"],
  "/api/v1/netplan/scenarios": ["GET", "POST"],
  "/api/v1/netplan/scenarios/{scenario_id}": ["GET"],
  "/api/v1/netplan/scenarios/{scenario_id}/close": ["POST"],
  "/api/v1/netplan/scenarios/{scenario_id}/decide": ["POST"],
  "/api/v1/netplan/scenarios/{scenario_id}/execute": ["POST"],
  "/api/v1/netplan/scenarios/{scenario_id}/outcomes": ["POST"],
  "/api/v1/netplan/scenarios/{scenario_id}/solve": ["POST"],
  "/api/v1/netplan/scenarios/{scenario_id}/submit": ["POST"],
  "/api/v1/operator/approvals": ["GET"],
  "/api/v1/operator/approvals/{approval_id}/decision": ["POST"],
  "/api/v1/operator/bootstrap": ["GET"],
  "/api/v1/operator/evidence/{evidence_id}/purpose": ["POST"],
  "/api/v1/operator/governance/decisions": ["POST"],
  "/api/v1/operator/governance/evidence-package": ["POST"],
  "/api/v1/operator/governance/evidence-packages": ["GET"],
  "/api/v1/operator/governance/snapshot": ["GET"],
  "/api/v1/operator/growth/actions": ["GET", "POST"],
  "/api/v1/operator/growth/actions/{action_id}": ["GET"],
  "/api/v1/operator/growth/actions/{action_id}/outcome": ["POST"],
  "/api/v1/operator/growth/actions/{action_id}/submit": ["POST"],
  "/api/v1/operator/growth/actions/{action_id}/transition": ["POST"],
  "/api/v1/operator/growth/approvals": ["GET"],
  "/api/v1/operator/growth/approvals/{approval_id}/decision": ["POST"],
  "/api/v1/operator/growth/conflicts/check": ["POST"],
  "/api/v1/operator/growth/decisions": ["GET"],
  "/api/v1/operator/growth/freshness": ["GET"],
  "/api/v1/operator/growth/recommendations": ["GET"],
  "/api/v1/operator/growth/segments": ["GET"],
  "/api/v1/operator/growth/summary": ["GET"],
  "/api/v1/operator/issues": ["GET"],
  "/api/v1/operator/issues/{issue_id}/{action_type}": ["POST"],
  "/api/v1/operator/network-listings": ["GET"],
  "/api/v1/operator/network-listings/": ["GET"],
  "/api/v1/operator/network-listings/intake": ["GET"],
  "/api/v1/operator/network-listings/intake/submit": ["POST"],
  "/api/v1/operator/network-listings/intake/{intake_id}": ["GET"],
  "/api/v1/operator/network-listings/intake/{intake_id}/correct": ["POST"],
  "/api/v1/operator/network-listings/intake/{intake_id}/decide": ["POST"],
  "/api/v1/operator/network-listings/intake/{intake_id}/promote": ["POST"],
  "/api/v1/operator/network-listings/intake/{intake_id}/retry": ["POST"],
  "/api/v1/operator/network-listings/listings/{listing_id}/archive": ["POST"],
  "/api/v1/operator/network-listings/listings/{listing_id}/convert": ["POST"],
  "/api/v1/operator/network-listings/listings/{listing_id}/merge": ["POST"],
  "/api/v1/operator/network-listings/reset": ["POST"],
  "/api/v1/operator/network-rebalance": ["GET"],
  "/api/v1/operator/network-rebalance/": ["GET"],
  "/api/v1/operator/network-rebalance/reset": ["POST"],
  "/api/v1/operator/network-rebalance/stores/{store_id}/avm/complete": ["POST"],
  "/api/v1/operator/network-rebalance/stores/{store_id}/avm/request": ["POST"],
  "/api/v1/operator/network-rebalance/stores/{store_id}/netplan/solve": ["POST"],
  "/api/v1/operator/network-rebalance/stores/{store_id}/scenarios/{scenario_id}/select": ["POST"],
  "/api/v1/operator/network-rebalance/stores/{store_id}/submit-review": ["POST"],
  "/api/v1/operator/network-reviews": ["GET"],
  "/api/v1/operator/network-reviews/": ["GET"],
  "/api/v1/operator/network-reviews/reset": ["POST"],
  "/api/v1/operator/network-reviews/{review_id}/decide": ["POST"],
  "/api/v1/operator/network-scoring": ["GET"],
  "/api/v1/operator/network-scoring/": ["GET"],
  "/api/v1/operator/network-scoring/candidates/{candidate_id}/score": ["POST"],
  "/api/v1/operator/network-scoring/compare": ["POST"],
  "/api/v1/operator/network-scoring/reset": ["POST"],
  "/api/v1/operator/network-scoring/score": ["POST"],
  "/api/v1/operator/privacy/export": ["POST"],
  "/api/v1/operator/privacy/export/download/{download_evidence_id}": ["GET"],
  "/api/v1/operator/privacy/export/verify/{export_id}": ["GET"],
  "/api/v1/operator/privacy/hold": ["POST"],
  "/api/v1/operator/privacy/hold/release": ["POST"],
  "/api/v1/operator/privacy/purge": ["POST"],
  "/api/v1/operator/search": ["GET"],
  "/api/v1/operator/seed/reset": ["POST"],
  "/api/v1/operator/shell/admin": ["GET"],
  "/api/v1/operator/shell/admin/roles/{target_role_id}/workspaces": ["PUT"],
  "/api/v1/operator/shell/franchisee": ["GET"],
  "/api/v1/operator/shell/franchisee/acknowledgement": ["POST"],
  "/api/v1/operator/shell/franchisee/reports": ["POST"],
  "/api/v1/operator/shell/home": ["GET"],
  "/api/v1/operator/shell/notifications": ["GET"],
  "/api/v1/operator/shell/notifications/preferences": ["GET", "PUT"],
  "/api/v1/operator/shell/notifications/{notification_id}/acknowledgement": ["POST"],
  "/api/v1/operator/shell/search": ["GET"],
  "/api/v1/operator/shell/settings": ["GET", "PUT"],
  "/api/v1/operator/shell/tasks": ["GET"],
  "/api/v1/operator/shell/tasks/{task_id}/assignment": ["POST"],
  "/api/v1/operator/store-ops/issues": ["GET"],
  "/api/v1/operator/store-ops/issues/{issue_id}": ["GET"],
  "/api/v1/operator/store-ops/issues/{issue_id}/camera-purpose": ["POST"],
  "/api/v1/operator/store-ops/issues/{issue_id}/evidence": ["GET"],
  "/api/v1/operator/store-ops/issues/{issue_id}/{action_type}": ["POST"],
  "/api/v1/operator/store-ops/summary": ["GET"],
  "/api/v1/operator/today": ["GET"],
  "/api/v1/priceops/optimizer-jobs": ["POST"],
  "/api/v1/priceops/optimizer-jobs/{job_id}": ["GET"],
  "/api/v1/priceops/plans": ["GET", "POST"],
  "/api/v1/priceops/plans/{plan_id}": ["GET"],
  "/api/v1/priceops/plans/{plan_id}/activate": ["POST"],
  "/api/v1/priceops/plans/{plan_id}/approve": ["POST"],
  "/api/v1/priceops/plans/{plan_id}/comparison": ["GET"],
  "/api/v1/priceops/plans/{plan_id}/evaluate": ["POST"],
  "/api/v1/priceops/plans/{plan_id}/observation": ["POST"],
  "/api/v1/priceops/plans/{plan_id}/optimize": ["POST"],
  "/api/v1/priceops/plans/{plan_id}/rollback": ["POST"],
  "/api/v1/priceops/plans/{plan_id}/simulate": ["POST"],
  "/api/v1/priceops/plans/{plan_id}/submit": ["POST"],
  "/api/v1/promotion-decisions/{promotion_decision_id}": ["GET"],
  "/api/v1/promotion-decisions/{promotion_decision_id}/actions/review": ["POST"],
  "/api/v1/saved-views": ["GET", "POST"],
  "/api/v1/sitescore/decisions": ["POST"],
  "/api/v1/sitescore/decisions/{decision_id}": ["GET"],
  "/api/v1/sitescore/decisions/{decision_id}/decision": ["POST"],
  "/api/v1/sitescore/prediction-runs/{prediction_run_id}": ["GET"],
  "/api/v1/sitescore/realized": ["GET"],
  "/api/v1/sitescore/reports": ["GET", "POST"],
  "/api/v1/sitescore/reports/{candidate_site_id}": ["GET"],
  "/api/v1/sitescore/runs/{sitescore_run_id}": ["GET"],
  "/api/v1/sitescore/score-jobs": ["POST"],
  "/api/v1/sla-instances/{sla_instance_id}/actions/pause": ["POST"],
  "/api/v1/sla-instances/{sla_instance_id}/actions/resume": ["POST"],
} as const;

export type ApiPath = keyof typeof API_PATHS;
