"use client";

/**
 * Settings form (ODP-PGAP-SHELL-001, acceptance §5).
 *
 * The option lists come from the server so the client cannot offer a value the
 * server would reject. Only changed keys are sent — a settings write is a patch,
 * so one operator's save cannot clobber a key they never touched.
 */
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@oday-plus/ui";
import { newShellIdempotencyKey, shellWrite } from "./shellClient.ts";
import type { ShellWriteResult } from "./shellClient.ts";
import type { ShellSettingsWriteResponse } from "@oday-plus/openapi-client";
import styles from "./shell.module.css";

const FIELD_LABEL: Record<string, string> = {
  locale: "語言",
  timezone: "時區",
  density: "版面密度",
};

const VALUE_LABEL: Record<string, string> = {
  "zh-TW": "繁體中文",
  "en-US": "English",
  "Asia/Taipei": "台北（UTC+8）",
  UTC: "UTC",
  comfortable: "寬鬆",
  compact: "緊湊",
};

export function SettingsForm({
  values,
  options,
  roleId,
}: {
  values: Record<string, string>;
  options: Record<string, string[]>;
  roleId: string | null;
}) {
  const router = useRouter();
  const [draft, setDraft] = useState(values);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ShellWriteResult<ShellSettingsWriteResponse> | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState(() =>
    newShellIdempotencyKey("settings", roleId ?? "self"),
  );

  const changed = Object.fromEntries(
    Object.entries(draft).filter(([key, value]) => values[key] !== value),
  );
  const dirty = Object.keys(changed).length > 0;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    const outcome = await shellWrite<ShellSettingsWriteResponse>(
      roleId,
      (client, options_) => client.updateShellSettings(changed, options_),
      idempotencyKey,
    );
    setResult(outcome);
    setBusy(false);
    if (outcome.ok) {
      setIdempotencyKey(newShellIdempotencyKey("settings", roleId ?? "self"));
      router.refresh();
    }
  }

  return (
    <form className={styles.form} onSubmit={submit} data-testid="settings-form">
      {Object.entries(options).map(([key, allowed]) => (
        <div key={key} className={styles.field}>
          <label htmlFor={`setting-${key}`}>{FIELD_LABEL[key] ?? key}</label>
          <select
            id={`setting-${key}`}
            value={draft[key] ?? allowed[0]}
            onChange={(event) => setDraft({ ...draft, [key]: event.target.value })}
            data-testid={`settings-${key}`}
          >
            {allowed.map((value) => (
              <option key={value} value={value}>
                {VALUE_LABEL[value] ?? value}
              </option>
            ))}
          </select>
        </div>
      ))}

      {result ? (
        <p
          className={styles.formNote}
          role="status"
          data-testid={result.ok ? "settings-saved" : "settings-error"}
        >
          {result.ok
            ? "設定已儲存，並寫入稽核紀錄。"
            : `${result.error.summary} ${result.error.nextAction}`}
        </p>
      ) : null}

      <div>
        <Button type="submit" disabled={busy || !dirty} data-testid="settings-submit">
          {busy ? "儲存中…" : "儲存設定"}
        </Button>
      </div>
    </form>
  );
}
