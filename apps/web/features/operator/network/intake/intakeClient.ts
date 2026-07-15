"use client";

// Assisted listing intake API binding (ODP-OC-R5-011).
//
// Owned layer  : the ONLY call path the intake UI uses. Every request goes
//                through the typed @oday-plus/openapi-client — no raw fetch,
//                no hardcoded auth headers. Identity comes from the console's
//                active role via operatorSecurityHeaders().
// Not changing : the backend routes, or the other network panels' legacy
//                fetch wiring (they are out of this task's scope).
// Composes with: AssistedIntakeQueuePanel + the four intake dialogs.

import {
  OdpApiError,
  createOdpApiClient,
  type AssistedIntake,
  type ConvertListingResponse,
  type IntakeCorrectPayload,
  type IntakeDecidePayload,
  type IntakeSubmitPayload,
  type OdpApiClient,
} from "@oday-plus/openapi-client";
import { operatorSecurityHeaders } from "../../operatorSecurityHeaders";

/**
 * Structured, renderable failure. `detail` is the server's own operator-facing
 * copy (zh-TW policy/permission text); we never overwrite it with an invented
 * message, and `correlationId` + `code` are surfaced so an operator can quote
 * them in a governance ticket (design §7 error requirements).
 */
export type IntakeApiError = {
  status: number;
  code: string;
  summary: string;
  nextAction: string;
  correlationId: string | null;
  occurredAt: string;
  retryable: boolean;
};

const ROLE_DENIED = "此角色無權執行本操作。請改由具備權限的角色（展店主管／選址審核／資料管理員）操作。";

/**
 * The intake UI runs in the browser, where the backend is reached same-origin
 * through the Next rewrite of `/api/v1/:path*` (apps/web/next.config.mjs) —
 * the client's paths already carry that prefix. An explicit
 * NEXT_PUBLIC_ODP_API_BASE_URL still wins when the API is served from another
 * origin. Note `createOdpApiClient` treats an empty baseUrl as "unconfigured"
 * and returns null, so same-origin must be passed as the concrete origin.
 */
export function buildIntakeClient(roleId?: string | null): OdpApiClient | null {
  const configured = process.env.NEXT_PUBLIC_ODP_API_BASE_URL?.trim();
  const baseUrl =
    configured || (typeof window !== "undefined" ? window.location.origin : undefined);
  if (!baseUrl) return null;

  return createOdpApiClient({
    baseUrl,
    defaultHeaders: operatorSecurityHeaders(roleId),
  });
}

/**
 * Map a thrown error onto the design's error contract: summary, next action,
 * error code, correlation ID and occurred time (design §7). Status drives the
 * next-action copy because the server's `detail` explains *what* was refused
 * but not *what the operator should do about it*.
 */
export function toIntakeApiError(error: unknown): IntakeApiError {
  const occurredAt = new Date().toISOString();

  if (error instanceof OdpApiError) {
    const detail = error.detail;
    const byStatus: Record<number, { code: string; next: string; retryable: boolean }> = {
      400: {
        code: "ODP-INTAKE-URL-INVALID",
        next: "請確認網址格式（需為 http(s):// 開頭的完整物件頁網址）後重新送出。",
        retryable: false,
      },
      403: {
        code: "ODP-INTAKE-FORBIDDEN",
        next: "請切換為具備權限的角色，或請展店主管代為決策。",
        retryable: false,
      },
      404: {
        code: "ODP-INTAKE-NOT-FOUND",
        next: "此收件紀錄已不存在，請回到收件佇列重新整理。",
        retryable: false,
      },
      409: {
        code: "ODP-INTAKE-CONFLICT",
        next: "此 URL 已在處理中，請開啟既有收件紀錄，不需重複送件。",
        retryable: false,
      },
      422: {
        code: "ODP-INTAKE-POLICY",
        next: "請依提示補齊必填的原因或欄位後再送出。",
        retryable: false,
      },
    };
    const mapped = byStatus[error.status];
    return {
      status: error.status,
      code: mapped?.code ?? `ODP-INTAKE-HTTP-${error.status}`,
      summary: detail ?? (error.status === 403 ? ROLE_DENIED : error.message),
      nextAction: mapped?.next ?? "請稍後重試；若持續發生，請附上 correlation ID 通報平台維運。",
      correlationId: error.correlationId ?? null,
      occurredAt,
      // 5xx is transient from the operator's point of view; 4xx is not.
      retryable: mapped ? mapped.retryable : error.status >= 500,
    };
  }

  // AbortError (client timeout) and network failures never reach the server.
  const isAbort = error instanceof Error && error.name === "AbortError";
  return {
    status: 0,
    code: isAbort ? "ODP-INTAKE-TIMEOUT" : "ODP-INTAKE-NETWORK",
    summary: isAbort
      ? "連線逾時 — 後端未在時限內回應，本次操作未寫入。"
      : "無法連線至後端服務，本次操作未寫入。",
    nextAction: "請確認網路連線後重試；你輸入的內容已保留。",
    correlationId: null,
    occurredAt,
    retryable: true,
  };
}

export function missingClientError(): IntakeApiError {
  return {
    status: 0,
    code: "ODP-INTAKE-UNCONFIGURED",
    summary: "尚未設定後端 API 位址（NEXT_PUBLIC_ODP_API_BASE_URL），收件功能無法使用。",
    nextAction: "請聯繫平台維運設定 API 位址；此畫面不會顯示模擬資料。",
    correlationId: null,
    occurredAt: new Date().toISOString(),
    retryable: false,
  };
}

export type IntakeResult<T> = { ok: true; value: T } | { ok: false; error: IntakeApiError };

async function guard<T>(run: () => Promise<T>): Promise<IntakeResult<T>> {
  try {
    return { ok: true, value: await run() };
  } catch (error) {
    return { ok: false, error: toIntakeApiError(error) };
  }
}

/**
 * Every write carries a correlation ID. The server persists whatever arrives in
 * X-Correlation-Id onto the intake record, and that value is what the detail
 * dialog shows as source evidence and what an error surfaces for a governance
 * ticket. Omitting it leaves the record's correlationId null, so it is
 * generated here rather than left to the caller to remember.
 */
export const intakeApi = {
  list(client: OdpApiClient, heatZoneId?: string): Promise<IntakeResult<AssistedIntake[]>> {
    return guard(() => client.listIntakes({ selectedHeatZoneId: heatZoneId }));
  },

  get(client: OdpApiClient, intakeId: string): Promise<IntakeResult<AssistedIntake>> {
    return guard(() => client.getIntake(intakeId));
  },

  /**
   * `idempotencyKey` is what makes a double-submit safe server-side; the UI
   * additionally disables the button while in flight, but the key is the
   * durable guarantee (a retried request must not create a second record).
   */
  submit(
    client: OdpApiClient,
    payload: IntakeSubmitPayload,
    options: { idempotencyKey: string; correlationId?: string },
  ): Promise<IntakeResult<AssistedIntake>> {
    return guard(() =>
      client.submitIntake(payload, {
        idempotencyKey: options.idempotencyKey,
        correlationId: options.correlationId ?? newCorrelationId(),
      }),
    );
  },

  correct(
    client: OdpApiClient,
    intakeId: string,
    payload: IntakeCorrectPayload,
  ): Promise<IntakeResult<AssistedIntake>> {
    return guard(() =>
      client.correctIntake(intakeId, payload, { correlationId: newCorrelationId() }),
    );
  },

  decide(
    client: OdpApiClient,
    intakeId: string,
    payload: IntakeDecidePayload,
  ): Promise<IntakeResult<AssistedIntake>> {
    return guard(() =>
      client.decideIntake(intakeId, payload, { correlationId: newCorrelationId() }),
    );
  },

  retry(
    client: OdpApiClient,
    intakeId: string,
    actorRoleId: string,
  ): Promise<IntakeResult<AssistedIntake>> {
    return guard(() =>
      client.retryIntake(intakeId, { actorRoleId }, { correlationId: newCorrelationId() }),
    );
  },

  /**
   * Promotion requires the caller to pass the risk summary it showed the
   * operator plus their acknowledgement; the server rejects (422) a missing or
   * unacknowledged summary rather than inventing one.
   */
  promote(
    client: OdpApiClient,
    intakeId: string,
    actorRoleId: string,
    reason: string,
    risk: { riskSummary: string; riskAcknowledged: boolean },
  ): Promise<IntakeResult<ConvertListingResponse>> {
    return guard(() =>
      client.promoteIntake(
        intakeId,
        { actorRoleId, reason, ...risk },
        { correlationId: newCorrelationId() },
      ),
    );
  },
};

export function newCorrelationId(): string {
  return `corr-${randomToken()}`;
}

/** Stable per-attempt key so a retry of the *same* submission dedups server-side. */
export function newIdempotencyKey(url: string): string {
  return `intake-${canonicalKeyPart(url)}-${randomToken()}`;
}

function randomToken(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);
}

function canonicalKeyPart(url: string): string {
  return url.replace(/^https?:\/\/(www\.)?/, "").replace(/[^a-zA-Z0-9]+/g, "-").slice(0, 40);
}
