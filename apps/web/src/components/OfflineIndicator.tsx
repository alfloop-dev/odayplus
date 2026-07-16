"use client";

import { useEffect, useState } from "react";

export function OfflineIndicator() {
  const [isOffline, setIsOffline] = useState(false);

  useEffect(() => {
    if (typeof window !== "undefined") {
      setIsOffline(!window.navigator.onLine);
      const onOnline = () => setIsOffline(false);
      const onOffline = () => setIsOffline(true);
      window.addEventListener("online", onOnline);
      window.addEventListener("offline", onOffline);
      return () => {
        window.removeEventListener("online", onOnline);
        window.removeEventListener("offline", onOffline);
      };
    }
  }, []);

  if (!isOffline) return null;

  return (
    <div
      data-testid="offline-indicator"
      style={{
        padding: "8px 12px",
        backgroundColor: "#fff0f0",
        color: "#d93838",
        border: "1px solid #f8c2c2",
        borderRadius: "4px",
        marginBottom: "12px",
        fontSize: "14px",
        display: "flex",
        alignItems: "center",
        gap: "8px",
      }}
    >
      <span aria-hidden="true">⚠️</span>
      <span>[OFFLINE] 網路連線已中斷，改用離線模式。</span>
    </div>
  );
}
