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

export type SourceFreshnessEvidence = {
  provider_id: string;
  source_snapshot_id: string;
  data_status: string;
  provider_observed_at?: string | null;
  ingested_at?: string | null;
  freshness_sla_seconds: number;
  correlation_id: string;
  quality_flags?: string[];
};

export type ExternalDataFreshnessResponse = {
  freshness: SourceFreshnessEvidence[];
  correlation_id: string;
};

export type InterventionSummary = {
  intervention_id: string;
  status?: string;
  [key: string]: unknown;
};

/** A persisted four-light ForecastOps alert (see GET /forecastops/alerts). */
export type ForecastAlert = {
  alert_id: string;
  store_id: string;
  alert_level: string;
  alert_reason_code: string;
  status: string;
  opened_at: string;
  acknowledged_by?: string | null;
  acknowledged_at?: string | null;
  acknowledgement_note?: string | null;
  [key: string]: unknown;
};

export type AdliftReport = {
  campaign_id?: string;
  [key: string]: unknown;
};

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

  listExternalDataFreshness(): Promise<ExternalDataFreshnessResponse> {
    return this.request<ExternalDataFreshnessResponse>("/external-data/freshness");
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

  listForecastAlerts(
    options: { level?: string } = {},
  ): Promise<ListResponse<ForecastAlert>> {
    return this.request<ListResponse<ForecastAlert>>("/forecastops/alerts", {
      query: { level: options.level },
    });
  }

  listAdliftReports(): Promise<ListResponse<AdliftReport>> {
    return this.request<ListResponse<AdliftReport>>("/adlift/reports");
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

  getOperatorBootstrap(): Promise<any> {
    return this.request("/api/v1/operator/bootstrap");
  }

  transitionOperatorIssue(
    issueId: string,
    actionType: string,
    payload: any,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<any> {
    return this.request(`/api/v1/operator/issues/${issueId}/${actionType}`, {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
      query: options.idempotencyKey ? { idempotency_key: options.idempotencyKey } : undefined,
    });
  }

  decideOperatorApproval(
    approvalId: string,
    payload: any,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<any> {
    return this.request(`/api/v1/operator/approvals/${approvalId}/decision`, {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
      query: options.idempotencyKey ? { idempotency_key: options.idempotencyKey } : undefined,
    });
  }

  confirmOperatorEvidencePurpose(
    evidenceId: string,
    payload: any,
    options: { correlationId?: string; idempotencyKey?: string } = {},
  ): Promise<any> {
    return this.request(`/api/v1/operator/evidence/${evidenceId}/purpose`, {
      method: "POST",
      body: payload,
      correlationId: options.correlationId,
      query: options.idempotencyKey ? { idempotency_key: options.idempotencyKey } : undefined,
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
