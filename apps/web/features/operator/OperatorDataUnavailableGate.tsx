"use client";

import { Button, StatusBadge } from "./components";
import {
  unavailableDataMessage,
  type OperatorDataAvailability,
} from "./operatorDataMode";
import styles from "./operator.module.css";

export function OperatorDataUnavailableGate({
  detail,
  onRetry,
  status,
}: {
  detail?: string | null;
  onRetry?: () => void;
  status: Exclude<OperatorDataAvailability, "ready" | "fixture">;
}) {
  const message = unavailableDataMessage(status);

  return (
    <section
      aria-live={status === "loading" ? "polite" : "assertive"}
      className={styles.dataUnavailableGate}
      data-status={status}
      data-testid="operator-data-unavailable"
      role={status === "loading" ? "status" : "alert"}
    >
      <div>
        <StatusBadge tone={status === "loading" ? "info" : "warning"}>
          {message.code}
        </StatusBadge>
        <h1>{message.title}</h1>
        <p>{detail || message.detail}</p>
      </div>
      {onRetry && status !== "loading" ? (
        <Button onClick={onRetry} size="sm" variant="secondary">
          重新載入
        </Button>
      ) : null}
    </section>
  );
}
