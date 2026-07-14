/**
 * @oday-plus/openapi-client
 *
 * Hand-maintained typed client for the ODay Plus FastAPI backend
 * (apps/api/oday_api). It has no runtime dependencies beyond the platform
 * `fetch`, so it can run inside Next.js server components, the Playwright
 * test runner, or plain Node. Browser bundles MUST NOT import it directly —
 * the web app calls it only from server components (see
 * apps/web/src/lib/api).
 */

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

export class OdpApiError extends Error {
  readonly status: number;
  readonly url: string;
  readonly correlationId?: string;

  constructor(
    message: string,
    options: { status: number; url: string; correlationId?: string },
  ) {
    super(message);
    this.name = "OdpApiError";
    this.status = options.status;
    this.url = options.url;
    this.correlationId = options.correlationId;
  }
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
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch;
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
        headers: {
          accept: "application/json",
          ...(options.body !== undefined ? { "content-type": "application/json" } : {}),
          ...(options.correlationId ? { [CORRELATION_ID_HEADER]: options.correlationId } : {}),
          ...(options.idempotencyKey ? { "Idempotency-Key": options.idempotencyKey } : {}),
          ...this.defaultHeaders,
        },
        body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      });
      if (!response.ok) {
        throw new OdpApiError(`ODay API ${response.status} for ${path}`, {
          status: response.status,
          url,
          correlationId: response.headers.get(CORRELATION_ID_HEADER) ?? options.correlationId,
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
}

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
