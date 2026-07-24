import type { ReactNode } from "react";
import { Badge } from "@oday-plus/ui";
import type { StatusTone } from "@oday-plus/domain-types";
import type { ApiBinding, BindingState } from "../../src/lib/api/binding.ts";
import styles from "./productionDataState.module.css";

const STATE_LABEL: Record<BindingState, string> = {
  ready: "API live",
  empty: "API empty",
  error: "API unavailable",
  unconfigured: "Live API unconfigured",
};

const STATE_TONE: Record<BindingState, StatusTone> = {
  ready: "green",
  empty: "blue",
  error: "red",
  unconfigured: "gray",
};

export function resolveProductionMode(explicit?: boolean): boolean {
  if (explicit !== undefined) return explicit;
  return (
    process.env.NODE_ENV === "production" ||
    process.env.NEXT_PUBLIC_PRODUCTION_MODE === "true" ||
    process.env.ODP_REQUIRE_LIVE_DATA === "true"
  );
}

export function productionBindingState(
  binding?: Pick<ApiBinding<unknown>, "source" | "state">,
): BindingState {
  if (!binding) return "unconfigured";
  if (binding.state === "ready" && binding.source !== "api") return "unconfigured";
  return binding.state;
}

export function ProductionDataState<T>({
  binding,
  children,
  resource,
  testId,
}: {
  binding?: ApiBinding<T>;
  children?: ReactNode;
  resource: string;
  testId: string;
}) {
  const state = productionBindingState(binding);
  if (state === "ready" && binding) {
    return <>{children}</>;
  }

  return (
    <section
      aria-live="polite"
      className={styles.state}
      data-source={state === "ready" ? "api" : "unavailable"}
      data-state={state}
      data-testid={testId}
      role={state === "error" ? "alert" : "status"}
    >
      <div className={styles.heading}>
        <h2>{resource}</h2>
        <Badge label={STATE_LABEL[state]} marker={state === "error" ? "!" : "◇"} tone={STATE_TONE[state]} />
      </div>
      <p>{productionStateMessage(state, resource, binding?.error)}</p>
      {binding?.fetchedAt ? <p className={styles.meta}>Checked at {binding.fetchedAt}</p> : null}
    </section>
  );
}

export function ProductionDataBadge<T>({
  binding,
  testId,
}: {
  binding: ApiBinding<T>;
  testId: string;
}) {
  const state = productionBindingState(binding);
  return (
    <span data-source="api" data-state={state} data-testid={testId}>
      <Badge label={STATE_LABEL[state]} marker={state === "error" ? "!" : "◆"} tone={STATE_TONE[state]} />
    </span>
  );
}

function productionStateMessage(state: BindingState, resource: string, error?: string): string {
  if (state === "empty") {
    return `${resource} API 已連線，但目前沒有資料。此頁不會以範例資料補位。`;
  }
  if (state === "error") {
    return `${resource} 暫時無法讀取${error ? `：${error}` : ""}。請重試或聯絡值班人員；目前未顯示任何替代資料。`;
  }
  return `${resource} 尚未設定 live API。完成環境設定前，此頁不顯示範例或 fixture 資料。`;
}
