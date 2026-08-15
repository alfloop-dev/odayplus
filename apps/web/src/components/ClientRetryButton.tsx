"use client";

import { useRouter } from "next/navigation";
import { useTransition } from "react";

export function ClientRetryButton() {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();

  return (
    <button
      onClick={() => {
        startTransition(() => {
          router.refresh();
        });
      }}
      disabled={isPending}
      className="retry-button"
      style={{
        marginLeft: "10px",
        padding: "2px 8px",
        fontSize: "12px",
        cursor: "pointer",
        border: "1px solid #ccc",
        borderRadius: "4px",
        background: isPending ? "#eee" : "#fff",
        color: "#333",
      }}
      type="button"
      data-testid="client-retry-button"
    >
      {isPending ? "Loading..." : "重試 (Retry)"}
    </button>
  );
}
