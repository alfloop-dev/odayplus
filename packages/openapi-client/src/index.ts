/**
 * @oday-plus/openapi-client
 *
 * Typed client for the ODay Plus FastAPI backend (apps/api/oday_api). It has no
 * runtime dependencies beyond the platform `fetch`, so it runs inside Next.js
 * server components, client components, the Playwright test runner, or plain
 * Node.
 *
 * Client-component use (ODP-OC-R5-011): pass identity through
 * `defaultHeaders` and a same-origin `baseUrl` so the request goes through
 * the web app's /api/v1 rewrite. Never embed a credential here — the console
 * derives its headers from the active operator role.
 *
 * Type provenance (ODP-PGAP-API-001)
 * ----------------------------------
 * `./generated/types` is generated from `openapi.json`, which is exported from
 * the live app. It is re-exported wholesale below and is the source of truth
 * for request DTOs, the error envelope, and the versioned path map. Never edit
 * it; run `scripts/openapi/generate_client.py`.
 *
 * Two categories of type are still hand-written here:
 *
 * 1. **Narrowings.** A few request DTOs are declared more strictly than the
 *    server's schema admits, because a Pydantic field default renders as
 *    `optional` even where the server rejects the request at runtime without
 *    it — `riskAcknowledged` is the sharp case. These shadow their generated
 *    namesakes and are pinned by the `AssertAssignable` checks below, so a
 *    server-side shape change breaks the build rather than the caller.
 *
 * 2. **Response DTOs.** Every route is annotated `-> dict[str, Any]`, so the
 *    artifact describes all 156 success responses as `additionalProperties:
 *    true` — there is no response shape to generate from. Those types remain
 *    hand-written and are quarantined in `./handwritten`, re-exported here for
 *    compatibility. Declaring `response_model=` per route is the fix and is
 *    tracked as a follow-up; it cannot be applied mechanically, because
 *    `response_model` filters the response to the declared fields and an
 *    incomplete model would silently drop data the console renders.
 */

// Generated first: local declarations below intentionally shadow their
// generated namesakes (ES module semantics), which is how the narrowings win.
export * from "./generated/types";
export * as AssistedListingIntakeV1 from "./generated/assisted_listing_intake";

import type {
  IntakeCorrectPayload as GeneratedIntakeCorrectPayload,
  IntakeDecidePayload as GeneratedIntakeDecidePayload,
  IntakePromotePayload as GeneratedIntakePromotePayload,
  IntakeSubmitPayload as GeneratedIntakeSubmitPayload,
  NetworkListingActorPayload as GeneratedNetworkListingActorPayload,
  NetworkListingMergePayload as GeneratedNetworkListingMergePayload,
  ErrorEnvelope,
  ReasonCommand,
  AssignmentTransferRequest,
  AssignmentReceipt,
  AssignmentRequest,
  SlaPauseRequest,
  SlaReceipt,
  JobReceipt,
  ListingDetail,
  PromotionDecisionReceipt,
  PromotionRequest,
  RetryRequest,
  ReviewDecisionRequest,
  BatchIntakeRequest,
  BatchIntakeReceipt,
} from "./generated/types";
import type {
  ApiError as CanonicalApiError,
  CorrectionReceipt as CanonicalCorrectionReceipt,
  ConflictError as CanonicalConflictError,
  IntakeSubmissionReceipt,
} from "./generated/assisted_listing_intake";

export type { ErrorEnvelope };

export type HealthResponse = {
  status: string;
  service: string;
  version?: string;
  time?: string;
  correlation_id?: string;
};

/** Standard list envelope returned by the collection endpoints. */
export type ListResponse<T> = {
  items: T[];
  count: number;
};

export type AvmCase = {
  case_id: string;
  store_id: string;
  status: string;
  created_by: string;
  created_at: string;
  valuation_input?: Record<string, unknown>;
  status_history?: Array<Record<string, unknown>>;
};

export type CreateAvmCaseInput = {
  store_id: string;
  gm_ttm: number;
  forecast_gm_next_12m: number;
  asset_book_value: number;
  equipment_fair_value: number;
  lease_liability?: number;
  working_capital?: number;
  comparable_multiples?: number[];
  liquidity_discount?: number;
  quality_score?: number;
  source_snapshot_ids?: string[];
  prediction_origin_time?: string | null;
  created_by: string;
  idempotency_key?: string | null;
};

export type AuditEvent = {
  event_id: string;
  event_type: string;
  actor: string;
  action: string;
  resource: string;
  outcome: string;
  result?: string;
  correlation_id: string;
  job_id?: string | null;
  metadata?: Record<string, unknown>;
  occurred_at: string;
};

export type AuditEventsResponse = {
  events: AuditEvent[];
};

export type InterventionSummary = {
  intervention_id: string;
  status?: string;
  [key: string]: unknown;
};

export type AdliftReport = {
  campaign_id?: string;
  [key: string]: unknown;
};

export type ForecastAlert = {
  alert_id: string;
  store_id: string;
  alert_level: "green" | "yellow" | "orange" | "red" | string;
  alert_reason_code?: string;
  evidence_json?: Record<string, unknown>;
  opened_at?: string;
  closed_at?: string | null;
  status: string;
  acknowledged_by?: string | null;
  acknowledged_at?: string | null;
  acknowledgement_note?: string | null;
  [key: string]: unknown;
};

export type SourceFreshnessEvidence = {
  provider_id: string;
  source_snapshot_id: string;
  data_status: string;
  provider_observed_at?: string | null;
  ingested_at?: string | null;
  freshness_sla_seconds: number;
  correlation_id: string;
  quality_flags?: string[];
  [key: string]: unknown;
};

export type ExternalDataFreshnessResponse = {
  freshness: SourceFreshnessEvidence[];
  correlation_id?: string;
};


// ---------------------------------------------------------------------------
// Expansion, SiteScore, NetPlan, LearningHub types (from dev)
// ---------------------------------------------------------------------------

/**
 * Raw heatzone score row returned by GET /heatzones.
 * Shape mirrors HeatZoneBatchScoreResult.to_dict().
 */
export type HeatZoneScore = {
  /** H3 hex index used as a stable identifier. */
  h3_index: string;
  score: number;
  rank: number;
  unmet_demand: number;
  confidence: number;
  state: string;
  [key: string]: unknown;
};

/**
 * A NetPlan scenario as served by `GET /netplan/scenarios` (the list/compare
 * endpoint). Mirrors `NetPlanScenario.to_dict()` in
 * modules/netplan/domain/planning.py — only the always-present summary fields
 * are typed; the full solve/execution/outcome detail lives on the
 * `/netplan/scenarios/{id}` response and is left open via the index signature.
 */
export type NetPlanScenarioSummary = {
  scenario_id: string;
  scenario_name?: string;
  planning_horizon?: string;
  status?: string;
  solver_version?: string;
  model_version?: string;
  correlation_id?: string;
  [key: string]: unknown;
};

/**
 * Candidate site card returned by GET /listings/candidates.
 * Shape mirrors CandidateSiteDraft.to_card_dict().
 */
export type CandidateSiteCard = {
  candidateSiteId: string;
  address: string;
  geocodeConfidence: number;
  rent: number;
  area: number;
  frontage?: number | null;
  floor?: string | null;
  parkingOrTemporaryStop?: boolean;
  feasibilityFlags: string[];
  heatZone: string;
  listingSource?: string | null;
  status: string;
  [key: string]: unknown;
};

/**
 * Site score report summary returned by GET /sitescore/reports.
 * Shape mirrors SiteScoreReport.to_summary_dict().
 */
export type SiteScoreReportSummary = {
  candidateSiteId: string;
  reportVersion: number;
  recommendation: string;
  confidence: number;
  modelVersion: string;
  featureSnapshotTime: string;
  cannibalizationRisk: string;
  [key: string]: unknown;
};

/**
 * A model release decision as served by `GET /learninghub/releases` (the
 * Learning Hub release/rollback log the UI binds to). Mirrors
 * `ModelReleaseDecision.to_dict()` in
 * modules/learninghub/application/release.py — only the always-present summary
 * fields are typed; the full success/fail criteria detail is left open via the
 * index signature.
 */
export type ModelReleaseSummary = {
  release_id: string;
  model_name?: string;
  from_version?: string | null;
  to_version?: string;
  release_type?: string;
  approval_id?: string;
  monitoring_window?: string;
  rollback_target?: string | null;
  approved_by?: string;
  created_at?: string;
  audit_event_id?: string | null;
  [key: string]: unknown;
};

// ---------------------------------------------------------------------------
// Operator Console R4 DTOs (ODP-OC-R4-001)
// ---------------------------------------------------------------------------

// --- Product shell types (ODP-PGAP-SHELL-001) ---

/** Where a shell payload came from and who it was built for. */
export type ShellMeta = {
  generatedAt: string;
  correlationId?: string | null;
  source: string;
  role?: { id: string; label: string };
  allowedWorkspaces?: string[];
  isAdmin?: boolean;
  [key: string]: unknown;
};

export type ShellSeverity = "critical" | "warning" | "info";
export type ShellSlaState = "breached" | "at-risk" | "on-track" | "none";

export type ShellEntryPoint = {
  key: string;
  label: string;
  href: string;
  workspace: string;
  description: string;
};

export type ShellTask = {
  taskId: string;
  id: string;
  title: string;
  status: string;
  owner?: string;
  meta?: string;
  time?: string;
  tone?: string;
  workspace?: string;
  severity: ShellSeverity;
  slaState: ShellSlaState;
  slaDueAt: string | null;
  assigneeId: string | null;
  assigneeName: string | null;
  assignedAt: string | null;
  assignedToMe: boolean;
  deepLink: { workspace: string; entityId: string; tab: string };
  sourceHref: string;
};

export type ShellNotification = {
  notificationId: string;
  title: string;
  detail: string;
  severity: ShellSeverity;
  acknowledged: boolean;
  acknowledgedAt: string | null;
  acknowledgedBy: string | null;
  sourceHref: string;
};

export type ShellFreshnessRow = {
  source: string;
  label: string;
  generatedAt: string;
  records: number;
  state: string;
};

export type ShellHomeResponse = {
  meta: ShellMeta;
  status: {
    headline: string;
    openTasks: number;
    slaBreached: number;
    slaAtRisk: number;
    pendingApprovals: number;
    unacknowledgedNotifications: number;
    tone: string;
  };
  tasks: ShellTask[];
  approvals: Array<{ id: string; title: string; status: string; meta?: string; tone?: string }>;
  decisions: Array<{ id: string; title: string; status: string; meta?: string; tone?: string }>;
  freshness: ShellFreshnessRow[];
  entryPoints: ShellEntryPoint[];
  notifications: ShellNotification[];
  kpis: Array<{ label: string; value: string; delta?: string; meta?: string; tone?: string }>;
};

export type ShellTaskFilters = {
  sla?: string;
  assignee?: string;
  status?: string;
  taskId?: string;
};

export type ShellAction = {
  key: string;
  label: string;
  allowed: boolean;
  reason: string | null;
};

export type ShellTasksResponse = {
  meta: ShellMeta;
  items: ShellTask[];
  count: number;
  total: number;
  facets: {
    sla: Record<string, number>;
    status: Record<string, number>;
    assignee: Record<string, number>;
  };
  actions: ShellAction[];
  assignableRoles: Array<{ id: string; label: string }>;
};

export type ShellTaskAssignRequest = {
  assigneeId: string;
  assigneeName?: string;
  slaDueAt?: string | null;
};

/** Every shell write echoes its audit event and whether it was a replay. */
export type ShellWriteResponse = {
  auditEvent: {
    id: string;
    auditEventId: string;
    occurredAt: string;
    actorRoleId: string;
    action: string;
    message: string;
    metadata: Record<string, unknown>;
  };
  correlationId?: string | null;
  idempotentReplay: boolean;
  [key: string]: unknown;
};

export type ShellTaskAssignResponse = ShellWriteResponse & {
  assignment: {
    taskId: string;
    assigneeId: string;
    assigneeName: string;
    slaDueAt: string | null;
    updatedAt: string;
    updatedBy: string;
  };
};

export type ShellPreferences = {
  channels: Record<string, boolean>;
  severityFloor: string;
  digest: string;
};

export type ShellPreferencesResponse = {
  roleId: string;
  preferences: ShellPreferences;
  isDefault: boolean;
  severityLevels: string[];
};

export type ShellPreferencesWriteResponse = ShellWriteResponse & {
  roleId: string;
  preferences: ShellPreferences;
};

export type ShellNotificationsResponse = {
  meta: ShellMeta;
  items: ShellNotification[];
  count: number;
  unacknowledged: number;
  facets: { severity: Record<string, number> };
  preferences: ShellPreferences;
};

export type ShellSearchResult = {
  id: string;
  entityId: string;
  label: string;
  description: string;
  workspace: string;
  kind: "entity" | "workspace";
  href: string;
};

export type ShellSearchCommand = {
  id: string;
  label: string;
  description: string;
  href: string;
  kind: "command";
};

export type ShellSearchResponse = {
  meta: ShellMeta;
  items: ShellSearchResult[];
  count: number;
  total: number;
  commands: ShellSearchCommand[];
};

export type ShellAdminRoleRow = {
  roleId: string;
  label: string;
  subtitle: string;
  allowedWorkspaces: string[];
  overridden: boolean;
  updatedAt: string | null;
  updatedBy: string | null;
};

export type ShellAdminResponse = {
  meta: ShellMeta;
  roles: ShellAdminRoleRow[];
  workspaces: Array<{ id: string; label: string }>;
  auditFeed: Array<{
    id: string;
    occurredAt: string;
    actorRoleId: string;
    action: string;
    message: string;
  }>;
};

export type ShellSettingsResponse = {
  meta: ShellMeta;
  scope: string;
  values: Record<string, string>;
  isDefault: boolean;
  updatedAt: string | null;
  updatedBy: string | null;
  options: Record<string, string[]>;
};

export type ShellSettingsWriteResponse = ShellWriteResponse & {
  scope: string;
  values: Record<string, string>;
};

/** The franchisee projection — deliberately narrower than the operator task. */
export type ShellFranchiseeTask = {
  id: string;
  title: string;
  status: string;
  time?: string;
};

export type ShellFranchiseeReport = {
  reportId: string;
  category: string;
  message: string;
  status: string;
  createdAt: string;
  storeId: string;
};

export type ShellFranchiseeResponse = {
  meta: ShellMeta;
  store: { id: string; label: string };
  tasks: ShellFranchiseeTask[];
  notifications: Array<{
    notificationId: string;
    title: string;
    detail: string;
    severity: ShellSeverity;
    acknowledged: boolean;
  }>;
  reports: ShellFranchiseeReport[];
  reportCategories: string[];
};

export type ShellFranchiseeReportResponse = ShellWriteResponse & {
  report: ShellFranchiseeReport;
};

/** Work-queue item shape returned by GET /operator/issues. */
export type OperatorWorkQueueItem = {
  id: string;
  title: string;
  description?: string;
  meta?: string;
  owner?: string;
  status: string;
  time?: string;
  tone?: string;
  workspace?: string;
  [key: string]: unknown;
};

/** Approval item shape returned by GET /operator/approvals. */
export type OperatorApprovalItem = {
  id: string;
  title: string;
  meta?: string;
  status: string;
  cta?: string;
  tone?: string;
  [key: string]: unknown;
};

/** Full bootstrap/today response shape. */
export type OperatorBootstrapResponse = {
  kpis: Array<{ label: string; value: string; delta?: string; meta?: string; tone?: string }>;
  workQueue: OperatorWorkQueueItem[];
  decisions: OperatorApprovalItem[];
  riskRows?: Array<{ label: string; score: number; signal?: string; tone?: string }>;
  auditFeed?: Array<{ actor: string; category: string; detail: string; time: string; auditEventId?: string }>;
  notifications?: Array<{ title: string; detail: string; tone?: string }>;
  [key: string]: unknown;
};

export type IntakeRoleMode =
  | "expansion-staff"
  | "expansion-manager"
  | "data-steward"
  | "governance-reviewer"
  | "privacy-officer"
  | "permission-limited";

export type CanonicalMatchComparisonField = {
  field_path: string;
  label: string;
  submitted_value: unknown;
  existing_value: unknown;
  agrees: boolean;
  detail: string | null;
};

export type CanonicalMatchSignal = {
  key: string;
  label: string;
  agrees: boolean;
  detail: string;
};

export type CanonicalIdentityGraphNode = {
  node_id: string;
  node_type: string;
  status: string;
};

export type CanonicalIdentityGraphEdge = {
  edge_id: string;
  relation: string;
  status: string;
  source_property_id: string | null;
  target_property_id: string | null;
  property_id: string | null;
  listing_id: string | null;
  intake_id: string | null;
  decision_id: string | null;
  supersedes_edge_ids: string[];
};

export type CanonicalIdentityGraphSnapshot = {
  version: number;
  nodes: CanonicalIdentityGraphNode[];
  edges: CanonicalIdentityGraphEdge[];
};

export type CanonicalIdentityRedirect = {
  from_property_id: string;
  to_property_id: string;
  reason: string;
  status: string;
};

export type CanonicalCandidateImpact = {
  candidate_site_id: string | null;
  disposition: string | null;
  source_property_id: string | null;
  target_property_id: string | null;
};

export type CanonicalLineageImpact = {
  append_only: boolean;
  source_evidence_preserved: boolean;
  superseded_edge_ids: string[];
  affected_decision_ids: string[];
  summary: string;
};

export type CanonicalDecisionActorReference = {
  subject_id: string;
  role_id: string;
};

export type CanonicalOriginalDecisionReference = {
  decision_id: string;
  action: string | null;
  status: string | null;
  version: number | null;
};

export type CanonicalMatchGraphPlan = {
  plan_id: string;
  plan_type: string;
  status: string;
  operations: Array<Record<string, unknown>>;
  permitted_decision_types: string[];
  requires_human_decision: boolean;
  before_graph: CanonicalIdentityGraphSnapshot;
  after_graph: CanonicalIdentityGraphSnapshot;
  redirects: CanonicalIdentityRedirect[];
  candidate_impacts: CanonicalCandidateImpact[];
  lineage_impact: CanonicalLineageImpact;
  proposer: CanonicalDecisionActorReference | null;
  reviewer: CanonicalDecisionActorReference | null;
  expected_graph_version: number;
  original_decision: CanonicalOriginalDecisionReference | null;
  generated_at: string;
};

export type CanonicalMatchCaseDetail = {
  match_case_id: string;
  version: number;
  intake_id: string;
  outcome: string;
  confidence: number;
  target_listing_id: string | null;
  summary: string;
  comparison_fields: CanonicalMatchComparisonField[];
  signals: CanonicalMatchSignal[];
  graph_plan: CanonicalMatchGraphPlan;
  source_snapshot_id: string | null;
  parser_version: string | null;
  created_at: string;
  updated_at: string;
};

export type CanonicalActorDecisionFacts = {
  role_mode: IntakeRoleMode;
  allowed_actions: string[];
  denied_action_reasons: Record<string, string>;
  scope: {
    principal_tenant_id: string;
    resource: {
      tenant_id: string;
      assigned_area_id?: string | null;
      brand_id?: string | null;
      heat_zone_id?: string | null;
      region_id?: string | null;
    };
    in_scope: boolean;
  };
  masking: {
    masked_fields: string[];
    reason_codes: string[];
    has_masked_fields: boolean;
    clearance: string | null;
  };
  purpose: {
    value: string | null;
    required: boolean;
    bound: boolean;
    reason_code: string | null;
  };
  second_actor: {
    required: boolean;
    pending_decision_ids: string[];
    proposer_subject_ids: string[];
    self_review_denied: boolean;
    reason_code: string | null;
  };
};

export type CanonicalLifecycleReceipt = {
  receipt_id: string | null;
  category: "assignment" | "sla" | "decision" | "promotion" | "job" | "intake";
  action: string | null;
  resource_id: string | null;
  resource_version: number | null;
  status: string | null;
  actor: string | null;
  correlation_id: string | null;
  occurred_at: string | null;
  receipt: CanonicalMutationReceipt;
};

export type CanonicalMutationReceipt = {
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

export type CanonicalTransitionReceipt = {
  transition_id: string;
  from_state: string | null;
  to_state: string;
  occurred_at: string;
  actor: string;
  reason_code?: string | null;
  version_after: number;
};

export type CanonicalFieldValue = {
  field_path: string;
  classification: "PUBLIC" | "INTERNAL" | "CONFIDENTIAL" | "RESTRICTED";
  masked: boolean;
  parsed: unknown;
  normalized: unknown;
  corrected: unknown;
  effective: unknown;
  confidence: number | null;
  mask_reason_code: string | null;
  correction_actor: string | null;
  correction_actor_role: string | null;
  correction_reason: string | null;
  corrected_at: string | null;
  source_snapshot_id: string | null;
  parser_run_id: string | null;
  parser_version: string | null;
};

export type CanonicalAuditReference = {
  audit_event_id: string;
  action: string;
  occurred_at: string;
  result: "SUCCEEDED" | "DENIED" | "FAILED";
  reason_code: string | null;
  actor: string | null;
  actor_role: string | null;
  before: unknown;
  after: unknown;
  source_snapshot_id: string | null;
  parser_version: string | null;
  related_ids: Record<string, unknown>;
  correlation_id: string | null;
  resource_version: number | null;
  evidence_state: string | null;
};

export type CanonicalSourceEvidence = {
  original_url: string | null;
  canonical_url: string | null;
  source_id: string | null;
  policy_state: string | null;
  policy_reason: string | null;
  policy_version: string | null;
  policy_evaluated_at: string | null;
  policy_expires_at: string | null;
  source_snapshot_id: string | null;
  captured_at: string | null;
  parser_run_id: string | null;
  parser_version: string | null;
  correlation_id: string;
  freshness_state: "CURRENT" | "STALE" | "NOT_CAPTURED";
  resource_version: number;
  etag: string;
};

export type CanonicalDecisionEffectReceipt = {
  receipt_id: string;
  decision_id: string;
  status: string;
  identity_edge_ids: string[];
  runtime_receipt: CanonicalMutationReceipt | null;
  audit_event_id: string;
  correlation_id: string;
  version: number;
  issued_at: string;
  evidence_state: string;
};

export type CanonicalAssignmentLifecycle = {
  assignment_id: string;
  intake_id: string | null;
  status: string;
  owner_subject_id: string | null;
  queue_id: string | null;
  due_at: string | null;
  version: number;
};

export type CanonicalSlaLifecycle = {
  sla_instance_id: string;
  state: string;
  due_at: string | null;
  paused_duration_seconds: number | null;
  version: number;
};

export type CanonicalPromotionLifecycle = {
  promotion_decision_id: string;
  intake_id: string | null;
  status: string;
  candidate_site_id: string | null;
  site_score_job_id: string | null;
  version: number;
};

export type CanonicalJobLifecycle = {
  job_id: string;
  status: string;
  attempt: number | null;
  checkpoint: string | null;
  next_retry_at: string | null;
  fence_token: number | string | null;
  version: number | null;
};

export type CanonicalDecisionLifecycle = {
  decision_id: string | null;
  receipt_id: string | null;
  status: string;
  action: string | null;
  version: number;
  proposer: string | null;
  reviewer: string | null;
  graph_plan: CanonicalMatchGraphPlan | null;
  correlation_id: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type CanonicalSubmissionLifecycleReceipt = {
  receipt_id: string;
  receipt_type: string;
  intake_id: string;
  state: string;
  existing_listing_id: string | null;
  navigation_target: string | null;
  correlation_id: string;
  issued_at: string;
};

export type CanonicalLifecycleAggregate = {
  intake_id: string;
  version: number;
  etag: string;
  actor_facts: CanonicalActorDecisionFacts;
  assignment: CanonicalAssignmentLifecycle | null;
  sla: CanonicalSlaLifecycle | null;
  decisions: CanonicalDecisionLifecycle[];
  promotion: CanonicalPromotionLifecycle | null;
  job: CanonicalJobLifecycle | null;
  assignment_history: CanonicalLifecycleReceipt[];
  sla_history: CanonicalLifecycleReceipt[];
  decision_history: CanonicalLifecycleReceipt[];
  promotion_history: CanonicalLifecycleReceipt[];
  job_history: CanonicalLifecycleReceipt[];
  mutation_receipts: CanonicalLifecycleReceipt[];
  latest_decision_receipt: CanonicalDecisionLifecycle | null;
  submission_receipt: CanonicalSubmissionLifecycleReceipt | null;
};

export type CanonicalIntakeRuntimeDetail = {
  intake_id: string;
  state: string;
  intake_method: string;
  source_id: string | null;
  match_outcome: string | null;
  submitted_by: string;
  assigned_to: string | null;
  due_at: string | null;
  submitted_at: string;
  updated_at: string;
  version: number;
  issue?: string | null;
  next_action?: string | null;
  retryable?: boolean;
  failed?: boolean;
  quarantined?: boolean;
  correlation_id?: string | null;
  last_observed_at?: string | null;
  scope: Record<string, unknown>;
  masked_fields: string[];
  original_url: string | null;
  canonical_url: string | null;
  policy_state: string | null;
  source_snapshot_id: string | null;
  parser_run_id: string | null;
  match_case_id: string | null;
  match_case_version: number | null;
  match_case: CanonicalMatchCaseDetail | null;
  processing_history: CanonicalTransitionReceipt[];
  fields: CanonicalFieldValue[];
  audit: CanonicalAuditReference[];
  evidence: CanonicalSourceEvidence;
  lifecycle: CanonicalLifecycleAggregate;
};

export type CanonicalIdentityDecisionReceipt = {
  decision_id: string;
  status: string;
  resource_versions: Record<string, number>;
  job_id: string | null;
  audit_event_id: string;
  correlation_id: string;
  version: number;
  action: string | null;
  proposer: string | null;
  reviewer: string | null;
  reason: string | null;
  graph_plan: CanonicalMatchGraphPlan | null;
  effect_receipt: CanonicalDecisionEffectReceipt | null;
  reverses_decision_id: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type CanonicalMatchDecisionCommand = {
  decision_type: "CREATE" | "REVISE" | "DUPLICATE" | "QUARANTINE" | "REJECT";
  reason: string;
  requested_second_reviewer_id?: string | null;
  risk_acknowledged: boolean;
  target_listing_id?: string | null;
  target_property_id?: string | null;
};

export type CanonicalMergeCommand = {
  source_property_ids: string[];
  target_property_id: string;
  reason: string;
  risk_acknowledged: true;
  candidate_reassignment_plan?: Array<Record<string, unknown>>;
  expected_property_versions?: Record<string, number>;
};

export type CanonicalIdentityPartition = {
  target_property_id: string | null;
  source_identity_edge_ids: string[];
};

export type CanonicalSplitCommand = {
  source_property_id: string;
  partitions: CanonicalIdentityPartition[];
  reason: string;
  risk_acknowledged: true;
  source_property_version?: number | null;
};

export type CanonicalUnmergeCommand = {
  original_decision_id: string;
  replacement_edges: CanonicalIdentityPartition[];
  reason: string;
  risk_acknowledged: true;
};

export type CanonicalIdentityReviewCommand = {
  decision: "APPROVE" | "REJECT";
  reason: string;
  requested_changes?: string[];
  risk_acknowledged: boolean;
};

export type CanonicalRiskReasonCommand = {
  reason: string;
  risk_acknowledged: boolean;
};

/** Valid action types for issue lifecycle transitions. */
export type OperatorIssueActionType = "triage" | "assign" | "actions" | "outcome";

/** Request body for POST /operator/issues/{id}/{action_type}. */
export type OperatorIssueTransitionRequest = {
  actorRoleId: string;
  actorName?: string;
  issueId?: string;
  status?: string;
  note?: string;
};

/** Response from POST /operator/issues/{id}/{action_type}. */
export type OperatorIssueTransitionResponse = {
  issueId: string;
  newStatus: string;
  auditEventId: string;
  correlationId?: string | null;
};

/** Request body for POST /operator/approvals/{id}/decision. */
export type OperatorApprovalDecisionRequest = {
  actorRoleId: string;
  actorName?: string;
  status: "approved" | "returned" | "rejected";
  /** Required. Must be non-empty for all approval decisions. */
  reason: string;
};

/** Response from POST /operator/approvals/{id}/decision. */
export type OperatorApprovalDecisionResponse = {
  approvalId: string;
  newStatus: string;
  auditEventId: string;
  correlationId?: string | null;
};

/** Request body for POST /operator/evidence/{id}/purpose. */
export type OperatorEvidencePurposeRequest = {
  actorRoleId: string;
  actorName?: string;
  purpose: string;
  cameraLocation?: string;
  timeWindow?: string;
  /** Must not exceed 72 (policy ceiling). */
  retentionHours?: number;
  /** Must be true for camera evidence kinds. */
  privacyAcknowledged?: boolean;
  auditNote?: string;
};

/** Response from POST /operator/evidence/{id}/purpose. */
export type OperatorEvidencePurposeResponse = {
  evidenceId: string;
  purpose: string;
  auditEventId: string;
  correlationId?: string | null;
};

/** Env keys checked, in priority order, when resolving the API base URL. */
export const API_BASE_URL_ENV_KEYS = [
  "ODP_API_BASE_URL",
  "NEXT_PUBLIC_ODP_API_BASE_URL",
] as const;

const DEFAULT_TIMEOUT_MS = 5000;
const CORRELATION_ID_HEADER = "x-correlation-id";
declare const process:
  | { env?: Record<string, string | undefined> }
  | undefined;

function readProcessEnv(): Record<string, string | undefined> {
  if (typeof process !== "undefined" && process.env) {
    return process.env as Record<string, string | undefined>;
  }
  return {};
}

/**
 * Resolve the backend base URL from the environment. Returns `null` when no
 * base URL is configured so callers can fall back to bundled fixture data
 * instead of throwing (the product must still render without a backend).
 */
export function resolveApiBaseUrl(
  env: Record<string, string | undefined> = readProcessEnv(),
): string | null {
  for (const key of API_BASE_URL_ENV_KEYS) {
    const value = env[key];
    if (value && value.trim()) {
      return value.trim().replace(/\/+$/, "");
    }
  }
  return null;
}

/**
 * FastAPI rejects a request from one of two layers, and each shapes `detail`
 * differently: a route handler raising HTTPException produces a plain string,
 * while Pydantic body validation produces an array of field errors. Callers
 * must handle both, so the union is explicit rather than `any`.
 */
export type ApiValidationIssue = {
  loc?: Array<string | number>;
  msg?: string;
  type?: string;
};

export type ApiErrorBody = Partial<CanonicalApiError & CanonicalConflictError> & {
  /**
   * Legacy detail, exactly as the route produced it. Retained indefinitely:
   * some routes return an object whose fields callers branch on (the
   * rebalance `state` retry flag, the scoring gate's `missing` list).
   */
  detail?: string | ApiValidationIssue[] | Record<string, unknown>;
  /** The structured envelope (ODP-PGAP-API-001). Present on every error. */
  error?: ErrorEnvelope;
};

export class OdpApiError extends Error {
  readonly status: number;
  readonly url: string;
  readonly correlationId?: string;
  /** Parsed response body, when the server sent JSON. */
  readonly body?: ApiErrorBody;
  /**
   * Server-supplied reason, flattened to a single string. The backend writes
   * operator-facing zh-TW copy here (policy refusals, reason-required, role
   * denials), so the UI must render this rather than invent its own message.
   */
  readonly detail?: string;
  /**
   * The structured envelope. Prefer this over `detail` in new code: `code` is
   * stable and branchable, whereas `detail` is prose that changes with copy
   * edits and cannot be safely matched on.
   */
  readonly envelope?: ErrorEnvelope;
  /** Stable machine-readable code, e.g. "forbidden", "idempotency_conflict". */
  readonly code?: string;
  /** What the server says the caller should do next; safe to surface as-is. */
  readonly nextAction?: string | null;
  /** Server decision about whether retrying this operation is permitted. */
  readonly retryable?: boolean;
  /** Server timestamp. Never replace this with the browser's current time. */
  readonly occurredAt?: string;
  /** Server state at the point of conflict, when the operation has one. */
  readonly currentState?: string | null;
  /** Server version at the point of conflict, when the operation has one. */
  readonly currentVersion?: number | null;
  /** Stable backend denial/policy reason, when more specific than `code`. */
  readonly reasonCode?: string | null;
  /** Backend-directed delay for a retryable response. */
  readonly retryAfterSeconds?: number | null;

  constructor(
    message: string,
    options: {
      status: number;
      url: string;
      correlationId?: string;
      body?: ApiErrorBody;
    },
  ) {
    super(message);
    this.name = "OdpApiError";
    this.status = options.status;
    this.url = options.url;
    this.body = options.body;
    this.envelope = options.body?.error;
    // Assisted-intake v1 errors are the canonical ApiError directly at the
    // response root. Other APIs use the compatibility {detail,error} shape.
    // Read both without deriving domain facts from the HTTP status.
    this.code = options.body?.code ?? this.envelope?.code;
    this.nextAction = options.body?.next_action ?? this.envelope?.next_action;
    this.retryable = options.body?.retryable;
    this.occurredAt = options.body?.occurred_at ?? this.envelope?.occurred_at;
    this.currentState = options.body?.current_state;
    this.currentVersion = options.body?.current_version;
    this.reasonCode = options.body?.reason_code;
    this.retryAfterSeconds = options.body?.retry_after_seconds;
    // The body is the value the server recorded. A response header is only the
    // compatibility fallback, followed by the caller-provided correlation ID.
    this.correlationId =
      options.body?.correlation_id ??
      this.envelope?.correlation_id ??
      options.correlationId ??
      undefined;
    // The envelope's message is already the flattened text, so prefer it and
    // fall back to flattening `detail` for any endpoint not yet behind the
    // handlers (and for older servers during a rollout).
    this.detail =
      options.body?.message ??
      this.envelope?.message ??
      flattenApiDetail(options.body?.detail);
  }
}

/**
 * Reduce a legacy `detail` to displayable text.
 *
 * Prefer `OdpApiError.envelope.message`; this remains for the fallback path.
 * An object `detail` yields its `message` field when it has one — the routes
 * that send objects put the operator-facing copy there.
 */
export function flattenApiDetail(
  detail: string | ApiValidationIssue[] | Record<string, unknown> | undefined,
): string | undefined {
  if (typeof detail === "string") return detail || undefined;
  if (!Array.isArray(detail)) {
    const message = detail?.["message"];
    return typeof message === "string" && message ? message : undefined;
  }
  const parts = detail
    .map((issue) => {
      const field = (issue.loc ?? [])
        .filter((segment) => segment !== "body")
        .join(".");
      return field && issue.msg ? `${field}: ${issue.msg}` : (issue.msg ?? "");
    })
    .filter(Boolean);
  return parts.length ? parts.join("; ") : undefined;
}

export type OdpApiClientOptions = {
  baseUrl: string;
  /** Override the fetch implementation (defaults to the platform `fetch`). */
  fetchImpl?: typeof fetch;
  /** Per-request timeout in milliseconds. */
  timeoutMs?: number;
  defaultHeaders?: Record<string, string>;
};

type RequestOptions = {
  method?: string;
  body?: unknown;
  correlationId?: string;
  idempotencyKey?: string;
  ifMatch?: string;
  query?: Record<string, string | readonly string[] | undefined>;
};

export class OdpApiClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly timeoutMs: number;
  private readonly defaultHeaders: Record<string, string>;

  constructor(options: OdpApiClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    // `fetch` must stay bound to its global. Storing a bare reference and
    // calling it as a method (this.fetchImpl(...)) throws "Illegal invocation"
    // in browsers, where the implementation requires a Window/WorkerGlobalScope
    // receiver — the request never leaves the page.
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.defaultHeaders = options.defaultHeaders ?? {};
    if (typeof this.fetchImpl !== "function") {
      throw new Error("OdpApiClient requires a fetch implementation");
    }
  }

  private canonicalTenantId(): string {
    const tenant = Object.entries(this.defaultHeaders).find(
      ([name]) => name.toLowerCase() === "x-tenant-id",
    )?.[1];
    if (!tenant) {
      throw new Error(
        "Canonical assisted-intake commands require an x-tenant-id default header",
      );
    }
    return tenant;
  }

  private commandIdempotencyKey(explicit?: string, suffix?: string): string {
    if (explicit) return suffix ? `${explicit}:${suffix}` : explicit;
    const random =
      globalThis.crypto?.randomUUID?.() ??
      `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    return `odp-intake-${random}${suffix ? `:${suffix}` : ""}`;
  }

  private async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const { value } = await this.requestWithMeta<T>(path, options);
    return value;
  }

  /**
   * Like `request`, but also surfaces the response headers. The assisted-intake
   * promotion saga needs `Idempotency-Replayed` to tell the operator whether
   * the server answered from a prior durable receipt (a replay must be labeled,
   * not silently presented as a fresh write — handoff §8.8).
   */
  private async requestWithMeta<T>(
    path: string,
    options: RequestOptions = {},
  ): Promise<{ value: T; headers: Headers }> {
    const query = options.query
      ? Object.entries(options.query)
          .filter(([, value]) => value !== undefined && value !== "")
          .flatMap(([key, value]) =>
            (Array.isArray(value) ? value : [value]).map(
              (item) =>
                `${encodeURIComponent(key)}=${encodeURIComponent(String(item))}`,
            ),
          )
          .join("&")
      : "";
    const url = `${this.baseUrl}${path}${query ? `?${query}` : ""}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const response = await this.fetchImpl(url, {
        method: options.method ?? "GET",
        signal: controller.signal,
        // The backend is the source of truth — never serve a stale cached
        // copy, otherwise a backend state change would not reach the UI.
        cache: "no-store",
        // defaultHeaders carry the caller's identity (subject/roles/tenant) and
        // are spread FIRST: a per-request correlation or idempotency key is
        // more specific than a client-construction default and must win, and
        // content-type must not be overridable at all.
        headers: {
          accept: "application/json",
          ...this.defaultHeaders,
          ...(options.body !== undefined ? { "content-type": "application/json" } : {}),
          ...(options.correlationId ? { [CORRELATION_ID_HEADER]: options.correlationId } : {}),
          ...(options.idempotencyKey ? { "Idempotency-Key": options.idempotencyKey } : {}),
          ...(options.ifMatch ? { "If-Match": options.ifMatch } : {}),
        },
        body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      });
      if (!response.ok) {
        // Read the body before throwing — the server's `detail` is the only
        // place the operator-facing refusal reason exists.
        let body: ApiErrorBody | undefined;
        try {
          body = (await response.json()) as ApiErrorBody;
        } catch {
          body = undefined;
        }
        throw new OdpApiError(`ODay API ${response.status} for ${path}`, {
          status: response.status,
          url,
          correlationId: response.headers.get(CORRELATION_ID_HEADER) ?? options.correlationId,
          body,
        });
      }
      return { value: (await response.json()) as T, headers: response.headers };
    } finally {
      clearTimeout(timer);
    }
  }

  health(): Promise<HealthResponse> {
    return this.request<HealthResponse>("/platform/health");
  }

  listAvmCases(): Promise<ListResponse<AvmCase>> {
    return this.request<ListResponse<AvmCase>>("/avm/cases");
  }

  createAvmCase(
    input: CreateAvmCaseInput,
    options: { correlationId?: string } = {},
  ): Promise<AvmCase & { created?: boolean; correlation_id?: string }> {
    return this.request("/avm/cases", {
      method: "POST",
      body: input,
      correlationId: options.correlationId,
    });
  }

  listAuditEvents(
    options: { correlationId?: string } = {},
  ): Promise<AuditEventsResponse> {
    return this.request<AuditEventsResponse>("/audit/events", {
      query: { correlation_id: options.correlationId },
    });
  }

  listInterventions(): Promise<ListResponse<InterventionSummary>> {
    return this.request<ListResponse<InterventionSummary>>("/interventions");
  }

  listAdliftReports(): Promise<ListResponse<AdliftReport>> {
    return this.request<ListResponse<AdliftReport>>("/adlift/reports");
  }

  listForecastAlerts(
    options: { level?: string } = {},
  ): Promise<ListResponse<ForecastAlert>> {
    return this.request<ListResponse<ForecastAlert>>("/forecastops/alerts", {
      query: { level: options.level },
    });
  }

  listExternalDataFreshness(): Promise<ExternalDataFreshnessResponse> {
    return this.request<ExternalDataFreshnessResponse>("/external-data/freshness");
  }


  /**
   * List heatzone scores from the most recent batch scoring run.
   * Returns an empty `items` array when no scoring job has been run yet.
   * Corresponds to GET /heatzones.
   */
  listHeatzones(options: { limit?: number } = {}): Promise<ListResponse<HeatZoneScore>> {
    return this.request<ListResponse<HeatZoneScore>>("/heatzones", {
      query: options.limit !== undefined ? { limit: String(options.limit) } : undefined,
    });
  }

  /**
   * List candidate sites that have passed hard-rule checks.
   * Corresponds to GET /listings/candidates.
   */
  listCandidates(): Promise<{ candidates: CandidateSiteCard[] }> {
    return this.request<{ candidates: CandidateSiteCard[] }>("/listings/candidates");
  }

  /**
   * List the latest SiteScore report per candidate site.
   * Corresponds to GET /sitescore/reports.
   */
  listSiteScoreReports(): Promise<ListResponse<SiteScoreReportSummary>> {
    return this.request<ListResponse<SiteScoreReportSummary>>("/sitescore/reports");
  }

  listNetplanScenarios(): Promise<ListResponse<NetPlanScenarioSummary>> {
    return this.request<ListResponse<NetPlanScenarioSummary>>("/netplan/scenarios");
  }

  listLearningReleases(
    options: { modelName?: string } = {},
  ): Promise<ListResponse<ModelReleaseSummary>> {
    return this.request<ListResponse<ModelReleaseSummary>>("/learninghub/releases", {
      query: { model_name: options.modelName },
    });
  }

  // ---------------------------------------------------------------------------
  // Operator Console — R4 typed contracts (ODP-OC-R4-001)
  // ---------------------------------------------------------------------------

  /** Fetch the full operator bootstrap/today payload. */
  getOperatorBootstrap(): Promise<OperatorBootstrapResponse> {
    return this.request<OperatorBootstrapResponse>("/api/v1/operator/bootstrap");
  }

  /** Fetch today operational snapshot (alias of bootstrap for FE compat). */
  getOperatorToday(): Promise<OperatorBootstrapResponse> {
    return this.request<OperatorBootstrapResponse>("/api/v1/operator/today");
  }

  /** List current work-queue issues. */
  listOperatorIssues(): Promise<ListResponse<OperatorWorkQueueItem>> {
    return this.request<ListResponse<OperatorWorkQueueItem>>("/api/v1/operator/issues");
  }

  /**
   * Transition an issue through its lifecycle.
   *
   * actionType: "triage" | "assign" | "actions" | "outcome"
   * Requires actorRoleId + actorName in payload.
   * Idempotency-Key header is sent when idempotencyKey is provided.
   */
  transitionOperatorIssue(
    issueId: string,
    actionType: OperatorIssueActionType,
    payload: OperatorIssueTransitionRequest,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<OperatorIssueTransitionResponse> {
    return this.request<OperatorIssueTransitionResponse>(
      `/api/v1/operator/issues/${issueId}/${actionType}`,
      {
        method: "POST",
        body: payload,
        correlationId: options.correlationId,
        idempotencyKey: options.idempotencyKey,
      },
    );
  }

  /** List current pending approval decisions. */
  listOperatorApprovals(): Promise<ListResponse<OperatorApprovalItem>> {
    return this.request<ListResponse<OperatorApprovalItem>>("/api/v1/operator/approvals");
  }

  /**
   * Record an approval decision.
   *
   * status: "approved" | "returned" | "rejected"
   * reason is required and must be non-empty.
   * Idempotency-Key header is sent when idempotencyKey is provided.
   */
  decideOperatorApproval(
    approvalId: string,
    payload: OperatorApprovalDecisionRequest,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<OperatorApprovalDecisionResponse> {
    return this.request<OperatorApprovalDecisionResponse>(
      `/api/v1/operator/approvals/${approvalId}/decision`,
      {
        method: "POST",
        body: payload,
        correlationId: options.correlationId,
        idempotencyKey: options.idempotencyKey,
      },
    );
  }

  /**
   * Unlock a locked evidence item by declaring its access purpose.
   *
   * privacyAcknowledged must be true for camera evidence.
   * retentionHours must not exceed 72 (policy ceiling).
   * Idempotency-Key header is sent when idempotencyKey is provided.
   */
  confirmOperatorEvidencePurpose(
    evidenceId: string,
    payload: OperatorEvidencePurposeRequest,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<OperatorEvidencePurposeResponse> {
    return this.request<OperatorEvidencePurposeResponse>(
      `/api/v1/operator/evidence/${evidenceId}/purpose`,
      {
        method: "POST",
        body: payload,
        correlationId: options.correlationId,
        idempotencyKey: options.idempotencyKey,
      },
    );
  }

  /**
   * Reset the in-memory operator state to the canonical R4 seed.
   * Idempotent — used by integration tests and dev-environment setup.
   */
  resetOperatorSeed(
    options: { correlationId?: string } = {},
  ): Promise<{ status: string; message: string; correlation_id?: string }> {
    return this.request("/api/v1/operator/seed/reset", {
      method: "POST",
      correlationId: options.correlationId,
    });
  }

  // --- Product shell API methods (ODP-PGAP-SHELL-001) ---

  /** Aggregated first screen: status, tasks, approvals, decisions, freshness. */
  getShellHome(): Promise<ShellHomeResponse> {
    return this.request<ShellHomeResponse>("/api/v1/operator/shell/home");
  }

  /** Task Center list. Filters are server-applied; facets describe the unfiltered set. */
  getShellTasks(filters: ShellTaskFilters = {}): Promise<ShellTasksResponse> {
    return this.request<ShellTasksResponse>("/api/v1/operator/shell/tasks", {
      query: {
        sla: filters.sla,
        assignee: filters.assignee,
        status: filters.status,
        taskId: filters.taskId,
      },
    });
  }

  /**
   * Assign a task. `idempotencyKey` must be minted once per logical assignment
   * and retained across retries, or a retry will be applied twice.
   */
  assignShellTask(
    taskId: string,
    body: ShellTaskAssignRequest,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<ShellTaskAssignResponse> {
    return this.request<ShellTaskAssignResponse>(
      `/api/v1/operator/shell/tasks/${encodeURIComponent(taskId)}/assignment`,
      {
        method: "POST",
        body,
        correlationId: options.correlationId,
        idempotencyKey: options.idempotencyKey,
      },
    );
  }

  /** Durable notification inbox for the acting role. */
  getShellNotifications(
    filters: { severity?: string; acknowledged?: boolean } = {},
  ): Promise<ShellNotificationsResponse> {
    return this.request<ShellNotificationsResponse>("/api/v1/operator/shell/notifications", {
      query: {
        severity: filters.severity,
        acknowledged:
          filters.acknowledged === undefined ? undefined : String(filters.acknowledged),
      },
    });
  }

  /** Acknowledge one notification for the acting role. */
  acknowledgeShellNotification(
    notificationId: string,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<ShellWriteResponse> {
    return this.request<ShellWriteResponse>(
      `/api/v1/operator/shell/notifications/${encodeURIComponent(notificationId)}/acknowledgement`,
      {
        method: "POST",
        correlationId: options.correlationId,
        idempotencyKey: options.idempotencyKey,
      },
    );
  }

  getShellNotificationPreferences(): Promise<ShellPreferencesResponse> {
    return this.request<ShellPreferencesResponse>(
      "/api/v1/operator/shell/notifications/preferences",
    );
  }

  updateShellNotificationPreferences(
    body: ShellPreferences,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<ShellPreferencesWriteResponse> {
    return this.request<ShellPreferencesWriteResponse>(
      "/api/v1/operator/shell/notifications/preferences",
      {
        method: "PUT",
        body,
        correlationId: options.correlationId,
        idempotencyKey: options.idempotencyKey,
      },
    );
  }

  /** Authorized cross-domain search + keyboard commands. */
  searchShell(query: string, options: { limit?: number } = {}): Promise<ShellSearchResponse> {
    return this.request<ShellSearchResponse>("/api/v1/operator/shell/search", {
      query: { q: query, limit: options.limit ? String(options.limit) : undefined },
    });
  }

  /** Role/workspace administration view. 403 for non-admin roles. */
  getShellAdmin(): Promise<ShellAdminResponse> {
    return this.request<ShellAdminResponse>("/api/v1/operator/shell/admin");
  }

  /** High-risk governed write: override a role's workspace grants. */
  updateShellRoleWorkspaces(
    roleId: string,
    body: { allowedWorkspaces: string[] },
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<ShellWriteResponse> {
    return this.request<ShellWriteResponse>(
      `/api/v1/operator/shell/admin/roles/${encodeURIComponent(roleId)}/workspaces`,
      {
        method: "PUT",
        body,
        correlationId: options.correlationId,
        idempotencyKey: options.idempotencyKey,
      },
    );
  }

  getShellSettings(): Promise<ShellSettingsResponse> {
    return this.request<ShellSettingsResponse>("/api/v1/operator/shell/settings");
  }

  updateShellSettings(
    values: Record<string, string>,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<ShellSettingsWriteResponse> {
    return this.request<ShellSettingsWriteResponse>("/api/v1/operator/shell/settings", {
      method: "PUT",
      body: { values },
      correlationId: options.correlationId,
      idempotencyKey: options.idempotencyKey,
    });
  }

  /** Franchisee-scoped view. A separate resource from the operator console. */
  getShellFranchisee(storeId?: string): Promise<ShellFranchiseeResponse> {
    return this.request<ShellFranchiseeResponse>("/api/v1/operator/shell/franchisee", {
      query: { storeId },
    });
  }

  acknowledgeShellFranchiseeNotification(
    body: { notificationId: string; storeId?: string },
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<ShellWriteResponse> {
    return this.request<ShellWriteResponse>("/api/v1/operator/shell/franchisee/acknowledgement", {
      method: "POST",
      body,
      correlationId: options.correlationId,
      idempotencyKey: options.idempotencyKey,
    });
  }

  submitShellFranchiseeReport(
    body: { category: string; message: string; storeId?: string },
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<ShellFranchiseeReportResponse> {
    return this.request<ShellFranchiseeReportResponse>(
      "/api/v1/operator/shell/franchisee/reports",
      {
        method: "POST",
        body,
        correlationId: options.correlationId,
        idempotencyKey: options.idempotencyKey,
      },
    );
  }

  // --- Network Listing Radar & Assisted Intake API methods ---

  getNetworkListings(
    options: { selectedHeatZoneId?: string; lens?: string; correlationId?: string } = {},
  ): Promise<NetworkListingRadarSnapshot> {
    const query: Record<string, string> = {};
    if (options.selectedHeatZoneId) query.selectedHeatZoneId = options.selectedHeatZoneId;
    if (options.lens) query.lens = options.lens;
    return this.request<NetworkListingRadarSnapshot>("/api/v1/operator/network-listings", {
      query,
      correlationId: options.correlationId,
    });
  }

  resetNetworkListings(
    options: { correlationId?: string } = {},
  ): Promise<NetworkListingRadarSnapshot> {
    return this.request<NetworkListingRadarSnapshot>("/api/v1/operator/network-listings/reset", {
      method: "POST",
      correlationId: options.correlationId,
    });
  }

  convertListing(
    listingId: string,
    payload: NetworkListingActorPayload,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<ConvertListingResponse> {
    return this.request<ConvertListingResponse>(`/api/v1/operator/network-listings/listings/${listingId}/convert`, {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
      idempotencyKey: options.idempotencyKey,
    });
  }

  mergeListing(
    listingId: string,
    payload: NetworkListingMergePayload,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<MergeListingResponse> {
    return this.request<MergeListingResponse>(`/api/v1/operator/network-listings/listings/${listingId}/merge`, {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
      idempotencyKey: options.idempotencyKey,
    });
  }

  archiveListing(
    listingId: string,
    payload: NetworkListingActorPayload,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<ArchiveListingResponse> {
    return this.request<ArchiveListingResponse>(`/api/v1/operator/network-listings/listings/${listingId}/archive`, {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
      idempotencyKey: options.idempotencyKey,
    });
  }

  async submitIntake(
    payload: IntakeSubmitPayload,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<CanonicalIntakeRuntimeDetail> {
    const heatZoneId =
      payload.heatZoneId &&
      /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
        payload.heatZoneId,
      )
        ? payload.heatZoneId
        : undefined;
    const receipt = await this.request<IntakeSubmissionReceipt>("/api/v1/intakes/url", {
      method: "POST",
      body: {
        original_url: payload.url,
        scope: {
          tenant_id: this.canonicalTenantId(),
          heat_zone_id: heatZoneId,
        },
      },
      correlationId: options.correlationId,
      idempotencyKey: this.commandIdempotencyKey(options.idempotencyKey),
    });
    return this.getIntake(receipt.intake_id);
  }

  submitIntakeBatch(
    payload: BatchIntakeRequest,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<BatchIntakeReceipt> {
    return this.request<BatchIntakeReceipt>("/api/v1/intake-batches", {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
      idempotencyKey: this.commandIdempotencyKey(options.idempotencyKey),
    });
  }

  listIntakes(options: IntakeInboxQuery = {}): Promise<IntakeInboxPage> {
    const sort =
      options.sort ??
      (options.sortBy
        ? (`${options.sortBy}_${options.sortOrder ?? "desc"}` as IntakeInboxQuery["sort"])
        : undefined);
    const query: RequestOptions["query"] = {
      cursor: options.cursor,
      page_size: String(options.page_size ?? options.pageSize ?? 50),
      sort,
      status: options.status ?? (options.intakeStage ? [options.intakeStage] : undefined),
      intake_method:
        options.intake_method ?? (options.intakeMethod ? [options.intakeMethod] : undefined),
      source_id: options.source_id,
      match_outcome:
        options.match_outcome ?? (options.matchOutcome ? [options.matchOutcome] : undefined),
      submitted_by: options.submitted_by,
      needs_review:
        options.needs_review === undefined ? undefined : String(options.needs_review),
      owner_subject_id: options.owner_subject_id,
      assignment_status: options.assignment_status,
      assigned: options.assigned === undefined ? undefined : String(options.assigned),
      sla_state: options.sla_state ?? (options.slaState ? [options.slaState] : undefined),
      assigned_area_id: options.assigned_area_id,
      heat_zone_id: options.heat_zone_id ?? options.selectedHeatZoneId,
      observed_from: options.observed_from,
      observed_to: options.observed_to,
      updated_from: options.updated_from,
      updated_to: options.updated_to,
      restricted_data:
        options.restricted_data === undefined ? undefined : String(options.restricted_data),
      quarantined:
        options.quarantined === undefined ? undefined : String(options.quarantined),
      failed: options.failed === undefined ? undefined : String(options.failed),
      retryable: options.retryable === undefined ? undefined : String(options.retryable),
      saved_view_id: options.saved_view_id ?? options.savedView,
      q: options.q ?? options.search,
    };
    return this.request<IntakeInboxPage>("/api/v1/intakes", {
      query,
    });
  }

  getIntake(intakeId: string): Promise<CanonicalIntakeRuntimeDetail> {
    return this.request<CanonicalIntakeRuntimeDetail>(`/api/v1/intakes/${intakeId}`);
  }

  /** Authoritative route-loader readback; never composed from the legacy radar projection. */
  getIntakeRuntimeDetail(intakeId: string): Promise<CanonicalIntakeRuntimeDetail> {
    return this.getIntake(intakeId);
  }

  getListing(listingId: string): Promise<ListingDetail> {
    return this.request<ListingDetail>(
      `/api/v1/listings/${encodeURIComponent(listingId)}`,
    );
  }

  getIntakeInboxBootstrap(): Promise<CanonicalIntakeInboxBootstrap> {
    return this.request<CanonicalIntakeInboxBootstrap>("/api/v1/intakes/bootstrap");
  }

  listSavedViews(): Promise<CanonicalSavedView[]> {
    return this.request<CanonicalSavedView[]>("/api/v1/saved-views");
  }

  createSavedView(
    payload: CanonicalSavedViewRequest,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<CanonicalSavedView> {
    return this.request<CanonicalSavedView>("/api/v1/saved-views", {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
      idempotencyKey: this.commandIdempotencyKey(options.idempotencyKey),
    });
  }

  /** Authoritative comparison, signals and backend-produced identity graph plan. */
  getMatchCase(matchCaseId: string): Promise<CanonicalMatchCaseDetail> {
    return this.request<CanonicalMatchCaseDetail>(`/api/v1/match-cases/${matchCaseId}`);
  }

  proposeMatchDecision(
    matchCaseId: string,
    payload: CanonicalMatchDecisionCommand,
    options: { correlationId?: string; idempotencyKey: string; ifMatch: string },
  ): Promise<CanonicalIdentityDecisionReceipt> {
    return this.request<CanonicalIdentityDecisionReceipt>(
      `/api/v1/match-cases/${matchCaseId}/decisions`,
      {
        method: "POST",
        body: payload,
        correlationId: options.correlationId,
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      },
    );
  }

  getIdentityDecision(decisionId: string): Promise<CanonicalIdentityDecisionReceipt> {
    return this.request<CanonicalIdentityDecisionReceipt>(
      `/api/v1/identity-decisions/${decisionId}`,
    );
  }

  reviewIdentityDecision(
    decisionId: string,
    payload: CanonicalIdentityReviewCommand,
    options: { correlationId?: string; idempotencyKey: string; ifMatch: string },
  ): Promise<CanonicalIdentityDecisionReceipt> {
    return this.request<CanonicalIdentityDecisionReceipt>(
      `/api/v1/identity-decisions/${decisionId}/actions/review`,
      {
        method: "POST",
        body: payload,
        correlationId: options.correlationId,
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      },
    );
  }

  proposeIdentityMerge(
    payload: CanonicalMergeCommand,
    options: { correlationId?: string; idempotencyKey: string; ifMatch: string },
  ): Promise<CanonicalIdentityDecisionReceipt> {
    return this.request<CanonicalIdentityDecisionReceipt>("/api/v1/identity/merge", {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
      idempotencyKey: options.idempotencyKey,
      ifMatch: options.ifMatch,
    });
  }

  proposeIdentitySplit(
    payload: CanonicalSplitCommand,
    options: { correlationId?: string; idempotencyKey: string; ifMatch: string },
  ): Promise<CanonicalIdentityDecisionReceipt> {
    return this.request<CanonicalIdentityDecisionReceipt>("/api/v1/identity/split", {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
      idempotencyKey: options.idempotencyKey,
      ifMatch: options.ifMatch,
    });
  }

  proposeIdentityUnmerge(
    payload: CanonicalUnmergeCommand,
    options: { correlationId?: string; idempotencyKey: string; ifMatch: string },
  ): Promise<CanonicalIdentityDecisionReceipt> {
    return this.request<CanonicalIdentityDecisionReceipt>("/api/v1/identity/unmerge", {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
      idempotencyKey: options.idempotencyKey,
      ifMatch: options.ifMatch,
    });
  }

  requestIdentityDecisionReversal(
    decisionId: string,
    payload: CanonicalRiskReasonCommand,
    options: { correlationId?: string; idempotencyKey: string; ifMatch: string },
  ): Promise<CanonicalIdentityDecisionReceipt> {
    return this.request<CanonicalIdentityDecisionReceipt>(
      `/api/v1/identity-decisions/${decisionId}/actions/reverse`,
      {
        method: "POST",
        body: payload,
        correlationId: options.correlationId,
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      },
    );
  }

  async correctIntake(
    intakeId: string,
    payload: IntakeCorrectPayload,
    options: { correlationId?: string; idempotencyKey?: string; ifMatch?: string } = {},
  ): Promise<CanonicalIntakeRuntimeDetail> {
    let version = Number(
      options.ifMatch?.match(/[1-9][0-9]*/)?.[0] ??
        (await this.getIntakeRuntimeDetail(intakeId)).version,
    );
    for (const [fieldPath, correctedValue] of Object.entries(payload.fields)) {
      if (correctedValue === undefined) continue;
      const correction = await this.request<CanonicalCorrectionReceipt>(
        `/api/v1/intakes/${intakeId}/corrections`,
        {
          method: "POST",
          body: {
            field_path: fieldPath,
            corrected_value: correctedValue,
            reason: payload.reason || payload.riskSummary,
            risk_acknowledged: payload.riskAcknowledged,
          },
          correlationId: options.correlationId,
          idempotencyKey: this.commandIdempotencyKey(
            options.idempotencyKey,
            fieldPath,
          ),
          ifMatch: `W/"${version}"`,
        },
      );
      version = correction.version;
    }
    return this.getIntake(intakeId);
  }

  async decideIntake(
    intakeId: string,
    payload: IntakeDecidePayload,
    options: { correlationId?: string; idempotencyKey?: string; ifMatch?: string } = {},
  ): Promise<CanonicalIntakeRuntimeDetail> {
    const intake = await this.getIntakeRuntimeDetail(intakeId);
    if (!intake.match_case_id || !intake.match_case) {
      throw new Error(`Intake ${intakeId} has no canonical matchCaseId`);
    }
    await this.proposeMatchDecision(
      intake.match_case_id,
      {
        decision_type: payload.action.toUpperCase() as CanonicalMatchDecisionCommand["decision_type"],
        reason: payload.reason || payload.riskSummary,
        risk_acknowledged: payload.riskAcknowledged,
        target_listing_id: intake.match_case.target_listing_id,
      },
      {
        correlationId: options.correlationId,
        idempotencyKey: this.commandIdempotencyKey(options.idempotencyKey),
        ifMatch: options.ifMatch ?? `W/"${intake.match_case_version}"`,
      },
    );
    return this.getIntake(intakeId);
  }

  async retryIntake(
    intakeId: string,
    payload: NetworkListingActorPayload,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<CanonicalIntakeRuntimeDetail> {
    const intake = await this.getIntakeRuntimeDetail(intakeId);
    await this.request<CanonicalTransitionReceipt>(
      `/api/v1/intakes/${intakeId}/actions/reopen`,
      {
        method: "POST",
        body: {
          reason: payload.reason || "Operator requested retry",
          risk_acknowledged: true,
        },
        correlationId: options.correlationId,
        idempotencyKey: this.commandIdempotencyKey(options.idempotencyKey),
        ifMatch: `W/"${intake.version}"`,
      },
    );
    return this.getIntake(intakeId);
  }

  async reopenIntake(
    intakeId: string,
    payload: CanonicalRiskReasonCommand,
    options: { correlationId?: string; idempotencyKey: string; ifMatch: string },
  ): Promise<CanonicalIntakeRuntimeDetail> {
    await this.reopenIntakeRuntime(intakeId, payload, options);
    return this.getIntake(intakeId);
  }

  promoteIntake(
    intakeId: string,
    payload: IntakePromotePayload,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<ConvertListingResponse> {
    return this.request<ConvertListingResponse>(`/api/v1/operator/network-listings/intake/${intakeId}/promote`, {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
      idempotencyKey: options.idempotencyKey,
    });
  }

  assignIntake(
    intakeId: string,
    payload: AssignmentRequest,
    options: { correlationId?: string; idempotencyKey?: string; ifMatch?: string } = {},
  ): Promise<AssignmentReceipt> {
    return this.request<AssignmentReceipt>(`/api/v1/intakes/${intakeId}/assignment`, {
      method: "PUT",
      body: payload,
      correlationId: options.correlationId,
      idempotencyKey: options.idempotencyKey,
      ifMatch: options.ifMatch,
    });
  }

  claimAssignment(
    assignmentId: string,
    payload: ReasonCommand,
    options: { correlationId?: string; idempotencyKey?: string; ifMatch?: string } = {},
  ): Promise<AssignmentReceipt> {
    return this.request<AssignmentReceipt>(`/api/v1/assignments/${assignmentId}/actions/claim`, {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
      idempotencyKey: options.idempotencyKey,
      ifMatch: options.ifMatch,
    });
  }

  transferAssignment(
    assignmentId: string,
    payload: AssignmentTransferRequest,
    options: { correlationId?: string; idempotencyKey?: string; ifMatch?: string } = {},
  ): Promise<AssignmentReceipt> {
    return this.request<AssignmentReceipt>(`/api/v1/assignments/${assignmentId}/actions/transfer`, {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
      idempotencyKey: options.idempotencyKey,
      ifMatch: options.ifMatch,
    });
  }

  completeAssignment(
    assignmentId: string,
    payload: ReasonCommand,
    options: { correlationId?: string; idempotencyKey?: string; ifMatch?: string } = {},
  ): Promise<AssignmentReceipt> {
    return this.request<AssignmentReceipt>(`/api/v1/assignments/${assignmentId}/actions/complete`, {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
      idempotencyKey: options.idempotencyKey,
      ifMatch: options.ifMatch,
    });
  }

  escalateAssignment(
    assignmentId: string,
    payload: ReasonCommand,
    options: { correlationId?: string; idempotencyKey: string; ifMatch: string },
  ): Promise<AssignmentReceipt> {
    return this.request<AssignmentReceipt>(
      `/api/v1/assignments/${assignmentId}/actions/escalate`,
      {
        method: "POST",
        body: payload,
        correlationId: options.correlationId,
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      },
    );
  }

  cancelIntakeRuntime(
    intakeId: string,
    payload: ReasonCommand,
    options: { correlationId?: string; idempotencyKey: string; ifMatch: string },
  ): Promise<CanonicalTransitionReceipt> {
    return this.request<CanonicalTransitionReceipt>(
      `/api/v1/intakes/${intakeId}/actions/cancel`,
      {
        method: "POST",
        body: payload,
        correlationId: options.correlationId,
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      },
    );
  }

  reopenIntakeRuntime(
    intakeId: string,
    payload: CanonicalRiskReasonCommand,
    options: { correlationId?: string; idempotencyKey: string; ifMatch: string },
  ): Promise<CanonicalTransitionReceipt> {
    return this.request<CanonicalTransitionReceipt>(
      `/api/v1/intakes/${intakeId}/actions/reopen`,
      {
        method: "POST",
        body: payload,
        correlationId: options.correlationId,
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      },
    );
  }

  pauseSla(
    slaInstanceId: string,
    payload: SlaPauseRequest,
    options: { correlationId?: string; idempotencyKey?: string; ifMatch?: string } = {},
  ): Promise<SlaReceipt> {
    return this.request<SlaReceipt>(`/api/v1/sla-instances/${slaInstanceId}/actions/pause`, {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
      idempotencyKey: options.idempotencyKey,
      ifMatch: options.ifMatch,
    });
  }

  resumeSla(
    slaInstanceId: string,
    payload: ReasonCommand,
    options: { correlationId?: string; idempotencyKey?: string; ifMatch?: string } = {},
  ): Promise<SlaReceipt> {
    return this.request<SlaReceipt>(`/api/v1/sla-instances/${slaInstanceId}/actions/resume`, {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
      idempotencyKey: options.idempotencyKey,
      ifMatch: options.ifMatch,
    });
  }

  // -------------------------------------------------------------------------
  // Candidate promotion saga (ODP-INTAKE-UX-PROMOTION-001). These are the v1
  // assisted-intake contract routes (docs/api/openapi/
  // ODAY_PLUS_ASSISTED_LISTING_INTAKE_V1.yaml). Every write is a high-impact
  // mutation: Idempotency-Key and If-Match are REQUIRED, not optional, and the
  // response envelope carries `idempotencyReplayed` so the UI can label a
  // durable-receipt replay instead of presenting it as a fresh write.
  // -------------------------------------------------------------------------

  /** POST /api/v1/intakes/{intake_id}/promotion-requests (202). */
  async requestCandidatePromotion(
    intakeId: string,
    payload: PromotionRequest,
    options: { correlationId?: string; idempotencyKey: string; ifMatch: string },
  ): Promise<{ receipt: PromotionDecisionReceipt; idempotencyReplayed: boolean }> {
    const { value, headers } = await this.requestWithMeta<PromotionDecisionReceipt>(
      `/api/v1/intakes/${intakeId}/promotion-requests`,
      {
        method: "POST",
        body: payload,
        correlationId: options.correlationId,
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      },
    );
    return { receipt: value, idempotencyReplayed: headers.get("Idempotency-Replayed") === "true" };
  }

  /** GET /api/v1/promotion-decisions/{promotion_decision_id} — lost-response recovery lookup. */
  getPromotionDecision(
    promotionDecisionId: string,
    options: { correlationId?: string } = {},
  ): Promise<PromotionDecisionReceipt> {
    return this.request<PromotionDecisionReceipt>(
      `/api/v1/promotion-decisions/${promotionDecisionId}`,
      { correlationId: options.correlationId },
    );
  }

  /** GET /api/v1/intakes/{intake_id}/promotion-decision — durable reload recovery. */
  getIntakePromotionDecision(
    intakeId: string,
    options: { correlationId?: string } = {},
  ): Promise<PromotionDecisionReceipt> {
    return this.request<PromotionDecisionReceipt>(
      `/api/v1/intakes/${intakeId}/promotion-decision`,
      { correlationId: options.correlationId },
    );
  }

  /** POST /api/v1/promotion-decisions/{id}/actions/review — independent second-actor decision. */
  async reviewPromotionDecision(
    promotionDecisionId: string,
    payload: ReviewDecisionRequest,
    options: { correlationId?: string; idempotencyKey: string; ifMatch: string },
  ): Promise<{ receipt: PromotionDecisionReceipt; idempotencyReplayed: boolean }> {
    const { value, headers } = await this.requestWithMeta<PromotionDecisionReceipt>(
      `/api/v1/promotion-decisions/${promotionDecisionId}/actions/review`,
      {
        method: "POST",
        body: payload,
        correlationId: options.correlationId,
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      },
    );
    return { receipt: value, idempotencyReplayed: headers.get("Idempotency-Replayed") === "true" };
  }

  /** POST /api/v1/jobs/{job_id}/retry (202) — replay from a durable checkpoint. */
  async retryJob(
    jobId: string,
    payload: RetryRequest,
    options: { correlationId?: string; idempotencyKey: string; ifMatch: string },
  ): Promise<{ receipt: JobReceipt; idempotencyReplayed: boolean }> {
    const { value, headers } = await this.requestWithMeta<JobReceipt>(
      `/api/v1/jobs/${jobId}/retry`,
      {
        method: "POST",
        body: payload,
        correlationId: options.correlationId,
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      },
    );
    return { receipt: value, idempotencyReplayed: headers.get("Idempotency-Replayed") === "true" };
  }

  /** Canonical replay command; alias retains the existing /retry contract path. */
  replayJob(
    jobId: string,
    payload: RetryRequest,
    options: { correlationId?: string; idempotencyKey: string; ifMatch: string },
  ): Promise<{ receipt: JobReceipt; idempotencyReplayed: boolean }> {
    return this.retryJob(jobId, payload, options);
  }

  async cancelJob(
    jobId: string,
    payload: ReasonCommand,
    options: { correlationId?: string; idempotencyKey: string; ifMatch: string },
  ): Promise<{ receipt: JobReceipt; idempotencyReplayed: boolean }> {
    const { value, headers } = await this.requestWithMeta<JobReceipt>(
      `/api/v1/jobs/${jobId}/actions/cancel`,
      {
        method: "POST",
        body: payload,
        correlationId: options.correlationId,
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      },
    );
    return {
      receipt: value,
      idempotencyReplayed: headers.get("Idempotency-Replayed") === "true",
    };
  }

  /** GET /api/v1/jobs/{job_id}/receipt — authoritative durable job receipt. */
  async getJobReceipt(jobId: string): Promise<JobReceipt> {
    return this.request<JobReceipt>(`/api/v1/jobs/${jobId}/receipt`);
  }

  getAvmCase(caseId: string): Promise<AvmCase> {
    return this.request<AvmCase>(`/avm/cases/${caseId}`);
  }

  getAvmCaseReport(caseId: string): Promise<any> {
    return this.request<any>(`/avm/cases/${caseId}/report`);
  }

  getAvmCaseDataRoom(caseId: string): Promise<any> {
    return this.request<any>(`/avm/cases/${caseId}/dataroom`);
  }
}

// ---------------------------------------------------------------------------
// Assisted listing intake contract (ODP-OC-R5-011)
//
// These mirror modules/external_data/application/assisted_intake.py. The wire
// format carries `policyLabel` and `matchResult.outcomeLabel` inline but has
// NO `stageLabel`, so the stage labels below are the single shared source of
// truth for TypeScript callers instead of being re-typed per surface.
// ---------------------------------------------------------------------------

/** assisted_intake.INTAKE_STAGES */
export type IntakeStage =
  | "SUBMITTED"
  | "CHECKING_IDENTITY"
  | "CHECKING_SOURCE_POLICY"
  | "AWAITING_ASSISTED_ENTRY"
  | "RETRIEVING"
  | "PARSING"
  | "MATCHING"
  | "NEEDS_REVIEW"
  | "READY"
  | "QUARANTINED"
  | "FAILED"
  | "CANCELLED";

export const INTAKE_STAGES: readonly IntakeStage[] = [
  "SUBMITTED",
  "CHECKING_IDENTITY",
  "CHECKING_SOURCE_POLICY",
  "AWAITING_ASSISTED_ENTRY",
  "RETRIEVING",
  "PARSING",
  "MATCHING",
  "NEEDS_REVIEW",
  "READY",
  "QUARANTINED",
  "FAILED",
  "CANCELLED",
] as const;

/** assisted_intake.STAGE_LABEL */
export const INTAKE_STAGE_LABEL: Record<IntakeStage, string> = {
  SUBMITTED: "已送出",
  CHECKING_IDENTITY: "識別檢查",
  CHECKING_SOURCE_POLICY: "來源政策",
  AWAITING_ASSISTED_ENTRY: "待人工補錄",
  RETRIEVING: "擷取中",
  PARSING: "解析中",
  MATCHING: "比對中",
  NEEDS_REVIEW: "待人工覆核",
  READY: "可決策",
  QUARANTINED: "已隔離",
  FAILED: "處理失敗",
  CANCELLED: "已取消",
};

/** assisted_intake.TERMINAL_STAGES */
export const TERMINAL_INTAKE_STAGES: readonly IntakeStage[] = [
  "NEEDS_REVIEW",
  "READY",
  "QUARANTINED",
  "FAILED",
  "AWAITING_ASSISTED_ENTRY",
  "CANCELLED",
] as const;

/** assisted_intake.SOURCE_POLICY_STATES */
export type SourcePolicyState =
  | "APPROVED_RETRIEVAL"
  | "ASSISTED_ENTRY_ONLY"
  | "AUTH_REQUIRED"
  | "SOURCE_BLOCKED"
  | "POLICY_UNKNOWN";

/** assisted_intake.SOURCE_POLICY_LABEL */
export const SOURCE_POLICY_LABEL: Record<SourcePolicyState, string> = {
  APPROVED_RETRIEVAL: "已核准擷取",
  ASSISTED_ENTRY_ONLY: "僅人工補錄",
  AUTH_REQUIRED: "需授權帳號",
  SOURCE_BLOCKED: "來源封鎖",
  POLICY_UNKNOWN: "政策未知",
};

/** assisted_intake.MATCH_OUTCOMES */
export type MatchOutcome =
  | "NEW"
  | "EXACT_DUPLICATE"
  | "REVISION"
  | "POSSIBLE_MATCH"
  | "QUARANTINED";

/** assisted_intake.MATCH_OUTCOME_LABEL */
export const MATCH_OUTCOME_LABEL: Record<MatchOutcome, string> = {
  NEW: "新物件",
  EXACT_DUPLICATE: "完全重複",
  REVISION: "版本更新",
  POSSIBLE_MATCH: "疑似重複",
  QUARANTINED: "已隔離",
};

/**
 * assisted_intake.IDENTITY_FIELDS — correcting any of these requires a reason
 * (the server returns 422 without one), so the dialog must demand it up front.
 */
export const INTAKE_IDENTITY_FIELDS = [
  "providerListingId",
  "address",
  "rent",
  "areaPing",
] as const;

/** assisted_intake.CORRECTABLE_FIELDS */
export const INTAKE_CORRECTABLE_FIELDS = [
  ...INTAKE_IDENTITY_FIELDS,
  "floor",
  "listingType",
  "listingStatus",
] as const;

export type IntakeCorrectableField = (typeof INTAKE_CORRECTABLE_FIELDS)[number];

/** assisted_intake.ASSISTED_ENTRY_REQUIRED_FIELDS */
export const ASSISTED_ENTRY_REQUIRED_FIELDS = ["address", "rent", "areaPing"] as const;

/** Accepted by NetworkListingService.decide_intake. */
export type IntakeDecideAction =
  | "create"
  | "revise"
  | "duplicate"
  | "quarantine"
  | "reject";

export type IntakeFieldValue = string | number | boolean | null;

export type IntakeFieldCell = {
  key: string;
  label: string;
  sourceValue: IntakeFieldValue;
  normalizedValue: IntakeFieldValue;
  correctedValue: IntakeFieldValue;
  correctionReason: string | null;
  identity: boolean;
  lowConfidence: boolean;
  masked?: boolean;
  mask_reason_code?: string;
};

export type MatchSignalDto = {
  key: string;
  label: string;
  agrees: boolean;
  detail: string;
};

export type MatchResultDto = {
  outcome: MatchOutcome;
  outcomeLabel: string;
  confidence: number;
  targetListingId: string | null;
  agreeingSignals: MatchSignalDto[];
  contradictingSignals: MatchSignalDto[];
  summary: string;
};

/**
 * Written by decide/correct/promote/merge so the audit trail keeps the
 * before/after values and the risk disclosure made at the point of decision.
 */
export type IntakeAuditMetadata = {
  beforeAfter?: Record<string, { before?: IntakeFieldValue; after?: IntakeFieldValue }>;
  /** The caller-supplied text the operator was shown and accepted. */
  riskSummary?: string;
  /** True only when the operator explicitly acknowledged `riskSummary`. */
  riskAcknowledged?: boolean;
  /**
   * Server-derived description of what the write actually did. Kept separate
   * from `riskSummary` so it is never mistaken for acknowledged text.
   */
  effectSummary?: string;
  [key: string]: unknown;
};

export type IntakeAuditEvent = {
  id: string;
  occurredAt: string;
  actorRoleId: string;
  actorName: string;
  action: string;
  targetId: string;
  message: string;
  correlationId: string | null;
  metadata?: IntakeAuditMetadata;
};

export type IntakeFailure = {
  code: string;
  summary: string;
  nextAction: string;
  retryable: boolean;
};

export type IntakeSubmissionNavigationReceipt = {
  receiptId: string;
  receiptType: string;
  existingListingId: string | null;
  navigationTarget: string | null;
  issuedAt: string;
};

export type AssistedIntake = {
  id: string;
  originalUrl: string;
  canonicalUrl: string;
  submitter: string;
  owner: string;
  heatZoneId: string | null;
  intakeMethod?: "URL" | "MANUAL" | "CSV" | "APPROVED_FEED";
  stage: IntakeStage;
  sourceId: string;
  policy: SourcePolicyState;
  policyLabel: string;
  policyReason: string;
  rawSnapshot: Record<string, unknown> | null;
  snapshotId: string | null;
  capturedAt: string | null;
  parserVersion: string;
  correlationId: string | null;
  matchCaseId?: string | null;
  jobId?: string | null;
  parsedFields: Record<string, IntakeFieldCell>;
  matchResult: MatchResultDto | null;
  auditEvents: IntakeAuditEvent[];
  idempotencyKey?: string | null;
  failure?: IntakeFailure | null;
  submissionReceipt?: IntakeSubmissionNavigationReceipt | null;
  version: number;
  assignmentId?: string | null;
  assignmentStatus?: string | null;
  assignmentVersion?: number | null;
  slaInstanceId?: string | null;
  slaState?: string | null;
  slaVersion?: number | null;
  slaReceipt?: string | null;
  dueAt?: string | null;
};

export type IntakeInboxQuery = {
  cursor?: string;
  page_size?: number;
  sort?: "submitted_at_desc" | "updated_at_desc" | "due_at_asc" | "status_asc";
  status?: string[];
  intake_method?: Array<"URL" | "MANUAL" | "CSV" | "APPROVED_FEED" | "OPERATOR_SNAPSHOT">;
  source_id?: string[];
  match_outcome?: string[];
  submitted_by?: string;
  needs_review?: boolean;
  owner_subject_id?: string[];
  assignment_status?: Array<
    "ASSIGNED" | "CLAIMED" | "TRANSFERRED" | "ESCALATED" | "COMPLETED"
  >;
  assigned?: boolean;
  sla_state?: Array<
    "ON_TRACK" | "DUE_SOON" | "OVERDUE" | "BREACHED" | "PAUSED" | "COMPLETED"
  >;
  assigned_area_id?: string;
  heat_zone_id?: string;
  observed_from?: string;
  observed_to?: string;
  updated_from?: string;
  updated_to?: string;
  restricted_data?: boolean;
  quarantined?: boolean;
  failed?: boolean;
  retryable?: boolean;
  saved_view_id?: string;
  q?: string;
  // Temporary aliases for the existing Inbox caller. They are translated to
  // canonical query names and never routed through the legacy endpoint.
  selectedHeatZoneId?: string;
  pageSize?: number;
  search?: string;
  savedView?: string;
  intakeMethod?: string;
  intakeStage?: string;
  matchOutcome?: string;
  slaState?: string;
  sortBy?: string;
  sortOrder?: "asc" | "desc";
};

export type CanonicalInboxLocationSummary = {
  address: string | null;
  district: string | null;
  assigned_area_id: string | null;
  heat_zone_id: string | null;
  latitude: number | null;
  longitude: number | null;
  confidence: number | null;
  source: string | null;
};

export type CanonicalInboxMaskingSummary = {
  restricted_data: boolean;
  has_masked_fields: boolean;
  masked_fields: string[];
  reason_codes: string[];
};

export type CanonicalIntakeSummary = {
  intake_id: string;
  state: string;
  intake_method: string;
  source_id: string | null;
  original_url: string | null;
  canonical_url: string | null;
  policy_state: string | null;
  match_outcome: string | null;
  submitted_by: string;
  assigned_to: string | null;
  assignment_id: string | null;
  assignment_status:
    | "ASSIGNED"
    | "CLAIMED"
    | "TRANSFERRED"
    | "ESCALATED"
    | "COMPLETED"
    | null;
  assignment_version: number | null;
  owner_subject_id: string | null;
  queue_id: string | null;
  sla_instance_id: string | null;
  sla_state:
    | "ON_TRACK"
    | "DUE_SOON"
    | "OVERDUE"
    | "BREACHED"
    | "PAUSED"
    | "COMPLETED"
    | null;
  sla_version: number | null;
  due_at: string | null;
  last_observed_at: string | null;
  submitted_at: string;
  updated_at: string;
  version: number;
  scope: {
    tenant_id: string;
    assigned_area_id?: string | null;
    brand_id?: string | null;
    heat_zone_id?: string | null;
    region_id?: string | null;
  };
  issue: string | null;
  next_action: string | null;
  retryable: boolean;
  quarantined: boolean;
  failed: boolean;
  location: CanonicalInboxLocationSummary;
  masking: CanonicalInboxMaskingSummary;
  masked_fields: string[];
};

export type IntakeInboxPage = {
  items: CanonicalIntakeSummary[];
  next_cursor: string | null;
  page_size: number;
  total_count: number;
  total_count_accuracy: "EXACT" | "ESTIMATED";
  snapshot_time: string;
  query_fingerprint: string;
};

export type CanonicalSavedViewRequest = {
  name: string;
  query: IntakeInboxQuery;
  resource: "intake";
  shared_role?: IntakeRoleMode | null;
  visibility: "PRIVATE" | "ROLE" | "TENANT";
};

export type CanonicalSavedView = CanonicalSavedViewRequest & {
  saved_view_id: string;
  owner_subject_id: string;
  created_at: string;
  version: number;
};

export type CanonicalInboxHeatZone = {
  heat_zone_id: string;
  label: string;
  assigned_area_id: string | null;
  region_id: string | null;
  rank: number | null;
};

export type CanonicalInboxCommandContract = {
  method: "POST" | "PUT";
  path_template: string;
  requires_if_match: boolean;
  requires_idempotency_key: boolean;
};

export type CanonicalIntakeInboxBootstrap = {
  tenant_id: string;
  subject_id: string;
  role_mode: IntakeRoleMode;
  scope: {
    tenant_id: string;
    brand_ids: string[];
    region_ids: string[];
    assigned_area_ids: string[];
    heat_zone_ids: string[];
  };
  heat_zones: CanonicalInboxHeatZone[];
  selected_heat_zone_id: string | null;
  intake_methods: Array<"URL" | "MANUAL" | "CSV" | "APPROVED_FEED" | "OPERATOR_SNAPSHOT">;
  intake_states: string[];
  match_outcomes: Array<
    "NEW" | "EXACT_DUPLICATE" | "REVISION" | "POSSIBLE_MATCH" | "QUARANTINED"
  >;
  assignment_states: Array<
    "ASSIGNED" | "CLAIMED" | "TRANSFERRED" | "ESCALATED" | "COMPLETED"
  >;
  sla_states: Array<
    "ON_TRACK" | "DUE_SOON" | "OVERDUE" | "BREACHED" | "PAUSED" | "COMPLETED"
  >;
  saved_views: CanonicalSavedView[];
  commands: {
    assign: CanonicalInboxCommandContract;
    claim: CanonicalInboxCommandContract;
    transfer: CanonicalInboxCommandContract;
    complete: CanonicalInboxCommandContract;
  };
};

export type NetworkListingRadarSnapshot = {
  source: string;
  heatZones: Array<{
    id: string;
    label: string;
    rank: number;
    centroid: [number, number];
    demandGap: number;
    competitionIndex: number;
    cannibalizationRisk: string;
    rentBand: string;
    confidence: number;
    recommendedLens: string;
    reasons: string[];
    risks: string[];
    nextStep: string;
  }>;
  listingSources: Array<{
    id: string;
    name: string;
    status: string;
    complianceNote: string;
    lastSyncedAt: string;
  }>;
  listings: Array<{
    id: string;
    sourceId: string;
    sourceListingId: string;
    heatZoneId: string;
    address: string;
    status: string;
    rentPerMonth: number;
    areaPing: number;
    floor: string;
    frontageMeters: number;
    geocodeConfidence: number;
    hardRuleFailures: string[];
    hardRuleSummary: string;
    sourceEvidence: string[];
    fitScore: number;
    firstSeenAt: string;
    sourceUrl: string;
    contentFingerprint?: string;
  }>;
  candidates: Array<{
    id: string;
    listingId: string;
    heatZoneId: string;
    title: string;
    address: string;
    status: string;
    score: number;
    recommendation: string;
    modelVersion: string;
    datasetSnapshotId: string;
    missingData: string[];
    reviewId?: string | null;
  }>;
  siteReviews: any[];
  assistedIntakes: AssistedIntake[];
  expansionSteps: any[];
  selectedHeatZoneId: string;
  selectedLens: string;
  auditEvents: any[];
  correlationId: string | null;
  counts: {
    heatZones: number;
    listings: number;
    candidates: number;
    siteReviews: number;
    assistedIntakes: number;
  };
};

export type ConvertListingResponse = {
  listing: any;
  candidate: any;
  created: boolean;
  auditEvent: any;
  candidateCount: number;
  correlationId: string | null;
  expansionSteps: any[];
};

export type MergeListingResponse = {
  source: any;
  target: any;
  sourceEvidenceRetained: string[];
  auditEvent: any;
  correlationId: string | null;
  expansionSteps: any[];
};

export type ArchiveListingResponse = {
  listing: any;
  auditEvent: any;
  correlationId: string | null;
  expansionSteps: any[];
};

export type NetworkListingActorPayload = {
  actorRoleId?: string;
  actorName?: string | null;
  reason?: string | null;
};

/**
 * The disclosure a high-impact write must carry.
 *
 * `riskSummary` is the exact text shown to the operator and `riskAcknowledged`
 * records that they accepted it; both are stored in the audit event. They are
 * required (not optional) so a caller cannot omit the disclosure and have the
 * server 422 at runtime — the server rejects a missing or unacknowledged
 * summary rather than inventing one.
 */
export type RiskDisclosure = {
  riskSummary: string;
  riskAcknowledged: boolean;
};

export type NetworkListingMergePayload = NetworkListingActorPayload &
  RiskDisclosure & {
    targetListingId: string;
  };

export type IntakeSubmitPayload = {
  url: string;
  heatZoneId?: string | null;
  actorRoleId?: string;
  actorName?: string | null;
};

export type IntakeCorrectPayload = RiskDisclosure & {
  /** Keyed by IntakeCorrectableField; a reason is mandatory for identity fields. */
  fields: Partial<Record<IntakeCorrectableField, IntakeFieldValue>>;
  reason?: string | null;
  actorRoleId?: string;
  actorName?: string | null;
};

export type IntakeDecidePayload = RiskDisclosure & {
  action: IntakeDecideAction;
  reason?: string | null;
  actorRoleId?: string;
  actorName?: string | null;
};

export type IntakePromotePayload = NetworkListingActorPayload & RiskDisclosure;

/**
 * Pin every hand-written narrowing to its generated counterpart.
 *
 * Each narrowing above is deliberately stricter than the server's schema (a
 * required `riskAcknowledged` where Pydantic's default renders it optional; a
 * `IntakeDecideAction` union where the schema says `string`). Strictness is
 * only safe while the narrowing remains *assignable* to the generated type: if
 * the server renames or retypes a field, the narrowing silently stops
 * describing the real request and callers keep compiling against a fiction.
 *
 * `AssertAssignable` makes that a build failure instead. These are type-level
 * only and emit no runtime code.
 */
type AssertAssignable<Narrow extends Wide, Wide> = Narrow;

type _PinIntakeSubmit = AssertAssignable<IntakeSubmitPayload, GeneratedIntakeSubmitPayload>;
type _PinIntakeCorrect = AssertAssignable<IntakeCorrectPayload, GeneratedIntakeCorrectPayload>;
type _PinIntakeDecide = AssertAssignable<IntakeDecidePayload, GeneratedIntakeDecidePayload>;
type _PinIntakePromote = AssertAssignable<IntakePromotePayload, GeneratedIntakePromotePayload>;
type _PinListingActor = AssertAssignable<
  NetworkListingActorPayload,
  GeneratedNetworkListingActorPayload
>;
type _PinListingMerge = AssertAssignable<
  NetworkListingMergePayload,
  GeneratedNetworkListingMergePayload
>;

/**
 * Build a client from explicit options or the environment. Returns `null`
 * when no base URL is configured, signalling callers to use fixture data.
 */
export function createOdpApiClient(
  options: Partial<OdpApiClientOptions> & {
    env?: Record<string, string | undefined>;
  } = {},
): OdpApiClient | null {
  const baseUrl = options.baseUrl ?? resolveApiBaseUrl(options.env);
  if (!baseUrl) {
    return null;
  }
  return new OdpApiClient({
    baseUrl,
    fetchImpl: options.fetchImpl,
    timeoutMs: options.timeoutMs,
    defaultHeaders: options.defaultHeaders,
  });
}
