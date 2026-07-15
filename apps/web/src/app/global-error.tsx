"use client";

/**
 * Global error boundary (ODP-PGAP-SHELL-001, acceptance §7).
 *
 * Catches failures in the root layout itself, which route-level error.tsx
 * cannot. Next replaces the whole document here, so this file must render its
 * own <html>/<body> and cannot rely on the shell's provider or CSS modules
 * being mounted — hence the inline, token-free styling. This is the only place
 * in the shell where literal styles are correct: by definition the layout that
 * loads the tokens is what failed.
 */
import { useEffect } from "react";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("shell global error", { digest: error.digest, message: error.message });
  }, [error]);

  return (
    <html lang="zh-Hant">
      <body style={{ fontFamily: "system-ui, sans-serif", margin: 0, padding: "2rem" }}>
        <main
          data-testid="global-error"
          data-state="error"
          role="alert"
          style={{ maxWidth: "40rem", margin: "0 auto" }}
        >
          <h1 style={{ fontSize: "1.25rem" }}>OpsBoard 無法載入</h1>
          <p>平台發生未預期的錯誤，畫面無法顯示。目前沒有任何資料被寫入或變更。</p>
          <p>請重試；若持續發生，請附上下方代碼通報平台維運。</p>
          {error.digest ? (
            <p>
              代碼：<code data-testid="global-error-digest">{error.digest}</code>
            </p>
          ) : null}
          <button
            type="button"
            onClick={() => reset()}
            data-testid="global-error-retry"
            style={{ minHeight: "2.75rem", padding: "0 1rem", cursor: "pointer" }}
          >
            重試
          </button>
        </main>
      </body>
    </html>
  );
}
