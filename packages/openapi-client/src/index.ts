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

import type {
  IntakeCorrectPayload as GeneratedIntakeCorrectPayload,
  IntakeDecidePayload as GeneratedIntakeDecidePayload,
  IntakePromotePayload as GeneratedIntakePromotePayload,
  IntakeSubmitPayload as GeneratedIntakeSubmitPayload,
  NetworkListingActorPayload as GeneratedNetworkListingActorPayload,
  NetworkListingMergePayload as GeneratedNetworkListingMergePayload,
  ErrorEnvelope,
} from "./generated/types";

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

export type ApiErrorBody = {
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
  readonly nextAction?: string;

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
    this.code = this.envelope?.code;
    this.nextAction = this.envelope?.next_action;
    // Envelope first, then the header, then the caller's own id: the envelope
    // is the value the server recorded against the audit event.
    this.correlationId = this.envelope?.correlation_id ?? options.correlationId ?? undefined;
    // The envelope's message is already the flattened text, so prefer it and
    // fall back to flattening `detail` for any endpoint not yet behind the
    // handlers (and for older servers during a rollout).
    this.detail = this.envelope?.message ?? flattenApiDetail(options.body?.detail);
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
  query?: Record<string, string | undefined>;
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

  private async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const query = options.query
      ? Object.entries(options.query)
          .filter(([, value]) => value !== undefined && value !== "")
          .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
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
      return (await response.json()) as T;
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

  submitIntake(
    payload: IntakeSubmitPayload,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<AssistedIntake> {
    return this.request<AssistedIntake>("/api/v1/operator/network-listings/intake/submit", {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
      idempotencyKey: options.idempotencyKey,
    });
  }

  listIntakes(
    options: { selectedHeatZoneId?: string } = {},
  ): Promise<AssistedIntake[]> {
    const query: Record<string, string> = {};
    if (options.selectedHeatZoneId) query.selectedHeatZoneId = options.selectedHeatZoneId;
    return this.request<AssistedIntake[]>("/api/v1/operator/network-listings/intake", {
      query,
    });
  }

  getIntake(intakeId: string): Promise<AssistedIntake> {
    return this.request<AssistedIntake>(`/api/v1/operator/network-listings/intake/${intakeId}`);
  }

  correctIntake(
    intakeId: string,
    payload: IntakeCorrectPayload,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<AssistedIntake> {
    return this.request<AssistedIntake>(`/api/v1/operator/network-listings/intake/${intakeId}/correct`, {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
      idempotencyKey: options.idempotencyKey,
    });
  }

  decideIntake(
    intakeId: string,
    payload: IntakeDecidePayload,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<AssistedIntake> {
    return this.request<AssistedIntake>(`/api/v1/operator/network-listings/intake/${intakeId}/decide`, {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
      idempotencyKey: options.idempotencyKey,
    });
  }

  retryIntake(
    intakeId: string,
    payload: NetworkListingActorPayload,
    options: { correlationId?: string } = {},
  ): Promise<AssistedIntake> {
    return this.request<AssistedIntake>(`/api/v1/operator/network-listings/intake/${intakeId}/retry`, {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
    });
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
  | "FAILED";

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
};

/** assisted_intake.TERMINAL_STAGES */
export const TERMINAL_INTAKE_STAGES: readonly IntakeStage[] = [
  "NEEDS_REVIEW",
  "READY",
  "QUARANTINED",
  "FAILED",
  "AWAITING_ASSISTED_ENTRY",
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

export type AssistedIntake = {
  id: string;
  originalUrl: string;
  canonicalUrl: string;
  submitter: string;
  owner: string;
  heatZoneId: string | null;
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
  parsedFields: Record<string, IntakeFieldCell>;
  matchResult: MatchResultDto | null;
  auditEvents: IntakeAuditEvent[];
  idempotencyKey?: string | null;
  failure?: IntakeFailure | null;
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
