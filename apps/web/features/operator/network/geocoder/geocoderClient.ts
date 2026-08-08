"use client";

// Governed geocoder address search — provider port (ODP-CAP-GEOCODER-SEARCH-001).
//
// Owned layer  : the ONLY call path the geocoder search surface uses, its error
//                vocabulary, and the parser that decides which provider rows are
//                admissible as candidates.
// Not changing : the provider gateway's routes (services/provider-gateway
//                POST /geocode) or the map's boundary config, which reads the
//                same NEXT_PUBLIC_ODP_GEOCODER_URL for its own status line.
// Composes with: ../operatorNetworkClient's OperatorApiError contract, so a
//                geocoder failure renders through the same error envelope
//                (summary / next action / code / correlation ID) as every other
//                network surface.
//
// The production geocoder endpoint is not yet wired. That is handled by
// returning a structured ODP-GEOCODER-UNCONFIGURED error, NOT by falling back
// to fixtures: this surface must never present a coordinate it did not receive
// from a provider.
//
// THE INVARIANT: every failure path returns { ok: false, error }. There is no
// path through this module that produces a GeocodeCandidate with a coordinate
// that was defaulted, interpolated, or otherwise invented. A provider row that
// cannot supply two finite numbers is dropped and counted, never repaired.

import type { OperatorApiError, OperatorApiResult } from "../operatorNetworkClient";
import { newCorrelationId } from "../operatorNetworkClient";
import { normalizeAddress } from "./geocoderPolicy";
import type { GeocodeCandidate, GeocodeSearchResult } from "./geocoderTypes";

export type GeocoderApiError = OperatorApiError;
export type GeocoderResult<T> = OperatorApiResult<T>;

/** Client-side timeout. Longer than a healthy lookup, shorter than the UI's patience. */
const REQUEST_TIMEOUT_MS = 10_000;

const STATUS_ERRORS: Record<number, { code: string; next: string; retryable: boolean }> = {
  400: {
    code: "ODP-GEOCODER-QUERY-INVALID",
    next: "請輸入含縣市、路名與門牌的完整地址後重新搜尋。",
    retryable: false,
  },
  401: {
    code: "ODP-GEOCODER-UNAUTHORIZED",
    next: "地址服務憑證未生效，請洽平台維運；此畫面不會以估計座標代替。",
    retryable: false,
  },
  403: {
    code: "ODP-GEOCODER-FORBIDDEN",
    next: "你的角色不可使用地址搜尋，請改由展店角色操作。",
    retryable: false,
  },
  429: {
    code: "ODP-GEOCODER-RATE-LIMITED",
    next: "地址服務已達流量上限，請稍後再搜尋。",
    retryable: true,
  },
  502: {
    code: "ODP-GEOCODER-UPSTREAM",
    next: "上游地址服務回應異常，請稍後重試；必要時改用人工輸入座標並留下覆核理由。",
    retryable: true,
  },
  503: {
    code: "ODP-GEOCODER-UNAVAILABLE",
    next: "地址服務尚未設定或暫停服務，請洽平台維運。",
    retryable: true,
  },
  504: {
    code: "ODP-GEOCODER-UPSTREAM-TIMEOUT",
    next: "上游地址服務逾時，請稍後重試。",
    retryable: true,
  },
};

/** The configured geocoder endpoint, or null when it is absent or a mock. */
export function resolveGeocoderUrl(): string | null {
  const configured = (process.env.NEXT_PUBLIC_ODP_GEOCODER_URL ?? "").trim();
  if (!configured || configured.startsWith("mock://")) return null;
  return configured;
}

export function isGeocoderConfigured(): boolean {
  return resolveGeocoderUrl() !== null;
}

/**
 * The unconfigured state. This is an honest empty surface, not a degraded one:
 * the operator is told the lookup did not happen, so nothing on screen can be
 * mistaken for a provider answer.
 */
export function unconfiguredGeocoderError(): GeocoderApiError {
  return {
    status: 0,
    code: "ODP-GEOCODER-UNCONFIGURED",
    summary:
      "尚未設定地址定位服務（NEXT_PUBLIC_ODP_GEOCODER_URL），地址搜尋無法使用。",
    nextAction:
      "請聯繫平台維運設定地址服務位址。此畫面不會顯示模擬座標，也不會以行政區中心點代替實際定位。",
    correlationId: null,
    occurredAt: new Date().toISOString(),
    retryable: false,
  };
}

/**
 * Search the geocoder for an address.
 *
 * `deps.fetchImpl` exists so tests drive the parser and the error mapping
 * without a network or a global patch; production callers omit it.
 */
export async function searchAddress(
  query: string,
  deps: {
    fetchImpl?: typeof fetch;
    correlationId?: string;
    headers?: Record<string, string>;
    timeoutMs?: number;
  } = {},
): Promise<GeocoderResult<GeocodeSearchResult>> {
  const url = resolveGeocoderUrl();
  if (!url) return { ok: false, error: unconfiguredGeocoderError() };

  const correlationId = deps.correlationId ?? newCorrelationId();
  const fetchImpl = deps.fetchImpl ?? (typeof fetch !== "undefined" ? fetch : undefined);
  if (!fetchImpl) {
    return { ok: false, error: unconfiguredGeocoderError() };
  }

  const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timer = controller
    ? setTimeout(() => controller.abort(), deps.timeoutMs ?? REQUEST_TIMEOUT_MS)
    : null;

  try {
    const response = await fetchImpl(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        "X-Correlation-Id": correlationId,
        ...(deps.headers ?? {}),
      },
      body: JSON.stringify({ address: query }),
      signal: controller?.signal,
    });

    if (!response.ok) {
      return { ok: false, error: await toStatusError(response, correlationId) };
    }

    // A body that will not parse is a failure, not an empty result: "no
    // candidates" and "we could not read the answer" must not look alike.
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      return {
        ok: false,
        error: {
          status: response.status,
          code: "ODP-GEOCODER-MALFORMED",
          summary: "地址服務回應格式無法解析，本次搜尋沒有取得任何可用座標。",
          nextAction: "請重試一次；若持續發生，請附上 correlation ID 通報平台維運。",
          correlationId,
          occurredAt: new Date().toISOString(),
          retryable: true,
        },
      };
    }

    return { ok: true, value: parseSearchPayload(payload, query, correlationId) };
  } catch (error) {
    return { ok: false, error: toTransportError(error, correlationId) };
  } finally {
    if (timer !== null) clearTimeout(timer);
  }
}

/**
 * Parse a provider payload into candidates.
 *
 * Accepts both shapes on the wire: the gateway's current single-result
 * `{ result, request_id, observed_at }` and the multi-candidate
 * `{ results: [...] }` the production endpoint will return. Rows without two
 * finite coordinates are dropped and counted in `rejectedRowCount`.
 */
export function parseSearchPayload(
  payload: unknown,
  query: string,
  correlationId: string,
): GeocodeSearchResult {
  const searchedAt = new Date().toISOString();
  const normalizedQuery = normalizeAddress(query).normalized;
  const body = isRecord(payload) ? payload : {};

  const rows: unknown[] = Array.isArray(body.results)
    ? body.results
    : isRecord(body.result)
      ? [body.result]
      : [];

  const candidates: GeocodeCandidate[] = [];
  let rejectedRowCount = 0;

  rows.forEach((row, index) => {
    const candidate = toCandidate(row, {
      query,
      index,
      fallbackRequestId: readString(body.request_id) || correlationId,
      fallbackObservedAt: readString(body.observed_at) || searchedAt,
      fallbackProvider: readString(body.upstream),
    });
    if (candidate) candidates.push(candidate);
    else rejectedRowCount += 1;
  });

  return { query, normalizedQuery, candidates, correlationId, searchedAt, rejectedRowCount };
}

/**
 * Convert one provider row into a candidate, or null if it is not admissible.
 *
 * Coordinates are the hard gate. Everything else degrades to an empty string or
 * a value the policy will flag — but a row missing latitude or longitude, or
 * carrying a non-finite one, is never given a placeholder position.
 */
function toCandidate(
  row: unknown,
  context: {
    query: string;
    index: number;
    fallbackRequestId: string;
    fallbackObservedAt: string;
    fallbackProvider: string;
  },
): GeocodeCandidate | null {
  if (!isRecord(row)) return null;

  const latitude = readFiniteNumber(row.latitude ?? row.lat);
  const longitude = readFiniteNumber(row.longitude ?? row.lng ?? row.lon);
  if (latitude === null || longitude === null) return null;

  const providerRequestId =
    readString(row.provider_request_id) || readString(row.request_id) || context.fallbackRequestId;
  // Confidence is deliberately NOT defaulted to a passing value. An absent
  // confidence yields NaN, which the policy reads as below-threshold and routes
  // to human review — the opposite of assuming the provider was sure.
  const confidence = readFiniteNumber(row.confidence);

  return {
    candidateId: `${providerRequestId}#${context.index}`,
    addressRaw: context.query,
    formattedAddress:
      readString(row.formatted_address) || readString(row.normalized_address) || readString(row.address),
    latitude,
    longitude,
    precision: readString(row.precision) || readString(row.geocode_precision),
    confidence: confidence === null ? Number.NaN : confidence,
    provider: readString(row.provider_id) || readString(row.provider) || context.fallbackProvider,
    providerRequestId,
    adminCity: readString(row.city) || readString(row.admin_city),
    adminDistrict: readString(row.district) || readString(row.admin_district),
    observedAt: readString(row.observed_at) || context.fallbackObservedAt,
  };
}

async function toStatusError(response: Response, correlationId: string): Promise<GeocoderApiError> {
  const mapped = STATUS_ERRORS[response.status];
  // The server's own copy is preferred over anything invented here; `detail` is
  // the FastAPI convention the gateway and API both use.
  let detail = "";
  try {
    const body: unknown = await response.json();
    if (isRecord(body)) detail = readString(body.detail) || readString(body.message);
  } catch {
    detail = "";
  }

  return {
    status: response.status,
    code: mapped?.code ?? `ODP-GEOCODER-HTTP-${response.status}`,
    summary: detail || `地址服務回應 ${response.status}，本次搜尋沒有取得任何可用座標。`,
    nextAction: mapped?.next ?? "請稍後重試；若持續發生，請附上 correlation ID 通報平台維運。",
    correlationId: response.headers?.get?.("X-Correlation-Id") ?? correlationId,
    occurredAt: new Date().toISOString(),
    retryable: mapped ? mapped.retryable : response.status >= 500,
  };
}

function toTransportError(error: unknown, correlationId: string): GeocoderApiError {
  const isAbort = error instanceof Error && error.name === "AbortError";
  return {
    status: 0,
    code: isAbort ? "ODP-GEOCODER-TIMEOUT" : "ODP-GEOCODER-NETWORK",
    summary: isAbort
      ? "地址服務逾時未回應，本次搜尋沒有取得任何可用座標。"
      : "無法連線至地址服務，本次搜尋沒有取得任何可用座標。",
    nextAction: "請確認網路連線後重試；你輸入的地址已保留，畫面不會顯示推估位置。",
    correlationId,
    occurredAt: new Date().toISOString(),
    retryable: true,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/** Numbers only — a numeric *string* is a provider contract violation, not a coordinate. */
function readFiniteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
