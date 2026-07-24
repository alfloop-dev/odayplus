"use client";

/**
 * Change a role's workspace grants (ODP-PGAP-SHELL-001, acceptance §5).
 *
 * High-risk governed write. The server owns the invariants (Today is
 * mandatory; the admin role keeps govern) and this form renders its refusal
 * verbatim rather than duplicating those rules — a second copy of a rule is a
 * second place for it to be wrong.
 */
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@oday-plus/ui";
import { newShellIdempotencyKey, shellWrite } from "./shellClient.ts";
import type { ShellWriteResult } from "./shellClient.ts";
import type { ShellWriteResponse } from "@oday-plus/openapi-client";
import styles from "./shell.module.css";

export function RoleWorkspacesForm({
  roleId,
  actingRoleId,
  workspaces,
  allowed,
}: {
  roleId: string;
  actingRoleId: string | null;
  workspaces: Array<{ id: string; label: string }>;
  allowed: string[];
}) {
  const router = useRouter();
  const [draft, setDraft] = useState<string[]>(allowed);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ShellWriteResult<ShellWriteResponse> | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState(() =>
    newShellIdempotencyKey("grant", roleId),
  );

  function toggle(workspaceId: string, checked: boolean) {
    setDraft((current) =>
      checked ? [...current, workspaceId] : current.filter((id) => id !== workspaceId),
    );
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    const outcome = await shellWrite<ShellWriteResponse>(
      actingRoleId,
      (client, options) =>
        client.updateShellRoleWorkspaces(roleId, { allowedWorkspaces: draft }, options),
      idempotencyKey,
    );
    setResult(outcome);
    setBusy(false);
    if (outcome.ok) {
      setIdempotencyKey(newShellIdempotencyKey("grant", roleId));
      router.refresh();
    }
  }

  const dirty =
    draft.length !== allowed.length || draft.some((id) => !allowed.includes(id));

  return (
    <form className={styles.form} onSubmit={submit} data-testid={`admin-grant-form-${roleId}`}>
      <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className={styles.rowMeta}>可進入的工作區</legend>
        {workspaces.map((workspace) => (
          <div key={workspace.id} className={styles.checkRow}>
            <input
              id={`ws-${roleId}-${workspace.id}`}
              type="checkbox"
              checked={draft.includes(workspace.id)}
              onChange={(event) => toggle(workspace.id, event.target.checked)}
              data-testid={`admin-grant-${roleId}-${workspace.id}`}
            />
            <label htmlFor={`ws-${roleId}-${workspace.id}`}>{workspace.label}</label>
          </div>
        ))}
      </fieldset>

      {result && !result.ok ? (
        <p className={styles.formNote} role="alert" data-testid={`admin-grant-error-${roleId}`}>
          {result.error.summary} {result.error.nextAction}
        </p>
      ) : null}
      {result?.ok ? (
        <p className={styles.formNote} role="status" data-testid={`admin-grant-saved-${roleId}`}>
          已更新授權並寫入稽核紀錄。
        </p>
      ) : null}

      <div>
        <Button
          type="submit"
          disabled={busy || !dirty}
          data-testid={`admin-grant-submit-${roleId}`}
        >
          {busy ? "儲存中…" : "更新授權"}
        </Button>
      </div>
    </form>
  );
}
