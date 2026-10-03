"use client";

/**
 * First-login password rotation (ODP-DEV-ADMIN-RELEASE-READINESS-001).
 *
 * The identity bootstrap creates the first administrator with its one-time
 * secret as the initial password and `must_change=true`; the API refuses every
 * protected call with PASSWORD_CHANGE_REQUIRED until it is rotated. This form
 * posts to the existing `/auth/password` route, which verifies the current
 * password, applies the password policy, stores only the Argon2id hash, clears
 * must_change and revokes every other session for the account.
 */

import React, { useState } from "react";
import { Button } from "./components";
import styles from "./operator.module.css";
import { ADMIN_PATH } from "./operatorSession";

const ERROR_MESSAGES: Record<string, string> = {
  AUTH_INVALID_CREDENTIALS: "目前密碼不正確。",
  WEB_SESSION_REQUIRED: "登入已失效，請重新登入。",
  CSRF_VERIFICATION_FAILED: "請求來源驗證失敗，請重新整理後再試。",
  WEB_AUTH_UNAVAILABLE: "驗證服務暫時無法使用，請稍後重試。",
};

export function OperatorPasswordChange({
  navigate = (path: string) => window.location.assign(path),
}: {
  navigate?: (path: string) => void;
}) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    if (newPassword !== confirmPassword) {
      setError("兩次輸入的新密碼不一致。");
      return;
    }
    setSubmitting(true);
    try {
      const res = await fetch("/auth/password", {
        method: "POST",
        credentials: "same-origin",
        headers: { accept: "application/json", "content-type": "application/json" },
        body: JSON.stringify({ currentPassword, newPassword }),
      });
      if (res.ok) {
        navigate(ADMIN_PATH);
        return;
      }
      let code = "";
      let summary = "";
      try {
        const body = (await res.json()) as { error?: { code?: string; summary?: string } };
        code = body?.error?.code ?? "";
        summary = body?.error?.summary ?? "";
      } catch {
        // fall through to the generic message
      }
      setError(ERROR_MESSAGES[code] ?? (summary || `密碼變更失敗（${res.status}）。`));
    } catch {
      setError("無法連線至驗證服務，請稍後重試。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={`${styles.console} ${styles.adminConsole}`} data-testid="operator-password-change">
      <header className={styles.adminHeader}>
        <div>
          <strong>變更密碼</strong>
          <span>首次登入請以新的個人密碼取代一次性初始密碼。</span>
        </div>
      </header>
      <main className={styles.adminMain}>
        <form className={styles.adminForm} onSubmit={submit}>
          <label>
            目前密碼
            <input
              autoComplete="current-password"
              onChange={(e) => setCurrentPassword(e.target.value)}
              required
              type="password"
              value={currentPassword}
            />
          </label>
          <label>
            新密碼
            <input
              autoComplete="new-password"
              minLength={12}
              onChange={(e) => setNewPassword(e.target.value)}
              required
              type="password"
              value={newPassword}
            />
          </label>
          <label>
            確認新密碼
            <input
              autoComplete="new-password"
              minLength={12}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              type="password"
              value={confirmPassword}
            />
          </label>
          {error ? (
            <p className={styles.adminNotice} role="alert">
              {error}
            </p>
          ) : null}
          <Button disabled={submitting} size="sm" type="submit" variant="secondary">
            {submitting ? "變更中…" : "變更密碼"}
          </Button>
        </form>
      </main>
    </div>
  );
}
