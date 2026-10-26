export type OperatorDataAvailability =
  | "loading"
  | "ready"
  | "error"
  | "seed"
  | "empty"
  | "fixture";

type RuntimeEnvironment = {
  deployEnv?: string;
  e2eMode?: string;
  nodeEnv?: string;
  productMode?: string;
  productionMode?: string;
  requireLiveData?: string;
};

type ShellInspection = {
  status: Extract<OperatorDataAvailability, "ready" | "seed" | "empty">;
  source?: string;
};

const KNOWN_SEED_SOURCES = new Set([
  "operator-shell-api-envelope",
  "r4",
  "seed-r4",
]);

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : null;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function isOperatorProductionMode(
  environment: RuntimeEnvironment = {
    deployEnv:
      process.env.ODP_DEPLOY_ENV ??
      process.env.ODAY_ENV ??
      process.env.ODP_ENV ??
      process.env.NEXT_PUBLIC_ODP_DEPLOY_ENV,
    e2eMode:
      process.env.ODP_E2E_MODE ??
      process.env.NEXT_PUBLIC_ODP_E2E_MODE,
    nodeEnv: process.env.NODE_ENV,
    productMode:
      process.env.ODP_PRODUCT_MODE ??
      process.env.NEXT_PUBLIC_ODP_PRODUCT_MODE,
    productionMode: process.env.NEXT_PUBLIC_PRODUCTION_MODE,
    requireLiveData: process.env.ODP_REQUIRE_LIVE_DATA,
  },
): boolean {
  return isProductionMode({
    NODE_ENV: environment.nodeEnv,
    ODP_DEPLOY_ENV: environment.deployEnv,
    ODP_E2E_MODE: environment.e2eMode,
    ODP_PRODUCT_MODE: environment.productMode,
    ODP_REQUIRE_LIVE_DATA: environment.requireLiveData,
    NEXT_PUBLIC_PRODUCTION_MODE: environment.productionMode,
  });
}

export function operatorFixturesAllowed(environment?: RuntimeEnvironment): boolean {
  return !isOperatorProductionMode(environment);
}

export function isSeedDataSource(source: unknown): boolean {
  return (
    (typeof source === "string" &&
      KNOWN_SEED_SOURCES.has(source.trim().toLowerCase())) ||
    isNonProductionDataSource(source)
  );
}

export function payloadContainsSeedData(
  value: unknown,
  _visited: WeakSet<object> = new WeakSet<object>(),
): boolean {
  return payloadContainsNonProductionData(value);
}

function hasText(record: Record<string, unknown> | null, key: string): boolean {
  return typeof record?.[key] === "string" && record[key].trim().length > 0;
}

export function inspectOperatorShellPayload(payload: unknown): ShellInspection {
  const root = asRecord(payload);
  if (!root) return { status: "empty" };

  const meta = asRecord(root.meta);
  const source = typeof meta?.source === "string" ? meta.source : undefined;
  const dataMode =
    typeof meta?.dataMode === "string" ? meta.dataMode.trim().toLowerCase() : undefined;
  const dataOrigin = asRecord(meta?.dataOrigin);
  const originKind =
    typeof dataOrigin?.kind === "string" ? dataOrigin.kind.trim().toLowerCase() : undefined;
  const liveReadiness = asRecord(meta?.liveReadiness);
  const liveReady = liveReadiness?.ready === true;
  const fixtureMeta = asRecord(root._meta);
  const fixtureDescription =
    typeof fixtureMeta?.description === "string" ? fixtureMeta.description : undefined;

  if (
    payloadContainsSeedData(payload) ||
    dataMode === "fixture" ||
    originKind === "fixture" ||
    isSeedDataSource(fixtureDescription)
  ) {
    return { status: "seed", source };
  }
  if (dataMode === "unavailable" || originKind === "unavailable") {
    return { status: "empty", source };
  }

  const explicitlyLive =
    dataMode === "live" ||
    dataMode === "production" ||
    originKind === "live" ||
    originKind === "production" ||
    liveReady ||
    source?.toLowerCase().includes("live") ||
    source?.toLowerCase().includes("production");

  const navigation = asRecord(root.navigation);
  const today = asRecord(root.today);
  const hero = asRecord(today?.hero);
  const role = asRecord(meta?.role);
  const search = asRecord(root.search);
  const hasNavigation =
    asArray(navigation?.workspaces).length > 0 &&
    asArray(navigation?.allowedWorkspaces).length > 0;
  const hasRole =
    hasText(role, "id") &&
    hasText(role, "label") &&
    asArray(role?.allowedWorkspaces).length > 0;
  const hasHero =
    hasText(hero, "name") &&
    hasText(hero, "roleLabel") &&
    hasText(hero, "scope") &&
    hasText(hero, "dateLabel");
  const hasOperationalRows = [
    today?.kpis,
    today?.queue,
    today?.decisions,
    today?.riskRows,
    today?.auditFeed,
    root.approvals,
    root.notifications,
    root.workQueue,
    search?.items,
  ].some((value) => asArray(value).length > 0);

  if (
    !source ||
    !explicitlyLive ||
    !hasNavigation ||
    !hasRole ||
    !hasHero ||
    !hasOperationalRows
  ) {
    return { status: "empty", source };
  }

  return { status: "ready", source };
}

/**
 * Why a data read failed, in the terms an operator can act on. The raw
 * exception text stays technical detail; it is never the headline.
 */
export type OperatorLoadFailureKind =
  | "timeout"
  | "network"
  | "server"
  | "forbidden"
  | "unauthenticated"
  | "unknown";

export type OperatorLoadFailure = {
  correlationId?: string;
  httpStatus?: number;
  kind: OperatorLoadFailureKind;
  occurredAt?: string;
  technicalDetail?: string;
};

export type UnavailableDataMessage = {
  /** Short human status for the badge. */
  badge: string;
  /** Machine code for support and logs; shown only in technical details. */
  code: string;
  detail: string;
  /** What the operator can do next. */
  next: string;
  title: string;
};

const NO_SUBSTITUTE_DATA = "上方導覽仍可使用；系統不會以示範資料代替正式資料。";

export function classifyLoadFailure(
  error: unknown,
  httpStatus?: number,
): OperatorLoadFailureKind {
  if (httpStatus === 401) return "unauthenticated";
  if (httpStatus === 403) return "forbidden";
  if (httpStatus === 408 || httpStatus === 504) return "timeout";
  if (httpStatus !== undefined && httpStatus >= 500) return "server";

  const name = error instanceof Error ? error.name : "";
  const text = (error instanceof Error ? error.message : typeof error === "string" ? error : "")
    .toLowerCase();
  if (name === "TimeoutError" || /timed? ?out|timeout|\b504\b/.test(text)) return "timeout";
  if (/\b401\b|unauthori[sz]ed|unauthenticated|session.required/.test(text)) return "unauthenticated";
  if (/\b403\b|forbidden|沒有.*權限/.test(text)) return "forbidden";
  if (name === "TypeError" || /failed to fetch|network|offline|econn/.test(text)) return "network";
  if (/\b5\d\d\b/.test(text)) return "server";
  return "unknown";
}

/**
 * Build the failure from a refused HTTP read. The status and the canonical
 * error envelope (`error.code`, `error.correlation_id`, see shared/api/errors.py)
 * decide the kind, so a received 403 is an authenticated denial and never a
 * transport failure, whatever words the endpoint path happens to contain.
 */
export async function operatorLoadFailureFromResponse(
  response: Response,
  label: string,
  requestCorrelationId?: string,
): Promise<OperatorLoadFailure> {
  let code: string | undefined;
  let correlationId: string | undefined;
  let message: string | undefined;
  try {
    const body = asRecord(await response.clone().json());
    const envelope = asRecord(body?.error);
    code = typeof envelope?.code === "string" ? envelope.code : undefined;
    correlationId =
      typeof envelope?.correlation_id === "string" ? envelope.correlation_id : undefined;
    message =
      typeof envelope?.message === "string"
        ? envelope.message
        : typeof body?.detail === "string"
          ? body.detail
          : undefined;
  } catch {
    // A non-JSON refusal still has its status; the kind comes from that.
  }
  const httpStatus = response.status;
  const kind =
    code === "forbidden"
      ? "forbidden"
      : code === "unauthorized"
        ? "unauthenticated"
        : classifyLoadFailure(undefined, httpStatus);
  return {
    correlationId:
      correlationId ??
      response.headers?.get?.("x-correlation-id") ??
      requestCorrelationId ??
      undefined,
    httpStatus,
    kind,
    occurredAt: new Date().toISOString(),
    technicalDetail: `${label} returned ${httpStatus}${code ? ` ${code}` : ""}${message ? `: ${message}` : ""}`,
  };
}

/** Build the failure for a read that never received an HTTP response. */
export function operatorLoadFailureFromError(
  error: unknown,
  label: string,
  requestCorrelationId?: string,
): OperatorLoadFailure {
  const text = error instanceof Error ? error.message : String(error ?? "request failed");
  return {
    correlationId: requestCorrelationId,
    kind: classifyLoadFailure(error),
    occurredAt: new Date().toISOString(),
    technicalDetail: `${label} request failed: ${text}`,
  };
}

/** Thrown by readers that must surface the typed failure to their caller. */
export class OperatorLoadFailureError extends Error {
  readonly failure: OperatorLoadFailure;

  constructor(failure: OperatorLoadFailure) {
    super(failure.technicalDetail ?? failure.kind);
    this.name = "OperatorLoadFailureError";
    this.failure = failure;
  }
}

/** Short label for a count whose collection could not be read. */
export function unreadCountLabel(kind: OperatorLoadFailureKind | undefined): string {
  if (kind === "forbidden") return "未授權";
  if (kind === "unauthenticated") return "未登入";
  return "無法取得";
}

function failureMessage(kind: OperatorLoadFailureKind): UnavailableDataMessage {
  switch (kind) {
    case "timeout":
      return {
        badge: "回應逾時",
        code: "OPERATOR_DATA_TIMEOUT",
        detail: `營運資料服務未在時限內回應，通常是暫時性的延遲。${NO_SUBSTITUTE_DATA}`,
        next: "請按「重新載入」再試一次；若持續發生，請將下方追蹤編號提供給維運人員。",
        title: "營運資料回應逾時",
      };
    case "network":
      return {
        badge: "連線失敗",
        code: "OPERATOR_DATA_NETWORK",
        detail: `瀏覽器無法連到營運資料服務。${NO_SUBSTITUTE_DATA}`,
        next: "請確認網路連線後按「重新載入」；若其他人也無法使用，請通知維運人員。",
        title: "無法連線到營運資料服務",
      };
    case "server":
      return {
        badge: "服務錯誤",
        code: "OPERATOR_DATA_SERVER_ERROR",
        detail: `營運資料服務回報錯誤。${NO_SUBSTITUTE_DATA}`,
        next: "請稍後按「重新載入」；若持續發生，請將下方追蹤編號提供給維運人員。",
        title: "營運資料服務發生錯誤",
      };
    case "forbidden":
      return {
        badge: "沒有權限",
        code: "OPERATOR_DATA_FORBIDDEN",
        detail: "伺服器已確認登入身分，但此帳號沒有這項營運資料的讀取權限（例如僅具平台管理員或稽核角色）。這不是連線問題，也不代表資料為空。",
        next: "使用者與角色管理請使用「管理後台」；需要營運資料權限請洽系統管理員。",
        title: "此帳號沒有營運資料讀取權限",
      };
    case "unauthenticated":
      return {
        badge: "登入已過期",
        code: "OPERATOR_SESSION_EXPIRED",
        detail: "此頁面的登入狀態已失效（可能已逾時或在其他地方登出）。",
        next: "請重新登入，登入後會回到目前的頁面。",
        title: "登入已過期",
      };
    default:
      return {
        badge: "無法取得",
        code: "OPERATOR_DATA_UNAVAILABLE",
        detail: `營運資料暫時無法讀取。${NO_SUBSTITUTE_DATA}`,
        next: "請按「重新載入」再試一次；若持續發生，請將下方追蹤編號提供給維運人員。",
        title: "營運資料暫時無法取得",
      };
  }
}

export function unavailableDataMessage(
  status: OperatorDataAvailability,
  failureKind?: OperatorLoadFailureKind,
): UnavailableDataMessage {
  switch (status) {
    case "loading":
      return {
        badge: "載入中",
        code: "OPERATOR_DATA_LOADING",
        detail: "正在取得最新的營運資料，完成前不會顯示示範資料。",
        next: "請稍候。",
        title: "營運資料載入中",
      };
    case "seed":
      return {
        badge: "資料來源未通過",
        code: "OPERATOR_SEED_DATA_BLOCKED",
        detail: "資料來源未通過正式資料檢查（可能是測試或示範資料），因此不顯示。",
        next: "這不是畫面故障；請通知維運人員確認資料來源設定。",
        title: "目前沒有可用的正式資料",
      };
    case "empty":
      return {
        badge: "尚無資料",
        code: "OPERATOR_DATA_EMPTY",
        detail: "服務已回應，但目前沒有可供此工作台使用的正式資料。",
        next: "若應該要有資料，請通知維運人員確認資料匯入狀態。",
        title: "目前沒有營運資料",
      };
    default:
      return failureMessage(failureKind ?? "unknown");
  }
}

export function toUnavailableOperatorStatus(
  status: OperatorDataAvailability,
): Exclude<OperatorDataAvailability, "ready" | "fixture"> {
  if (status === "fixture") return "seed";
  if (status === "ready") return "error";
  return status;
}
import {
  isNonProductionDataSource,
  payloadContainsNonProductionData,
} from "../../src/lib/api/liveData";
import { isProductionMode } from "../shell/mode";
