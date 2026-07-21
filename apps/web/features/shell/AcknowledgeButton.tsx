"use client";

/**
 * Acknowledge one notification (ODP-PGAP-SHELL-001, acceptance §3).
 *
 * The button does not optimistically flip to "acknowledged": the row only
 * changes once the server has durably recorded it, because an inbox that shows
 * a state the backend never stored is exactly the failure this task exists to
 * remove.
 */
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@oday-plus/ui";
import { newShellIdempotencyKey, shellWrite } from "./shellClient.ts";
import type { ShellWriteResult } from "./shellClient.ts";
import type { ShellWriteResponse } from "@oday-plus/openapi-client";
import styles from "./shell.module.css";

export function AcknowledgeButton({
  notificationId,
  roleId,
}: {
  notificationId: string;
  roleId: string | null;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ShellWriteResult<ShellWriteResponse> | null>(null);
  const [idempotencyKey] = useState(() => newShellIdempotencyKey("ack", notificationId));

  async function acknowledge() {
    setBusy(true);
    const outcome = await shellWrite<ShellWriteResponse>(
      roleId,
      (client, options) => client.acknowledgeShellNotification(notificationId, options),
      idempotencyKey,
    );
    setResult(outcome);
    setBusy(false);
    if (outcome.ok) router.refresh();
  }

  return (
    <>
      <Button
        onClick={acknowledge}
        disabled={busy}
        data-testid={`notification-ack-${notificationId}`}
      >
        {busy ? "確認中…" : "確認"}
      </Button>
      {result && !result.ok ? (
        <p
          className={styles.formNote}
          role="alert"
          data-testid={`notification-ack-error-${notificationId}`}
        >
          {result.error.summary} {result.error.nextAction}
        </p>
      ) : null}
    </>
  );
}
