"use client";

import { useEffect, useState, type ReactNode } from "react";
import { fetchFeatureFlags, type FeatureFlagDto } from "../../../features/operator/featureFlagsAdapter";

export type FeatureFlagGuardProps = {
  flagKey: string;
  children: ReactNode;
  fallback?: ReactNode;
  baseUrl?: string;
};

export function FeatureFlagGuard({
  flagKey,
  children,
  fallback,
  baseUrl = "",
}: FeatureFlagGuardProps) {
  const [enabled, setEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    let isMounted = true;
    fetchFeatureFlags(baseUrl)
      .then((flags) => {
        if (!isMounted) return;
        const flag = flags.find((f) => f.key === flagKey);
        setEnabled(flag ? flag.is_active : false);
      })
      .catch(() => {
        if (!isMounted) return;
        setEnabled(false);
      });
    return () => {
      isMounted = false;
    };
  }, [flagKey, baseUrl]);

  if (enabled === null) {
    return <div style={{ color: "#9ca3af", fontSize: "0.85rem" }}>驗證 Feature Flag 政策中...</div>;
  }

  if (!enabled) {
    if (fallback) return <>{fallback}</>;
    return (
      <div
        style={{
          background: "rgba(239, 68, 68, 0.1)",
          border: "1px solid rgba(239, 68, 68, 0.3)",
          color: "#f87171",
          padding: "1rem",
          borderRadius: "8px",
          margin: "0.5rem 0",
          fontSize: "0.875rem",
        }}
      >
        🛑 <strong>功能已停用 (Feature Disabled)</strong>
        <p style={{ margin: "0.25rem 0 0 0", color: "#fca5a5" }}>
          Feature flag <code>{flagKey}</code> 目前處於停用狀態 (Kill-Switch 已開啟)。依據 FR-SHARED-004 規範，此功能暫停執行。
        </p>
      </div>
    );
  }

  return <>{children}</>;
}
