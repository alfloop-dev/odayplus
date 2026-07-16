"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

type ClientApprovalFormProps = {
  caseId: string;
  canApprove: boolean;
  formattedReservePrice: string;
  currentUser?: { subjectId: string; roles: string };
};

export function ClientApprovalForm({
  caseId,
  canApprove,
  formattedReservePrice,
  currentUser,
}: ClientApprovalFormProps) {
  const router = useRouter();
  const [reason, setReason] = useState("");
  const [reserveOverride, setReserveOverride] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [idempotencyKey] = useState(
    () => `idem-approval-${caseId}-${Math.random().toString(36).substring(2, 9)}`
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!reason || reason.trim().length < 10) {
      setError("核准理由（decision_reason）為必填且至少 10 字。");
      return;
    }

    setSubmitting(true);
    setError(null);

    const apiBase =
      process.env.NEXT_PUBLIC_ODP_API_BASE_URL || "http://127.0.0.1:8099";

    try {
      const response = await fetch(
        `${apiBase}/api/v1/operator/approvals/${caseId}/decision`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "x-correlation-id": `corr-approval-${caseId}-${Date.now()}`,
            "Idempotency-Key": idempotencyKey,
          },
          body: JSON.stringify({
            status: "APPROVED",
            reason: `${reserveOverride ? "[Override] " : ""}${reason}`,
            actorRoleId: currentUser?.roles || "finance-lead",
            actorName: currentUser?.subjectId || "finance-lead-02",
          }),
        }
      );

      if (!response.ok) {
        throw new Error(`API error: ${response.status} ${response.statusText}`);
      }

      setSuccess(true);
      // Wait for server confirmation, stay visible for a moment
      setTimeout(() => {
        router.refresh();
      }, 1000);
    } catch (err: any) {
      // User input survives retries, states reason & reserveOverride are not reset
      setError(err?.message || "提交失敗，請重試。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      {error && (
        <div
          data-testid="approval-form-error"
          style={{
            padding: "8px",
            backgroundColor: "#fff0f0",
            color: "#d93838",
            borderRadius: "4px",
            fontSize: "13px",
            border: "1px solid #f8c2c2",
          }}
        >
          {error}
        </div>
      )}

      {success && (
        <div
          data-testid="approval-form-success"
          style={{
            padding: "8px",
            backgroundColor: "#f0fff0",
            color: "#2a702a",
            borderRadius: "4px",
            fontSize: "13px",
            border: "1px solid #c2f8c2",
          }}
        >
          ✓ 核准成功！正在整理頁面...
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
        <label style={{ fontSize: "13px", fontWeight: "bold" }}>
          系統 reserve_price（P10·0.97）
        </label>
        <input
          value={formattedReservePrice}
          readOnly
          aria-label="masked reserve price"
          style={{
            padding: "6px 8px",
            backgroundColor: "#f5f5f5",
            border: "1px solid #ccc",
            borderRadius: "4px",
            color: "#666",
          }}
        />
      </div>

      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          fontSize: "13px",
          cursor: "pointer",
        }}
      >
        <input
          type="checkbox"
          name="reserveOverride"
          checked={reserveOverride}
          onChange={(e) => setReserveOverride(e.target.checked)}
          disabled={submitting || success}
        />
        reserve override（覆寫須填 reason，標示與原值差）
      </label>

      <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
        <label style={{ fontSize: "13px", fontWeight: "bold" }}>
          decision_reason（必填）
        </label>
        <textarea
          name="reason"
          minLength={10}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          disabled={submitting || success}
          placeholder="核准理由，至少 10 字"
          style={{
            padding: "8px",
            minHeight: "80px",
            border: "1px solid #ccc",
            borderRadius: "4px",
            fontSize: "13px",
            fontFamily: "inherit",
          }}
        />
      </div>

      <button
        data-testid="approval-submit-button"
        disabled={!canApprove || submitting || success}
        type="submit"
        style={{
          padding: "8px 16px",
          backgroundColor: canApprove ? "#0066cc" : "#ccc",
          color: "#fff",
          border: "none",
          borderRadius: "4px",
          cursor: canApprove && !submitting && !success ? "pointer" : "not-allowed",
          fontSize: "14px",
          fontWeight: "bold",
          transition: "background-color 0.2s",
        }}
      >
        {submitting ? "提交中 (In Flight)..." : canApprove ? "財務核准此案" : "需先到 REVIEW_REQUIRED"}
      </button>
    </form>
  );
}
