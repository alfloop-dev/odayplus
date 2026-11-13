"use client";

import { useState } from "react";
import styles from "./intake.module.css";
import { ACTION_DENIED_NOTE } from "./intakePermissions";

export function AssistedEntryForm({
  busy,
  canEdit,
  onSave,
  native = false,
  locked = false,
}: {
  busy: boolean;
  canEdit: boolean;
  onSave: (input: {
    fields: Record<string, string>;
    riskSummary: string;
    riskAcknowledged: boolean;
    reason?: string;
  }) => void;
  native?: boolean;
  locked?: boolean;
}) {
  const [values, setValues] = useState({ address: "", rent: "", areaPing: "", floor: "", listingType: "", providerListingId: "", listingStatus: "" });
  const [reason, setReason] = useState("");
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const riskSummary =
    "人工補錄內容由操作者依來源頁自行輸入，不具本系統擷取證據，將用於後續比對；前後值、原因與操作者會寫入 Audit。";

  function update(key: keyof typeof values, value: string) {
    setValues((current) => ({ ...current, [key]: value }));
  }

  function handleSave() {
    if (busy || !canEdit || locked) return;
    if (!values.address.trim() || !values.rent.trim() || !values.areaPing.trim()) {
      setLocalError("人工補錄需至少填寫地址、租金、坪數。");
      return;
    }
    if (!riskAcknowledged) {
      setLocalError("請先確認你已了解人工補錄的風險。");
      return;
    }
    if (native && (reason.trim().length < 3 || ![Number(values.rent), Number(values.areaPing)].every((value) => Number.isFinite(value) && value > 0))) {
      setLocalError("請填寫至少三字的補錄原因，租金與坪數須為有效正數。");
      return;
    }
    setLocalError(null);
    onSave({
      fields: Object.fromEntries(Object.entries(values).filter(([, value]) => value.trim()).map(([key, value]) => [key, value.trim()])),
      riskSummary,
      riskAcknowledged,
      ...(native ? { reason: reason.trim() } : {}),
    });
  }

  return (
    <section className={styles.sectionBox} data-testid="intake-assisted-entry" aria-labelledby="assisted-entry-heading">
      <div className={styles.sectionHead} id="assisted-entry-heading">
        人工補錄 ASSISTED ENTRY — 僅輸入來源頁可見欄位；不得提供帳密、Cookie、Token 或私有端點
      </div>
      <div className={styles.grid2}>
        {([
          ["address", "地址（必填）", "例：新北市新莊區興德路 XX 號 1F"],
          ["rent", "租金（必填）", "45000"],
          ["areaPing", "坪數（必填）", "18"],
          ["floor", "樓層", "1F"],
          ["listingType", "型態／用途", "店面"],
          ...(native ? [["providerListingId", "來源物件編號", "來源頁提供的物件編號"], ["listingStatus", "來源物件狀態", "出租中"]] as const : []),
        ] as const).map(([key, label, placeholder]) => (
          <label className={styles.fieldLabel} key={key}>
            {label}
            <input
              aria-label={label}
              className={styles.input}
              data-testid={`assisted-${key === "areaPing" ? "area" : key === "listingType" ? "type" : key}`}
              disabled={!canEdit || busy || locked}
              maxLength={key === "address" ? 512 : 128}
              onChange={(event) => update(key, event.target.value)}
              placeholder={placeholder}
              value={values[key]}
            />
          </label>
        ))}
      </div>
      <div className={styles.riskSummaryText} data-testid="intake-assisted-risk-summary">{riskSummary}</div>
      <label className={styles.checkboxRow}>
        <input
          checked={riskAcknowledged}
          data-testid="assisted-risk-ack"
          disabled={!canEdit || busy || locked}
          onChange={(event) => setRiskAcknowledged(event.target.checked)}
          type="checkbox"
        />
        我已了解風險並確認補錄內容正確
      </label>
      {native ? <label className={styles.fieldLabel}>補錄原因<textarea className={styles.textarea} value={reason}
        disabled={!canEdit || busy || locked} maxLength={4000} onChange={(event) => setReason(event.target.value)} /></label> : null}
      {localError ? <div className={styles.errorText} data-testid="intake-assisted-error" role="alert">{localError}</div> : null}
      {!canEdit ? <div className={styles.warnNote}>{ACTION_DENIED_NOTE.correct}</div> : null}
      <button className={styles.primaryButton} data-testid="assisted-save" disabled={!canEdit || busy || locked} onClick={handleSave} type="button">
        {busy ? "儲存中…" : native ? "提交補錄提案" : "儲存並進入比對"}
      </button>
    </section>
  );
}
