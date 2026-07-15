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

import type { MergeListingResponse, OdpApiClient } from "@oday-plus/openapi-client";
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
};

export function toListingApiError(error: unknown): OperatorApiError {
  return toOperatorApiError(error, {
    byStatus: MERGE_ERRORS,
    fallbackPrefix: "ODP-LISTING-MERGE",
    roleDenied: ROLE_DENIED,
    timeoutSummary: "連線逾時 — 後端未在時限內回應，本次合併未寫入。",
    networkSummary: "無法連線至後端服務，本次合併未寫入。",
    transportNextAction: "請確認網路連線後重試；你輸入的原因已保留。",
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

export const listingsApi = {
  /**
   * `riskSummary` is the exact text the dialog rendered and `riskAcknowledged`
   * records that the operator accepted it; the server stores both on the audit
   * event and rejects (422) a missing or unacknowledged disclosure rather than
   * inventing one. The reason is the operator's own words — never a default.
   *
   * The idempotency key is per attempt, so a retried request cannot merge twice.
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
          },
          {
            correlationId: newCorrelationId(),
            idempotencyKey: `merge-${sourceListingId}-${input.targetListingId}-${randomToken()}`,
          },
        ),
      toListingApiError,
    );
  },
};
