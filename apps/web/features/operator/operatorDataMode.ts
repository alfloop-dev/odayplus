export type OperatorDataAvailability =
  | "loading"
  | "ready"
  | "error"
  | "seed"
  | "empty"
  | "fixture";

type RuntimeEnvironment = {
  nodeEnv?: string;
  productionMode?: string;
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
    nodeEnv: process.env.NODE_ENV,
    productionMode: process.env.NEXT_PUBLIC_PRODUCTION_MODE,
  },
): boolean {
  return environment.nodeEnv === "production" || environment.productionMode === "true";
}

export function operatorFixturesAllowed(environment?: RuntimeEnvironment): boolean {
  return !isOperatorProductionMode(environment);
}

export function isSeedDataSource(source: unknown): boolean {
  if (typeof source !== "string") return false;
  const normalized = source.trim().toLowerCase();
  return (
    KNOWN_SEED_SOURCES.has(normalized) ||
    normalized.includes("fixture") ||
    normalized.includes("mock") ||
    normalized.includes("prototype") ||
    normalized.includes("demo") ||
    normalized.includes("seed")
  );
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
    liveReady;
  if (!explicitlyLive && isSeedDataSource(source)) {
    return { status: "seed", source };
  }

  const navigation = asRecord(root.navigation);
  const today = asRecord(root.today);
  const search = asRecord(root.search);
  const hasNavigation = asArray(navigation?.workspaces).length > 0;
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

  if (!source || !hasNavigation || !hasOperationalRows) {
    return { status: "empty", source };
  }

  return { status: "ready", source };
}

export function unavailableDataMessage(status: OperatorDataAvailability): {
  code: string;
  detail: string;
  title: string;
} {
  switch (status) {
    case "loading":
      return {
        code: "OPERATOR_DATA_LOADING",
        detail: "正在向 Operator API 取得目前資料。載入完成前不會顯示測試資料。",
        title: "營運資料載入中",
      };
    case "seed":
      return {
        code: "OPERATOR_SEED_DATA_BLOCKED",
        detail: "API 回傳的是 seed、fixture 或 mock 資料。Production 模式已阻止渲染。",
        title: "目前沒有可用的正式資料",
      };
    case "empty":
      return {
        code: "OPERATOR_DATA_EMPTY",
        detail: "API 已回應，但沒有可供此工作台使用的正式資料。",
        title: "目前沒有營運資料",
      };
    case "error":
      return {
        code: "OPERATOR_DATA_UNAVAILABLE",
        detail: "Operator API 無法完成讀取。請重試，或使用 correlation ID 進行查核。",
        title: "營運資料暫時無法取得",
      };
    default:
      return {
        code: "OPERATOR_DATA_UNAVAILABLE",
        detail: "目前沒有可供此工作台使用的正式資料。",
        title: "營運資料暫時無法取得",
      };
  }
}

export function toUnavailableOperatorStatus(
  status: OperatorDataAvailability,
): Exclude<OperatorDataAvailability, "ready" | "fixture"> {
  if (status === "fixture") return "seed";
  if (status === "ready") return "error";
  return status;
}
