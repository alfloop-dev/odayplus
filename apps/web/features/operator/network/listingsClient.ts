"use client";

// Listing Radar write bindings (ODP-OC-R5-011).
//
// Owned layer  : the merge call path. Merge is a high-impact, irreversible
//                write, so it goes through the typed OpenAPI client with a
//                correlation ID and an idempotency key, and its failures are
//                rendered as structured errors rather than a status code.
// Not changing : the read-only snapshot fetches in NetworkFindAreasWorkspace.
// Composes with: ListingMergeDialog (collects reason + risk acknowledgement)
//                and NetworkListingService.merge_listing on the server.

import type { ArchiveListingResponse, MergeListingResponse, OdpApiClient } from "@oday-plus/openapi-client";
import {
  buildOperatorNetworkClient,
  guardCall,
  newCorrelationId,
  randomToken,
  toOperatorApiError,
  type OperatorApiError,
  type OperatorApiResult,
  type StatusErrorSpec,
} from "./operatorNetworkClient";

export { buildOperatorNetworkClient as buildListingsClient };
export type { OperatorApiError as ListingApiError };

const ROLE_DENIED =
  "此角色無權執行合併。請改由具備權限的角色（展店主管／選址審核／資料管理員）操作。";

/**
 * Merge-specific next actions. 422 is the server's risk/reason gate: it fires
 * when the disclosure the operator acknowledged is missing or the reason is
 * blank, so the copy points back at the form rather than suggesting a retry.
 */
const MERGE_ERRORS: Record<number, StatusErrorSpec> = {
  403: {
    code: "ODP-LISTING-MERGE-FORBIDDEN",
    next: "請切換為具備權限的角色，或請展店主管代為執行合併。",
    retryable: false,
  },
  404: {
    code: "ODP-LISTING-MERGE-NOT-FOUND",
    next: "來源或目標物件已不存在，請回到 Listing Radar 重新整理後確認。",
    retryable: false,
  },
  409: {
    code: "ODP-LISTING-MERGE-CONFLICT",
    next: "此物件狀態已變更（可能已被合併或封存），請重新整理後再確認。",
    retryable: false,
  },
  422: {
    code: "ODP-LISTING-MERGE-POLICY",
    next: "請填寫合併原因並勾選風險確認後再送出。",
    retryable: false,
  },
  428: { code: "ODP-LISTING-PRECONDITION-REQUIRED", next: "請重新載入目前物件版本後再確認。", retryable: false },
  503: { code: "ODP-LISTING-VERSION-UNAVAILABLE", next: "目前沒有可驗證的物件版本，請聯繫維運確認版本來源。", retryable: false },
};

export function toListingApiError(error: unknown): OperatorApiError {
  return toOperatorApiError(error, {
    byStatus: MERGE_ERRORS,
    fallbackPrefix: "ODP-LISTING-MERGE",
    roleDenied: ROLE_DENIED,
    timeoutSummary: "連線逾時 — 尚無法確認本次操作是否完成。",
    networkSummary: "連線中斷 — 尚無法確認本次操作是否完成。",
    transportNextAction: "請保留此確認視窗並以同一操作重試；原因與請求識別已保留。",
  });
}

export function missingListingsClientError(): OperatorApiError {
  return {
    status: 0,
    code: "ODP-LISTING-UNCONFIGURED",
    summary: "尚未設定後端 API 位址（NEXT_PUBLIC_ODP_API_BASE_URL），合併功能無法使用。",
    nextAction: "請聯繫平台維運設定 API 位址；此畫面不會顯示模擬結果。",
    correlationId: null,
    occurredAt: new Date().toISOString(),
    retryable: false,
  };
}

/**
 * A key identifying one logical merge, stable across retries.
 *
 * This is the whole point of the key: if a response is lost in transit and the
 * operator retries, the server must recognise the SAME operation and replay its
 * original result instead of merging again. A key minted per attempt would look
 * like a fresh operation every time and defeat the guarantee. So the key is
 * created once when the merge is initiated and retained until it succeeds, is
 * cancelled, or is replaced by a different merge request.
 */
export function newMergeIdempotencyKey(
  sourceListingId: string,
  targetListingId: string,
): string {
  return `merge-${sourceListingId}-${targetListingId}-${randomToken()}`;
}

export function newArchiveIdempotencyKey(listingId: string): string {
  return `archive-${listingId}-${randomToken()}`;
}

export const listingsApi = {
  /**
   * `riskSummary` is the exact text the dialog rendered and `riskAcknowledged`
   * records that the operator accepted it; the server stores both on the audit
   * event and rejects (422) a missing or unacknowledged disclosure rather than
   * inventing one. The reason is the operator's own words — never a default.
   *
   * `idempotencyKey` is supplied by the caller and must be the SAME value for
   * every retry of one logical merge — see newMergeIdempotencyKey. The
   * correlation ID is per attempt by design: each attempt is a distinct
   * request to trace, even though they share one operation identity.
   */
  merge(
    client: OdpApiClient,
    sourceListingId: string,
    input: {
      targetListingId: string;
      actorRoleId: string;
      reason: string;
      riskSummary: string;
      riskAcknowledged: boolean;
      idempotencyKey: string;
      ifMatch?: string;
      targetIfMatch?: string;
      ifActorMatch?: string;
    },
  ): Promise<OperatorApiResult<MergeListingResponse>> {
    return guardCall(
      () =>
        client.mergeListing(
          sourceListingId,
          {
            targetListingId: input.targetListingId,
            actorRoleId: input.actorRoleId,
            reason: input.reason,
            riskSummary: input.riskSummary,
            riskAcknowledged: input.riskAcknowledged,
            targetIfMatch: input.targetIfMatch,
            expectedActorSubjectId: input.ifActorMatch,
          },
          {
            correlationId: newCorrelationId(),
            idempotencyKey: input.idempotencyKey,
            ifMatch: input.ifMatch,
            ifActorMatch: input.ifActorMatch,
          },
        ),
      toListingApiError,
    );
  },
  archive(client: OdpApiClient, listingId: string, input: {
    actorRoleId: string; reason: string; idempotencyKey: string; ifMatch?: string; ifActorMatch?: string;
  }): Promise<OperatorApiResult<ArchiveListingResponse>> {
    return guardCall(() => client.archiveListing(listingId, { actorRoleId: input.actorRoleId, reason: input.reason,
      expectedActorSubjectId: input.ifActorMatch }, {
      correlationId: newCorrelationId(), idempotencyKey: input.idempotencyKey, ifMatch: input.ifMatch, ifActorMatch: input.ifActorMatch,
    }), toListingApiError);
  },
};
