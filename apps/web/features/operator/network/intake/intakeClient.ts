"use client";

// Assisted listing intake API binding (ODP-OC-R5-011).
//
// Owned layer  : the intake surface's error vocabulary, and the ONLY call path
//                the intake UI uses. Every request goes through the typed
//                @oday-plus/openapi-client — no raw fetch, no hardcoded auth
//                headers. Transport (client construction, correlation IDs, the
//                status→error mapping) is shared with the other network
//                surfaces; see ../operatorNetworkClient.
// Not changing : the backend routes, or the network panels that still use raw
//                fetch for read-only snapshots. Listing Radar's merge — the one
//                high-impact write among them — is typed too, via
//                ../listingsClient.
// Composes with: AssistedIntakeQueuePanel + the four intake dialogs.

import {
  type AssistedIntake,
  type ConvertListingResponse,
  type IntakeCorrectPayload,
  type IntakeDecidePayload,
  type IntakeInboxPage,
  type IntakeSubmitPayload,
  type JobReceipt,
  type OdpApiClient,
  type AssignmentReceipt,
  type AssignmentTransferRequest,
  type AssignmentRequest,
  type PromotionDecisionReceipt,
  type PromotionRequest,
  type ReasonCommand,
  type RetryRequest,
  type ReviewDecisionRequest,
  type SlaPauseRequest,
  type SlaReceipt,
} from "@oday-plus/openapi-client";
import {
  buildOperatorNetworkClient,
  guardCall,
  newCorrelationId,
  randomToken,
  toOperatorApiError,
  type OperatorApiError,
  type OperatorApiResult,
  type StatusErrorSpec,
} from "../operatorNetworkClient";

/** The shared network error contract, named for this surface's callers. */
export type IntakeApiError = OperatorApiError;

const ROLE_DENIED = "此角色無權執行本操作。請改由具備權限的角色（展店主管／選址審核／資料管理員）操作。";

/** Transport is shared with the other network surfaces; see operatorNetworkClient. */
export const buildIntakeClient = buildOperatorNetworkClient;

/** Intake-specific next actions layered on the shared status→error mapping. */
const INTAKE_ERRORS: Record<number, StatusErrorSpec> = {
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

/**
 * Promotion-saga error vocabulary (v1 promotion routes). The 409 here is a
 * concurrency/duplicate-candidate conflict, NOT the submit surface's
 * "URL already processing" 409, so it carries its own next action; 428 means
 * the mandatory If-Match header was missing or malformed.
 */
const PROMOTION_ERRORS: Record<number, StatusErrorSpec> = {
  403: {
    code: "ODP-PROMOTION-FORBIDDEN",
    next: "晉升申請/審查需要展店角色授權；提案者不得自行核准（SELF_REVIEW_DENIED）。",
    retryable: false,
  },
  404: {
    code: "ODP-PROMOTION-NOT-FOUND",
    next: "找不到收件或晉升決策，請重新整理後再試。",
    retryable: false,
  },
  409: {
    code: "ODP-PROMOTION-CONFLICT",
    next: "版本衝突或已有重複 Candidate。請重新整理取得最新 If-Match 後，以同一 Idempotency-Key 重試。",
    retryable: true,
  },
  422: {
    code: "ODP-PROMOTION-POLICY",
    next: "請依提示補齊原因、風險確認與 gate snapshot 後再送出。",
    retryable: false,
  },
  428: {
    code: "ODP-PROMOTION-PRECONDITION",
    next: "此操作必須攜帶 If-Match 版本，請重新整理後重試。",
    retryable: true,
  },
};

export function toPromotionApiError(error: unknown): IntakeApiError {
  return toOperatorApiError(error, {
    byStatus: PROMOTION_ERRORS,
    fallbackPrefix: "ODP-PROMOTION",
    roleDenied: ROLE_DENIED,
    timeoutSummary: "連線逾時 — 晉升操作結果未確認，可以同一 Idempotency-Key 重試或查詢決策狀態。",
    networkSummary: "無法連線至後端服務 — 晉升操作結果未確認。",
    transportNextAction: "以同一 Idempotency-Key 重試只會取回原收據；也可查詢決策狀態（不重送）。",
  });
}

export function toIntakeApiError(error: unknown): IntakeApiError {
  return toOperatorApiError(error, {
    byStatus: INTAKE_ERRORS,
    fallbackPrefix: "ODP-INTAKE",
    roleDenied: ROLE_DENIED,
    timeoutSummary: "連線逾時 — 後端未在時限內回應，本次操作未寫入。",
    networkSummary: "無法連線至後端服務，本次操作未寫入。",
    transportNextAction: "請確認網路連線後重試；你輸入的內容已保留。",
  });
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

export type IntakeResult<T> = OperatorApiResult<T>;
type IntakeWriteOptions = { idempotencyKey: string; correlationId?: string; ifMatch?: string };

function guard<T>(run: () => Promise<T>): Promise<IntakeResult<T>> {
  return guardCall(run, toIntakeApiError);
}

function guardPromotion<T>(run: () => Promise<T>): Promise<IntakeResult<T>> {
  return guardCall(run, toPromotionApiError);
}

/** Server receipt plus whether it was answered from a prior durable receipt. */
export type PromotionWriteResult = {
  receipt: PromotionDecisionReceipt;
  idempotencyReplayed: boolean;
};

export type ScoreJobWriteResult = {
  receipt: JobReceipt;
  idempotencyReplayed: boolean;
};

/**
 * Every write carries a correlation ID. The server persists whatever arrives in
 * X-Correlation-Id onto the intake record, and that value is what the detail
 * dialog shows as source evidence and what an error surfaces for a governance
 * ticket. Omitting it leaves the record's correlationId null, so it is
 * generated here rather than left to the caller to remember.
 */
export const intakeApi = {
  list(client: OdpApiClient, query: Parameters<OdpApiClient["listIntakes"]>[0]): Promise<IntakeResult<IntakeInboxPage>> {
    return guard(() => client.listIntakes(query));
  },

  get(client: OdpApiClient, intakeId: string): Promise<IntakeResult<AssistedIntake>> {
    return guard(() => client.getIntake(intakeId));
  },

  getScoreJob(client: OdpApiClient, jobId: string): Promise<IntakeResult<JobReceipt>> {
    return guardPromotion(() => client.getJobReceipt(jobId));
  },

  getPromotionForIntake(
    client: OdpApiClient,
    intakeId: string,
  ): Promise<IntakeResult<PromotionDecisionReceipt>> {
    return guardPromotion(() => client.getIntakePromotionDecision(intakeId));
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
    options: IntakeWriteOptions,
  ): Promise<IntakeResult<AssistedIntake>> {
    return guard(() =>
      client.correctIntake(intakeId, payload, {
        correlationId: options.correlationId ?? newCorrelationId(),
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      }),
    );
  },

  decide(
    client: OdpApiClient,
    intakeId: string,
    payload: IntakeDecidePayload,
    options: IntakeWriteOptions,
  ): Promise<IntakeResult<AssistedIntake>> {
    return guard(() =>
      client.decideIntake(intakeId, payload, {
        correlationId: options.correlationId ?? newCorrelationId(),
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      }),
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
    options: IntakeWriteOptions,
  ): Promise<IntakeResult<ConvertListingResponse>> {
    return guard(() =>
      client.promoteIntake(
        intakeId,
        { actorRoleId, reason, ...risk },
        {
          correlationId: options.correlationId ?? newCorrelationId(),
          idempotencyKey: options.idempotencyKey,
        },
      ),
    );
  },

  claimAssignment(
    client: OdpApiClient,
    assignmentId: string,
    payload: ReasonCommand,
    options: IntakeWriteOptions,
  ): Promise<IntakeResult<AssignmentReceipt>> {
    return guard(() =>
      client.claimAssignment(assignmentId, payload, {
        correlationId: options.correlationId ?? newCorrelationId(),
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      }),
    );
  },

  transferAssignment(
    client: OdpApiClient,
    assignmentId: string,
    payload: AssignmentTransferRequest,
    options: IntakeWriteOptions,
  ): Promise<IntakeResult<AssignmentReceipt>> {
    return guard(() =>
      client.transferAssignment(assignmentId, payload, {
        correlationId: options.correlationId ?? newCorrelationId(),
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      }),
    );
  },

  completeAssignment(
    client: OdpApiClient,
    assignmentId: string,
    payload: ReasonCommand,
    options: IntakeWriteOptions,
  ): Promise<IntakeResult<AssignmentReceipt>> {
    return guard(() =>
      client.completeAssignment(assignmentId, payload, {
        correlationId: options.correlationId ?? newCorrelationId(),
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      }),
    );
  },

  pauseSla(
    client: OdpApiClient,
    slaInstanceId: string,
    payload: SlaPauseRequest,
    options: IntakeWriteOptions,
  ): Promise<IntakeResult<SlaReceipt>> {
    return guard(() =>
      client.pauseSla(slaInstanceId, payload, {
        correlationId: options.correlationId ?? newCorrelationId(),
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      }),
    );
  },

  resumeSla(
    client: OdpApiClient,
    slaInstanceId: string,
    payload: ReasonCommand,
    options: IntakeWriteOptions,
  ): Promise<IntakeResult<SlaReceipt>> {
    return guard(() =>
      client.resumeSla(slaInstanceId, payload, {
        correlationId: options.correlationId ?? newCorrelationId(),
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      }),
    );
  },

  assign(
    client: OdpApiClient,
    intakeId: string,
    payload: AssignmentRequest,
    options: IntakeWriteOptions,
  ): Promise<IntakeResult<AssignmentReceipt>> {
    return guard(() =>
      client.assignIntake(intakeId, payload, {
        correlationId: options.correlationId ?? newCorrelationId(),
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      }),
    );
  },

  // ---- Candidate promotion saga (ODP-INTAKE-UX-PROMOTION-001) ------------
  // v1 contract routes. If-Match is REQUIRED on every write (the server
  // answers 428 without it), so these wrappers take it as a non-optional
  // field instead of inheriting IntakeWriteOptions' optional one.

  /** POST /api/v1/intakes/{id}/promotion-requests — explicit promotion request. */
  requestPromotion(
    client: OdpApiClient,
    intakeId: string,
    payload: PromotionRequest,
    options: { idempotencyKey: string; ifMatch: string; correlationId?: string },
  ): Promise<IntakeResult<PromotionWriteResult>> {
    return guardPromotion(() =>
      client.requestCandidatePromotion(intakeId, payload, {
        correlationId: options.correlationId ?? newCorrelationId(),
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      }),
    );
  },

  /** POST /api/v1/promotion-decisions/{id}/actions/review — second-actor review. */
  reviewPromotion(
    client: OdpApiClient,
    promotionDecisionId: string,
    payload: ReviewDecisionRequest,
    options: { idempotencyKey: string; ifMatch: string; correlationId?: string },
  ): Promise<IntakeResult<PromotionWriteResult>> {
    return guardPromotion(() =>
      client.reviewPromotionDecision(promotionDecisionId, payload, {
        correlationId: options.correlationId ?? newCorrelationId(),
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      }),
    );
  },

  /** GET /api/v1/promotion-decisions/{id} — lost-response recovery (no resend). */
  getPromotionDecision(
    client: OdpApiClient,
    promotionDecisionId: string,
  ): Promise<IntakeResult<PromotionDecisionReceipt>> {
    return guardPromotion(() =>
      client.getPromotionDecision(promotionDecisionId, { correlationId: newCorrelationId() }),
    );
  },

  /** POST /api/v1/jobs/{id}/retry — authorized replay from a durable checkpoint. */
  retryScoreJob(
    client: OdpApiClient,
    jobId: string,
    payload: RetryRequest,
    options: { idempotencyKey: string; ifMatch: string; correlationId?: string },
  ): Promise<IntakeResult<ScoreJobWriteResult>> {
    return guardPromotion(() =>
      client.retryJob(jobId, payload, {
        correlationId: options.correlationId ?? newCorrelationId(),
        idempotencyKey: options.idempotencyKey,
        ifMatch: options.ifMatch,
      }),
    );
  },
};

export { newCorrelationId };

/** Stable per-submission key so a retry of the *same* submission dedups server-side. */
export function newIdempotencyKey(url: string): string {
  return `intake-${canonicalKeyPart(url)}-${randomToken()}`;
}

/** A key identifying one high-impact intake write, stable across retries. */
export function newIntakeActionIdempotencyKey(
  intakeId: string,
  action: string,
  detail?: string,
): string {
  const suffix = detail ? `-${canonicalKeyPart(detail)}` : "";
  return (
    `intake-${canonicalKeyPart(action)}-${canonicalKeyPart(intakeId)}` +
    `${suffix}-${randomToken()}`
  );
}

function canonicalKeyPart(url: string): string {
  return (
    url
      .replace(/^https?:\/\/(www\.)?/, "")
      .replace(/[^a-zA-Z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 40) || "item"
  );
}
