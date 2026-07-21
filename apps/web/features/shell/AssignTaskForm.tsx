"use client";

/**
 * Task assignment control (ODP-PGAP-SHELL-001, acceptance §2).
 *
 * Only rendered when the server said the role may assign, but the 403 path is
 * still handled: the server re-checks and this form renders its refusal rather
 * than assuming the local gate was right.
 *
 * The idempotency key is minted once per open dialog and reused across retries,
 * so a retried submit cannot assign twice.
 */
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@oday-plus/ui";
import { newShellIdempotencyKey, shellWrite } from "./shellClient.ts";
import type { ShellWriteResult } from "./shellClient.ts";
import type { ShellTaskAssignResponse } from "@oday-plus/openapi-client";
import styles from "./shell.module.css";

export function AssignTaskForm({
  taskId,
  roleId,
  assignableRoles,
  currentAssigneeId,
}: {
  taskId: string;
  roleId: string | null;
  assignableRoles: Array<{ id: string; label: string }>;
  currentAssigneeId: string | null;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [assigneeId, setAssigneeId] = useState(
    currentAssigneeId ?? `operator-${assignableRoles[0]?.id ?? ""}`,
  );
  const [slaDueAt, setSlaDueAt] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ShellWriteResult<ShellTaskAssignResponse> | null>(null);
  // One key per open dialog: retrying a failed submit reuses it, so the retry
  // cannot apply a second assignment if the first actually landed.
  const [idempotencyKey, setIdempotencyKey] = useState(() =>
    newShellIdempotencyKey("assign", taskId),
  );

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    const assignee = assignableRoles.find((role) => `operator-${role.id}` === assigneeId);
    const outcome = await shellWrite<ShellTaskAssignResponse>(
      roleId,
      (client, options) =>
        client.assignShellTask(
          taskId,
          {
            assigneeId,
            assigneeName: assignee?.label,
            slaDueAt: slaDueAt ? new Date(slaDueAt).toISOString() : null,
          },
          options,
        ),
      idempotencyKey,
    );
    setResult(outcome);
    setBusy(false);
    if (outcome.ok) {
      setOpen(false);
      setIdempotencyKey(newShellIdempotencyKey("assign", taskId));
      router.refresh();
    }
  }

  if (!open) {
    return (
      <Button
        variant="secondary"
        onClick={() => setOpen(true)}
        data-testid={`task-assign-open-${taskId}`}
      >
        指派
      </Button>
    );
  }

  return (
    <form className={styles.form} onSubmit={submit} data-testid={`task-assign-form-${taskId}`}>
      <div className={styles.field}>
        <label htmlFor={`assignee-${taskId}`}>指派給</label>
        <select
          id={`assignee-${taskId}`}
          value={assigneeId}
          onChange={(event) => setAssigneeId(event.target.value)}
          data-testid={`task-assign-select-${taskId}`}
        >
          {assignableRoles.map((role) => (
            <option key={role.id} value={`operator-${role.id}`}>
              {role.label}
            </option>
          ))}
        </select>
      </div>
      <div className={styles.field}>
        <label htmlFor={`sla-${taskId}`}>SLA 到期時間（選填）</label>
        <input
          id={`sla-${taskId}`}
          type="datetime-local"
          value={slaDueAt}
          onChange={(event) => setSlaDueAt(event.target.value)}
          data-testid={`task-assign-sla-${taskId}`}
        />
      </div>
      {result && !result.ok ? (
        <p className={styles.formNote} role="alert" data-testid={`task-assign-error-${taskId}`}>
          {result.error.summary} {result.error.nextAction}
          {result.error.correlationId ? `（correlation: ${result.error.correlationId}）` : ""}
        </p>
      ) : null}
      <div className={styles.rowActions}>
        <Button type="submit" disabled={busy} data-testid={`task-assign-submit-${taskId}`}>
          {busy ? "送出中…" : "確認指派"}
        </Button>
        <Button variant="secondary" type="button" onClick={() => setOpen(false)}>
          取消
        </Button>
      </div>
    </form>
  );
}
