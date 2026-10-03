"use client";

/**
 * Administration-only Operator surface (ODP-DEV-ADMIN-RELEASE-READINESS-001).
 *
 * The business Operator Console gates every workspace on
 * `/api/v1/operator/bootstrap` (operator_console:view). The first account the
 * identity bootstrap creates holds only `platform_admin`, which is granted
 * user/role/feature-flag administration but deliberately no business read, so
 * it could sign in and then reach nothing. This page needs only `user:view`:
 * it hosts the identity-backed user & role administration and a real sign-out.
 */

import React, { useCallback, useEffect, useState } from "react";
import { Button } from "./components";
import styles from "./operator.module.css";
import {
  PASSWORD_CHANGE_PATH,
  classifyAccessDenial,
  signOutOperator,
  type OperatorAccessDenial,
} from "./operatorSession";
import { UserRoleManagementController } from "./UserRoleManagementController";

type AccessState = "checking" | "ready" | OperatorAccessDenial | "error";

const ACCESS_MESSAGES: Record<Exclude<AccessState, "checking" | "ready">, string> = {
  password_change_required: "首次登入必須先變更一次性初始密碼，才能使用管理功能。",
  forbidden: "此帳號沒有使用者與角色管理權限（需要 platform_admin）。",
  unauthenticated: "登入已失效，請重新登入。",
  error: "無法連線至管理 API，請稍後重試。",
};

export function OperatorAdminConsole() {
  const [access, setAccess] = useState<AccessState>("checking");
  const [signOutError, setSignOutError] = useState<string | null>(null);

  const probe = useCallback(async () => {
    setAccess("checking");
    try {
      const res = await fetch("/api/v1/operator/users", {
        credentials: "same-origin",
        headers: { accept: "application/json" },
      });
      if (res.ok) {
        setAccess("ready");
        return;
      }
      setAccess((await classifyAccessDenial(res)) ?? "error");
    } catch {
      setAccess("error");
    }
  }, []);

  useEffect(() => {
    void probe();
  }, [probe]);

  const handleSignOut = async () => {
    setSignOutError(null);
    const result = await signOutOperator();
    if (!result.ok) {
      setSignOutError(`登出失敗：session 尚未撤銷（${result.status ?? "network error"}），請重試。`);
    }
  };

  return (
    <div className={`${styles.console} ${styles.adminConsole}`} data-testid="operator-admin-console">
      <header className={styles.adminHeader}>
        <div>
          <strong>Oday Plus 管理後台</strong>
          <span>使用者、角色與帳號狀態（以 identity 權威資料為準）</span>
        </div>
        <div className={styles.topActions}>
          <a className={styles.adminLink} href="/operator">
            營運主控台
          </a>
          <Button onClick={handleSignOut} size="sm" variant="ghost">
            Logout
          </Button>
        </div>
      </header>
      {signOutError ? (
        <p className={styles.adminNotice} role="alert">
          {signOutError}
        </p>
      ) : null}
      <main className={styles.adminMain}>
        {access === "checking" ? (
          <p className={styles.adminNotice} role="status">
            正在確認管理權限…
          </p>
        ) : access === "ready" ? (
          <UserRoleManagementController currentRoleId="platform-admin" />
        ) : (
          <section className={styles.adminNotice} data-access={access} role="alert">
            <p>{ACCESS_MESSAGES[access]}</p>
            {access === "password_change_required" ? (
              <a className={styles.adminLink} href={PASSWORD_CHANGE_PATH}>
                前往變更密碼
              </a>
            ) : access === "unauthenticated" ? (
              <a className={styles.adminLink} href="/login?returnTo=%2Foperator%3Fview%3Dadmin">
                重新登入
              </a>
            ) : (
              <Button onClick={() => void probe()} size="sm" variant="secondary">
                重新檢查
              </Button>
            )}
          </section>
        )}
      </main>
    </div>
  );
}
