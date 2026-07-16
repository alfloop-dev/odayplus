"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export function ClientCreateCaseButton({
  currentUser,
}: {
  currentUser?: { subjectId: string; roles: string };
}) {
  const router = useRouter();
  const [isOpen, setIsOpen] = useState(false);
  const [storeId, setStoreId] = useState("");
  const [gmTtm, setGmTtm] = useState("3200000");
  const [forecastGmNext12m, setForecastGmNext12m] = useState("3400000");
  const [assetBookValue, setAssetBookValue] = useState("5000000");
  const [equipmentFairValue, setEquipmentFairValue] = useState("1800000");
  const [leaseLiability, setLeaseLiability] = useState("600000");
  const [workingCapital, setWorkingCapital] = useState("400000");
  const [comparableMultiples, setComparableMultiples] = useState("3.1, 3.5, 4.0");

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [idempotencyKey, setIdempotencyKey] = useState("");

  const handleOpen = () => {
    setIsOpen(true);
    setStoreId("");
    setGmTtm("3200000");
    setForecastGmNext12m("3400000");
    setAssetBookValue("5000000");
    setEquipmentFairValue("1800000");
    setLeaseLiability("600000");
    setWorkingCapital("400000");
    setComparableMultiples("3.1, 3.5, 4.0");
    setError(null);
    setSuccess(false);
    setIdempotencyKey(`idem-create-case-${Math.random().toString(36).substring(2, 9)}`);
  };

  const handleClose = () => {
    if (!submitting) {
      setIsOpen(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!storeId || storeId.trim().length === 0) {
      setError("門市代碼（store_id）為必填欄位。");
      return;
    }

    const ttm = Number(gmTtm);
    const fwd = Number(forecastGmNext12m);
    const asset = Number(assetBookValue);
    const equip = Number(equipmentFairValue);
    const lease = Number(leaseLiability);
    const wc = Number(workingCapital);
    const multiples = comparableMultiples
      .split(",")
      .map((s) => Number(s.trim()))
      .filter((n) => !isNaN(n));

    if (
      isNaN(ttm) ||
      isNaN(fwd) ||
      isNaN(asset) ||
      isNaN(equip) ||
      isNaN(lease) ||
      isNaN(wc) ||
      multiples.length === 0
    ) {
      setError("所有財務欄位與可比倍數必須為有效的數字。");
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      const response = await fetch(`/avm/cases`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-correlation-id": `corr-create-case-${Date.now()}`,
          "x-subject-id": currentUser?.subjectId || "product-ui-analyst",
          "x-roles": currentUser?.roles || "analyst,finance",
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify({
          store_id: storeId,
          gm_ttm: ttm,
          forecast_gm_next_12m: fwd,
          asset_book_value: asset,
          equipment_fair_value: equip,
          lease_liability: lease,
          working_capital: wc,
          comparable_multiples: multiples,
          created_by: currentUser?.subjectId || "finance-analyst-01",
          idempotency_key: idempotencyKey,
        }),
      });

      if (!response.ok) {
        throw new Error(`API error: ${response.status} ${response.statusText}`);
      }

      setSuccess(true);
      setTimeout(() => {
        setIsOpen(false);
        router.refresh();
      }, 1000);
    } catch (err: any) {
      // User input survives retries, storeId and financial states are preserved
      setError(err?.message || "建立失敗，請重試。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <button
        onClick={handleOpen}
        className="primary-button"
        style={{
          padding: "6px 12px",
          backgroundColor: "#0066cc",
          color: "#fff",
          border: "none",
          borderRadius: "4px",
          cursor: "pointer",
          fontSize: "14px",
          fontWeight: "bold",
        }}
        type="button"
        data-testid="header-create-case-btn"
      >
        建立估值案件
      </button>

      {isOpen && (
        <div
          data-testid="create-case-dialog"
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: "rgba(0, 0, 0, 0.5)",
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            zIndex: 9999,
          }}
        >
          <div
            style={{
              backgroundColor: "#fff",
              padding: "24px",
              borderRadius: "8px",
              width: "520px",
              boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
              display: "flex",
              flexDirection: "column",
              gap: "16px",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <h3 style={{ margin: 0, fontSize: "18px" }}>建立全新估值案件</h3>
              <button
                onClick={handleClose}
                disabled={submitting}
                style={{
                  background: "none",
                  border: "none",
                  fontSize: "18px",
                  cursor: submitting ? "not-allowed" : "pointer",
                }}
                type="button"
                aria-label="關閉"
              >
                ×
              </button>
            </div>

            <form
              onSubmit={handleSubmit}
              style={{ display: "flex", flexDirection: "column", gap: "16px" }}
            >
              {error && (
                <div
                  data-testid="create-case-error"
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
                  data-testid="create-case-success"
                  style={{
                    padding: "8px",
                    backgroundColor: "#f0fff0",
                    color: "#2a702a",
                    borderRadius: "4px",
                    fontSize: "13px",
                    border: "1px solid #c2f8c2",
                  }}
                >
                  ✓ 建立成功！正在更新列表...
                </div>
              )}

              <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                <label htmlFor="store-id" style={{ fontSize: "14px", fontWeight: "bold" }}>
                  門市代碼（store_id）
                </label>
                <input
                  id="store-id"
                  type="text"
                  value={storeId}
                  onChange={(e) => setStoreId(e.target.value)}
                  disabled={submitting || success}
                  placeholder="例如 store-099"
                  style={{
                    padding: "8px",
                    border: "1px solid #ccc",
                    borderRadius: "4px",
                    fontSize: "14px",
                  }}
                  data-testid="create-case-store-id-input"
                />
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                  <label htmlFor="gm-ttm" style={{ fontSize: "12px", fontWeight: "bold", color: "#555" }}>
                    GM TTM (元)
                  </label>
                  <input
                    id="gm-ttm"
                    type="number"
                    value={gmTtm}
                    onChange={(e) => setGmTtm(e.target.value)}
                    disabled={submitting || success}
                    style={{
                      padding: "8px",
                      border: "1px solid #ccc",
                      borderRadius: "4px",
                      fontSize: "14px",
                    }}
                  />
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                  <label htmlFor="forecast-gm" style={{ fontSize: "12px", fontWeight: "bold", color: "#555" }}>
                    Forecast GM 12M (元)
                  </label>
                  <input
                    id="forecast-gm"
                    type="number"
                    value={forecastGmNext12m}
                    onChange={(e) => setForecastGmNext12m(e.target.value)}
                    disabled={submitting || success}
                    style={{
                      padding: "8px",
                      border: "1px solid #ccc",
                      borderRadius: "4px",
                      fontSize: "14px",
                    }}
                  />
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                  <label htmlFor="asset-book" style={{ fontSize: "12px", fontWeight: "bold", color: "#555" }}>
                    資產帳面價值 (元)
                  </label>
                  <input
                    id="asset-book"
                    type="number"
                    value={assetBookValue}
                    onChange={(e) => setAssetBookValue(e.target.value)}
                    disabled={submitting || success}
                    style={{
                      padding: "8px",
                      border: "1px solid #ccc",
                      borderRadius: "4px",
                      fontSize: "14px",
                    }}
                  />
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                  <label htmlFor="equipment-fair" style={{ fontSize: "12px", fontWeight: "bold", color: "#555" }}>
                    設備公允價值 (元)
                  </label>
                  <input
                    id="equipment-fair"
                    type="number"
                    value={equipmentFairValue}
                    onChange={(e) => setEquipmentFairValue(e.target.value)}
                    disabled={submitting || success}
                    style={{
                      padding: "8px",
                      border: "1px solid #ccc",
                      borderRadius: "4px",
                      fontSize: "14px",
                    }}
                  />
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                  <label htmlFor="lease-liability" style={{ fontSize: "12px", fontWeight: "bold", color: "#555" }}>
                    租賃負債 (元)
                  </label>
                  <input
                    id="lease-liability"
                    type="number"
                    value={leaseLiability}
                    onChange={(e) => setLeaseLiability(e.target.value)}
                    disabled={submitting || success}
                    style={{
                      padding: "8px",
                      border: "1px solid #ccc",
                      borderRadius: "4px",
                      fontSize: "14px",
                    }}
                  />
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                  <label htmlFor="working-capital" style={{ fontSize: "12px", fontWeight: "bold", color: "#555" }}>
                    營運資金 (元)
                  </label>
                  <input
                    id="working-capital"
                    type="number"
                    value={workingCapital}
                    onChange={(e) => setWorkingCapital(e.target.value)}
                    disabled={submitting || success}
                    style={{
                      padding: "8px",
                      border: "1px solid #ccc",
                      borderRadius: "4px",
                      fontSize: "14px",
                    }}
                  />
                </div>
              </div>

              <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                <label htmlFor="comparable-multiples" style={{ fontSize: "12px", fontWeight: "bold", color: "#555" }}>
                  可比倍數 (以逗號分隔，例如 3.1, 3.5, 4.0)
                </label>
                <input
                  id="comparable-multiples"
                  type="text"
                  value={comparableMultiples}
                  onChange={(e) => setComparableMultiples(e.target.value)}
                  disabled={submitting || success}
                  style={{
                    padding: "8px",
                    border: "1px solid #ccc",
                    borderRadius: "4px",
                    fontSize: "14px",
                  }}
                />
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
                <button
                  type="button"
                  onClick={handleClose}
                  disabled={submitting}
                  style={{
                    padding: "6px 12px",
                    backgroundColor: "#eee",
                    color: "#333",
                    border: "none",
                    borderRadius: "4px",
                    cursor: submitting ? "not-allowed" : "pointer",
                  }}
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={submitting || success}
                  style={{
                    padding: "6px 12px",
                    backgroundColor: "#0066cc",
                    color: "#fff",
                    border: "none",
                    borderRadius: "4px",
                    cursor: submitting || success ? "not-allowed" : "pointer",
                  }}
                  data-testid="create-case-submit-btn"
                >
                  {submitting ? "建立中 (In Flight)..." : "確定建立"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}
