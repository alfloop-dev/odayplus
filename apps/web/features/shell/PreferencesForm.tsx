"use client";

/**
 * Notification delivery preferences (ODP-PGAP-SHELL-001, acceptance §3).
 *
 * A governed server write, not local storage: preferences decide what reaches
 * an operator out-of-band, so they have to be durable and audited rather than
 * per-browser.
 */
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@oday-plus/ui";
import { newShellIdempotencyKey, shellWrite } from "./shellClient.ts";
import type { ShellWriteResult } from "./shellClient.ts";
import type { ShellPreferences, ShellPreferencesWriteResponse } from "@oday-plus/openapi-client";
import styles from "./shell.module.css";

const CHANNEL_LABEL: Record<string, string> = {
  inApp: "站內通知",
  email: "電子郵件",
  push: "行動推播",
};

const FLOOR_OPTIONS = [
  { value: "critical", label: "只有嚴重" },
  { value: "warning", label: "警告以上" },
  { value: "info", label: "全部（含資訊）" },
];

const DIGEST_OPTIONS = [
  { value: "immediate", label: "即時" },
  { value: "daily", label: "每日彙整" },
];

export function PreferencesForm({
  preferences,
  roleId,
}: {
  preferences: ShellPreferences;
  roleId: string | null;
}) {
  const router = useRouter();
  const [draft, setDraft] = useState<ShellPreferences>(preferences);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ShellWriteResult<ShellPreferencesWriteResponse> | null>(
    null,
  );
  const [idempotencyKey, setIdempotencyKey] = useState(() =>
    newShellIdempotencyKey("prefs", roleId ?? "self"),
  );

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    const outcome = await shellWrite<ShellPreferencesWriteResponse>(
      roleId,
      (client, options) => client.updateShellNotificationPreferences(draft, options),
      idempotencyKey,
    );
    setResult(outcome);
    setBusy(false);
    if (outcome.ok) {
      // A new logical operation gets a new key; reusing the old one would make
      // the next save a no-op replay of this one.
      setIdempotencyKey(newShellIdempotencyKey("prefs", roleId ?? "self"));
      router.refresh();
    }
  }

  return (
    <form className={styles.form} onSubmit={submit} data-testid="preferences-form">
      <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className={styles.rowMeta}>傳送管道</legend>
        {Object.entries(draft.channels).map(([channel, enabled]) => (
          <div key={channel} className={styles.checkRow}>
            <input
              id={`channel-${channel}`}
              type="checkbox"
              checked={enabled}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  channels: { ...draft.channels, [channel]: event.target.checked },
                })
              }
              data-testid={`preferences-channel-${channel}`}
            />
            <label htmlFor={`channel-${channel}`}>{CHANNEL_LABEL[channel] ?? channel}</label>
          </div>
        ))}
      </fieldset>

      <div className={styles.field}>
        <label htmlFor="severity-floor">最低嚴重度</label>
        <select
          id="severity-floor"
          value={draft.severityFloor}
          onChange={(event) => setDraft({ ...draft, severityFloor: event.target.value })}
          data-testid="preferences-severity-floor"
        >
          {FLOOR_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      <div className={styles.field}>
        <label htmlFor="digest">傳送頻率</label>
        <select
          id="digest"
          value={draft.digest}
          onChange={(event) => setDraft({ ...draft, digest: event.target.value })}
          data-testid="preferences-digest"
        >
          {DIGEST_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      {result ? (
        <p
          className={styles.formNote}
          role="status"
          data-testid={result.ok ? "preferences-saved" : "preferences-error"}
        >
          {result.ok
            ? "偏好已儲存，並寫入稽核紀錄。"
            : `${result.error.summary} ${result.error.nextAction}`}
        </p>
      ) : null}

      <div>
        <Button type="submit" disabled={busy} data-testid="preferences-submit">
          {busy ? "儲存中…" : "儲存偏好"}
        </Button>
      </div>
    </form>
  );
}
