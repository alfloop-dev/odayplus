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
import { useEffect, useRef, useState } from "react";
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
    <AcknowledgeAction
      key={`${props.storeId}:${props.notificationId ?? ""}`}
      {...props}
    />
  ) : (
    <ReportAction key={props.storeId} {...props} />
  );
}

function AcknowledgeAction({ notificationId, acknowledged, storeId }: Props) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ShellWriteResult<ShellWriteResponse> | null>(null);
  const mountedRef = useRef(true);
  const [idempotencyKey] = useState(() =>
    newShellIdempotencyKey("fr-ack", notificationId ?? "unknown"),
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  if (acknowledged) {
    return (
      <p className={styles.rowMeta} data-testid={`franchisee-acked-${notificationId}`}>
        ✓ 已確認
      </p>
    );
  }

  async function acknowledge() {
    if (!notificationId || busy) return;
    const submittedStoreId = storeId;
    const submittedNotificationId = notificationId;
    const submittedKey = idempotencyKey;
    setBusy(true);
    const outcome = await shellWrite<ShellWriteResponse>(
      null,
      (client, options) =>
        client.acknowledgeShellFranchiseeNotification(
          { notificationId: submittedNotificationId, storeId: submittedStoreId },
          options,
        ),
      submittedKey,
    );
    if (!mountedRef.current) return;
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
  const mountedRef = useRef(true);
  const draftRevisionRef = useRef(0);
  const submittedRevisionRef = useRef<number | null>(null);
  const idempotencyKeyRef = useRef("");
  if (idempotencyKeyRef.current === "") {
    idempotencyKeyRef.current = newShellIdempotencyKey("fr-report", storeId);
  }

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  function recordDraftEdit() {
    draftRevisionRef.current += 1;
    if (submittedRevisionRef.current !== null) {
      idempotencyKeyRef.current = newShellIdempotencyKey("fr-report", storeId);
      submittedRevisionRef.current = null;
    }
  }

  function handleCategoryChange(nextCategory: string) {
    recordDraftEdit();
    setCategory(nextCategory);
  }

  function handleMessageChange(nextMessage: string) {
    recordDraftEdit();
    setMessage(nextMessage);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy || message.trim().length === 0) return;
    const submittedStoreId = storeId;
    const submittedCategory = category;
    const submittedMessage = message;
    const submittedRevision = draftRevisionRef.current;
    const submittedKey = idempotencyKeyRef.current;
    submittedRevisionRef.current = submittedRevision;

    setBusy(true);
    const outcome = await shellWrite<ShellWriteResponse>(
      null,
      (client, options) =>
        client.submitShellFranchiseeReport(
          {
            category: submittedCategory,
            message: submittedMessage,
            storeId: submittedStoreId,
          },
          options,
        ),
      submittedKey,
    );
    if (!mountedRef.current) return;
    setResult(outcome);
    setBusy(false);
    if (outcome.ok) {
      if (draftRevisionRef.current === submittedRevision) {
        setMessage("");
        // Each report is a distinct submission — a fresh key, or the second
        // report of the day would replay the first.
        idempotencyKeyRef.current = newShellIdempotencyKey("fr-report", submittedStoreId);
        submittedRevisionRef.current = null;
      }
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
          onChange={(event) => handleCategoryChange(event.target.value)}
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
          onChange={(event) => handleMessageChange(event.target.value)}
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
