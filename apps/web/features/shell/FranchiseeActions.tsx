"use client";

/**
 * Franchisee acknowledgement + reporting (ODP-PGAP-SHELL-001, acceptance §6).
 *
 * Identity comes from the franchisee's own session, not from an operator role —
 * these calls go to the franchisee_portal resource, which operations can read
 * but never write. Passing a role id here would be meaningless (and the server
 * would ignore it), so it is deliberately omitted.
 */
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@oday-plus/ui";
import { newShellIdempotencyKey, shellWrite } from "./shellClient.ts";
import type { ShellWriteResult } from "./shellClient.ts";
import type { ShellWriteResponse } from "@oday-plus/openapi-client";
import { REPORT_CATEGORY_LABEL } from "./vocabulary.ts";
import styles from "./shell.module.css";

type Props = {
  mode: "acknowledge" | "report";
  notificationId?: string;
  acknowledged?: boolean;
  storeId: string;
  categories: string[];
};

export function FranchiseeActions(props: Props) {
  return props.mode === "acknowledge" ? (
    <AcknowledgeAction {...props} />
  ) : (
    <ReportAction {...props} />
  );
}

function AcknowledgeAction({ notificationId, acknowledged, storeId }: Props) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ShellWriteResult<ShellWriteResponse> | null>(null);
  const [idempotencyKey] = useState(() =>
    newShellIdempotencyKey("fr-ack", notificationId ?? "unknown"),
  );

  if (acknowledged) {
    return (
      <p className={styles.rowMeta} data-testid={`franchisee-acked-${notificationId}`}>
        ✓ 已確認
      </p>
    );
  }

  async function acknowledge() {
    if (!notificationId) return;
    setBusy(true);
    const outcome = await shellWrite<ShellWriteResponse>(
      null,
      (client, options) =>
        client.acknowledgeShellFranchiseeNotification({ notificationId, storeId }, options),
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
        data-testid={`franchisee-ack-${notificationId}`}
      >
        {busy ? "確認中…" : "我已閱讀"}
      </Button>
      {result && !result.ok ? (
        <p className={styles.formNote} role="alert" data-testid={`franchisee-ack-error-${notificationId}`}>
          {result.error.summary} {result.error.nextAction}
        </p>
      ) : null}
    </>
  );
}

function ReportAction({ storeId, categories }: Props) {
  const router = useRouter();
  const [category, setCategory] = useState(categories[0] ?? "other");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ShellWriteResult<ShellWriteResponse> | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState(() =>
    newShellIdempotencyKey("fr-report", storeId),
  );

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    const outcome = await shellWrite<ShellWriteResponse>(
      null,
      (client, options) =>
        client.submitShellFranchiseeReport({ category, message, storeId }, options),
      idempotencyKey,
    );
    setResult(outcome);
    setBusy(false);
    if (outcome.ok) {
      setMessage("");
      // Each report is a distinct submission — a fresh key, or the second
      // report of the day would replay the first.
      setIdempotencyKey(newShellIdempotencyKey("fr-report", storeId));
      router.refresh();
    }
  }

  return (
    <form className={styles.form} onSubmit={submit} data-testid="franchisee-report-form">
      <div className={styles.field}>
        <label htmlFor="report-category">回報類別</label>
        <select
          id="report-category"
          value={category}
          onChange={(event) => setCategory(event.target.value)}
          data-testid="franchisee-report-category"
        >
          {categories.map((value) => (
            <option key={value} value={value}>
              {REPORT_CATEGORY_LABEL[value] ?? value}
            </option>
          ))}
        </select>
      </div>
      <div className={styles.field}>
        <label htmlFor="report-message">狀況說明</label>
        <textarea
          id="report-message"
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          required
          data-testid="franchisee-report-message"
          placeholder="請簡述現場狀況"
        />
      </div>
      {result ? (
        <p
          className={styles.formNote}
          role="status"
          data-testid={result.ok ? "franchisee-report-sent" : "franchisee-report-error"}
        >
          {result.ok
            ? "已送出回報，營運團隊會收到通知。"
            : `${result.error.summary} ${result.error.nextAction}`}
        </p>
      ) : null}
      <div>
        <Button
          type="submit"
          disabled={busy || message.trim().length === 0}
          data-testid="franchisee-report-submit"
        >
          {busy ? "送出中…" : "送出回報"}
        </Button>
      </div>
    </form>
  );
}
