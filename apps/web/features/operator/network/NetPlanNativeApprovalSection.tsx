"use client";
import { useEffect, useRef, useState } from "react";
import { Button, Modal } from "@oday-plus/ui";
import styles from "../networkFindAreas.module.css";
import type { CanonicalBuilder, NativeDecision, NetPlanBuilderApi } from "./netplanBuilderClient";

export function NetPlanNativeApprovalSection({ storeId, builder, api, disabled, onRecorded }: {
  storeId: string; builder: CanonicalBuilder; api: NetPlanBuilderApi; disabled: boolean;
  onRecorded: (builder: CanonicalBuilder, audit?: string) => void;
}) {
  const [open, setOpen] = useState(false); const [reason, setReason] = useState("");
  const [receipt, setReceipt] = useState(""); const [decision, setDecision] = useState<NativeDecision["decision"]>("approved");
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const context = JSON.stringify([storeId, builder.scenarioId, builder.problemHash, builder.status]);
  const lease = useRef({ context, api, active: true });
  if (lease.current.context !== context || lease.current.api !== api) {
    lease.current.active = false; lease.current = { context, api, active: true };
  }
  const retry = useRef<{ fingerprint: string; key: string } | null>(null);
  useEffect(() => { setOpen(false); setReason(""); setReceipt(""); setDecision("approved"); setBusy(false); setError(""); retry.current = null; }, [context, api]);
  useEffect(() => { if (!lease.current.active) lease.current = { context, api, active: true };
    const captured = lease.current; return () => { captured.active = false; }; }, [context, api]);
  if (builder.status !== "pending_approval") return null;
  const permitted = builder.lifecycle?.canApprove === true && Boolean(api.decide) && !disabled;
  const complete = Boolean(reason.trim()) && (decision !== "approved" || Boolean(receipt.trim()));
  async function submit() {
    if (!permitted || busy || !complete || !api.decide) return;
    const intent = { decision, approvalReceiptId: receipt.trim() };
    const fingerprint = JSON.stringify([context, reason.trim(), intent]);
    if (retry.current?.fingerprint !== fingerprint) retry.current = { fingerprint, key: crypto.randomUUID() };
    const captured = lease.current; const current = () => captured.active && captured === lease.current;
    setBusy(true); setError("");
    try {
      const result = await api.decide(storeId, builder, reason.trim(), retry.current.key, intent);
      if (!current()) return;
      if (result.ok) { retry.current = null; setOpen(false); onRecorded(result.builder, result.auditEventId); }
      else if (!("superseded" in result)) setError(result.message);
    } catch { if (current()) setError("核准結果暫時無法確認，請保留輸入後重試。"); }
    finally { if (current()) setBusy(false); }
  }
  return <section className={styles.rebalanceNetPlanBlock} aria-label="原生方案核准">
    <h4>方案核准</h4><p>核准會驗證原始管理核准回執、選定方案與限制揭露；此畫面不產生核准憑證。</p>
    <Button type="button" disabled={!permitted || busy} onClick={() => setOpen(true)}>登錄方案決定</Button>
    {!builder.lifecycle?.canApprove ? <p>目前帳號沒有此情境的核准權限。</p> : null}
    <Modal key={context} open={open} title="登錄方案決定" closeDisabled={busy}
      onClose={() => { if (!busy) setOpen(false); }} primaryAction={{ id: "record-native-netplan-decision", label: "確認送出",
        loading: busy, permitted: permitted && complete && !busy, onSelect: () => { void submit(); } }}>
      <p>情境 {builder.scenarioId}</p><p>即將影響的實體：</p>
      <ul>{Object.keys(builder.optionsByEntity).map(id => <li key={id}>{id}</li>)}</ul>
      <label className="odp-field"><span>決定</span><select className={styles.inputField} value={decision} disabled={busy}
        onChange={event => setDecision(event.target.value as NativeDecision["decision"])}>
        <option value="approved">核准</option><option value="rejected">駁回</option></select></label>
      <label className="odp-field"><span>原始管理核准回執識別</span><input className={styles.inputField} value={receipt}
        onChange={event => setReceipt(event.target.value)} disabled={busy} /></label>
      <label className="odp-field"><span>決定理由</span><textarea className={styles.textareaField} value={reason}
        onChange={event => setReason(event.target.value)} disabled={busy} /></label>
      {error ? <p role="alert">{error}</p> : null}
    </Modal>
  </section>;
}
