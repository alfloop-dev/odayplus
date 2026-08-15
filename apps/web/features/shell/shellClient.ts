"use client";

/**
 * Browser transport for shell writes (ODP-PGAP-SHELL-001).
 *
 * Owned layer  : the shell's error vocabulary and idempotency-key discipline.
 * Not changing : the transport itself — buildOperatorNetworkClient /
 *                toOperatorApiError / guardCall are reused from the operator
 *                network surface so identity, correlation IDs and the error
 *                contract stay identical across the product.
 *
 * Error copy lives here rather than in the transport because the next action an
 * operator should take differs per surface: a refused assignment is a
 * permissions conversation, a refused report is a retry.
 */
import {
  buildOperatorNetworkClient,
  guardCall,
  newCorrelationId,
  randomToken,
  toOperatorApiError,
  type OperatorApiResult,
  type StatusErrorSpec,
} from "../operator/network/operatorNetworkClient";
import type { OdpApiClient } from "@oday-plus/openapi-client";

const SHELL_ERRORS: Record<number, StatusErrorSpec> = {
  401: {
    code: "SHELL-UNAUTHENTICATED",
    next: "請重新登入後再試。",
    retryable: false,
  },
  403: {
    code: "SHELL-FORBIDDEN",
    next: "此動作需要更高權限；請聯繫營運主管調整角色授權。",
    retryable: false,
  },
  404: {
    code: "SHELL-NOT-FOUND",
    next: "此項目已不存在或不在你的權限範圍內；請重新整理列表。",
    retryable: false,
  },
  409: {
    code: "SHELL-CONFLICT",
    next: "此變更與目前狀態衝突；請重新整理後確認最新狀態再試。",
    retryable: false,
  },
  422: {
    code: "SHELL-INVALID",
    next: "請依畫面提示修正輸入內容後再送出。",
    retryable: false,
  },
  503: {
    code: "SHELL-MAINTENANCE",
    next: "平台維護中，寫入已暫停；請稍後再試。",
    retryable: true,
  },
};

export function shellError(error: unknown) {
  return toOperatorApiError(error, {
    byStatus: SHELL_ERRORS,
    fallbackPrefix: "SHELL",
    roleDenied: "你的角色無法執行這個動作。",
    timeoutSummary: "後端未在時限內回應，這個動作沒有送出。",
    networkSummary: "目前無法連線到後端，這個動作沒有送出。",
    transportNextAction: "請確認網路連線後重試；沒有任何變更被寫入。",
  });
}

/**
 * Mint an idempotency key for one logical operation.
 *
 * Call this ONCE per operation and reuse it across retries — a per-attempt key
 * defeats the guarantee and lets a retried assignment apply twice. The scope
 * prefix keeps keys readable in the audit trail.
 */
export function newShellIdempotencyKey(scope: string, subject: string): string {
  return `shell-${scope}-${subject}-${randomToken()}`;
}

export function shellClient(roleId?: string | null): OdpApiClient | null {
  return buildOperatorNetworkClient(roleId);
}

/** The refusal shown when no API base URL is configured — never a fake success. */
export function missingShellClient(): OperatorApiResult<never> {
  return {
    ok: false,
    error: {
      status: 0,
      code: "SHELL-UNCONFIGURED",
      summary: "此環境未設定後端位址，動作無法送出。",
      nextAction: "請聯繫平台維運設定 API 位址；此畫面不會顯示模擬結果。",
      correlationId: null,
      occurredAt: new Date().toISOString(),
      retryable: false,
    },
  };
}

/**
 * Run one shell write. `idempotencyKey` is supplied by the caller so a retry of
 * the same logical operation reuses it; `correlationId` is minted per attempt
 * so each attempt is traceable on its own.
 */
export async function shellWrite<T>(
  roleId: string | null | undefined,
  run: (client: OdpApiClient, options: { correlationId: string; idempotencyKey: string }) => Promise<T>,
  idempotencyKey: string,
): Promise<OperatorApiResult<T>> {
  const client = shellClient(roleId);
  if (!client) return missingShellClient();
  return guardCall(
    () => run(client, { correlationId: newCorrelationId(), idempotencyKey }),
    shellError,
  );
}

export type { OperatorApiResult as ShellWriteResult };
