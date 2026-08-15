/**
 * Shell state surfaces (ODP-PGAP-SHELL-001, acceptance §7).
 *
 * One vocabulary for every non-happy path the shell can reach: forbidden,
 * not-found, error, offline, maintenance, loading, and unconfigured. Each one
 * must (a) say what happened in operator-facing zh-TW, (b) offer a next step —
 * never a dead end (component contracts §4.12), and (c) expose a stable
 * `data-testid` + `data-state` so desktop and mobile E2E can assert on the
 * state rather than on prose.
 *
 * In production mode these replace fixture fallback entirely: a shell that
 * cannot reach its API says so, rather than rendering invented rows.
 */
import type { ReactNode } from "react";
import { Badge } from "@oday-plus/ui";
import type { StatusTone } from "@oday-plus/domain-types";
import type { ApiResource } from "./resource.ts";
import { isProductionMode } from "./mode.ts";
import styles from "./shell.module.css";

export type ShellStateKind =
  | "forbidden"
  | "not-found"
  | "error"
  | "offline"
  | "maintenance"
  | "loading"
  | "unconfigured"
  | "empty";

type StateCopy = {
  title: string;
  body: string;
  next: string;
  tone: StatusTone;
};

const STATE_COPY: Record<ShellStateKind, StateCopy> = {
  forbidden: {
    title: "沒有這個畫面的權限",
    body: "你的角色無法檢視此工作區。這不是錯誤，而是權限設定的結果。",
    next: "若你認為這是誤設，請聯繫營運主管調整角色的工作區授權。",
    tone: "orange",
  },
  "not-found": {
    title: "找不到這個頁面",
    body: "此路徑不存在，或你追蹤的連結指向已被移除的項目。",
    next: "回到總覽，或用全域搜尋找到你要的項目。",
    tone: "blue",
  },
  error: {
    title: "這個區塊暫時載入失敗",
    body: "後端回應異常，畫面不會顯示可能過期或推測的內容。",
    next: "請重試；若持續失敗，請附上下方 correlation ID 通報平台維運。",
    tone: "red",
  },
  offline: {
    title: "目前離線",
    body: "偵測不到網路連線，因此無法取得最新的營運狀態。",
    next: "恢復連線後畫面會自動重試；期間不會顯示快取的舊資料作為現況。",
    tone: "orange",
  },
  maintenance: {
    title: "系統維護中",
    body: "平台正在進行維護，寫入動作已暫停以避免產生不一致的紀錄。",
    next: "請稍後再試。維護期間的既有紀錄不受影響。",
    tone: "blue",
  },
  loading: {
    title: "載入中",
    body: "正在向後端取得最新狀態。",
    next: "請稍候。",
    tone: "gray",
  },
  unconfigured: {
    title: "尚未設定後端位址",
    body: "此環境未設定 API base URL（ODP_API_BASE_URL）。在 production 模式下，畫面不會以固定樣本代替真實資料。",
    next: "請聯繫平台維運設定 API 位址。",
    tone: "red",
  },
  empty: {
    title: "目前沒有項目",
    body: "後端可連線，且這個範圍內確實沒有待處理項目。",
    next: "這是正常狀態，無需處理。",
    tone: "gray",
  },
};

/**
 * A state surface. `detail` carries server-supplied refusal copy — rendered
 * verbatim, because the server is the only place the real reason exists.
 */
export function ShellState({
  kind,
  detail,
  correlationId,
  testId,
  actions,
}: {
  kind: ShellStateKind;
  detail?: string;
  correlationId?: string;
  testId?: string;
  actions?: ReactNode;
}) {
  const copy = STATE_COPY[kind];
  return (
    <div
      className={styles.state}
      data-testid={testId ?? `shell-state-${kind}`}
      data-state={kind}
      role={kind === "loading" ? "status" : "alert"}
      aria-live={kind === "loading" ? "polite" : "assertive"}
    >
      <div className={styles.stateHead}>
        <Badge label={copy.title} tone={copy.tone} marker="●" />
      </div>
      <h2 className={styles.stateTitle}>{copy.title}</h2>
      <p className={styles.stateBody}>{detail ?? copy.body}</p>
      <p className={styles.stateNext} data-testid="shell-state-next">
        {copy.next}
      </p>
      {correlationId ? (
        <p className={styles.stateMeta}>
          Correlation ID: <code data-testid="shell-state-correlation">{correlationId}</code>
        </p>
      ) : null}
      {actions ? <div className={styles.stateActions}>{actions}</div> : null}
    </div>
  );
}

/**
 * Map a failed resource onto its state surface.
 *
 * Returns `null` when the resource is ready — the caller then renders the real
 * content. In production mode an unconfigured API is a hard state; in POC mode
 * the caller may still choose a documented fixture fallback.
 */
export function resourceState(resource: ApiResource<unknown>): ShellStateKind | null {
  switch (resource.state) {
    case "ready":
      return null;
    case "forbidden":
      return "forbidden";
    case "unauthorized":
      return "forbidden";
    case "unconfigured":
      return "unconfigured";
    case "error":
      // A 503 is a maintenance window; anything else is an outage the operator
      // should report rather than wait out.
      return resource.status === 503 ? "maintenance" : "error";
    default:
      return "error";
  }
}

/** Render the state surface for a failed resource, or null when it is ready. */
export function ShellResourceState({
  resource,
  testId,
  actions,
}: {
  resource: ApiResource<unknown>;
  testId?: string;
  actions?: ReactNode;
}) {
  const kind = resourceState(resource);
  if (kind === null) return null;
  return (
    <ShellState
      kind={kind}
      detail={resource.detail}
      correlationId={resource.correlationId}
      testId={testId}
      actions={actions}
    />
  );
}

/**
 * Data-source disclosure for a shell region.
 *
 * In production mode there is no fixture path, so this states the live source
 * and its freshness. It keeps the `data-source` / `data-state` attribute
 * contract the other workspaces' E2E specs already rely on.
 */
export function ShellDataSource({
  resource,
  endpoint,
  testId,
}: {
  resource: ApiResource<unknown>;
  endpoint: string;
  testId: string;
}) {
  const live = resource.state === "ready";
  return (
    <span
      data-testid={testId}
      data-source={live ? "api" : "none"}
      data-state={resource.state}
      data-mode={isProductionMode() ? "production" : "poc"}
      className={styles.source}
    >
      <Badge
        label={live ? `API live · ${endpoint}` : `無法取得 · ${endpoint}`}
        tone={live ? "green" : "red"}
        marker={live ? "◆" : "◫"}
      />
    </span>
  );
}
