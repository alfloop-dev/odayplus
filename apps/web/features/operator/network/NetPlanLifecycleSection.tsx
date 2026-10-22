"use client";
import { useEffect, useRef, useState } from "react";
import { Button, Modal } from "@oday-plus/ui";
import styles from "../networkFindAreas.module.css";
import { type CanonicalBuilder, type LifecycleOperation, type NetPlanBuilderApi, type RecordedOutcome } from "./netplanBuilderClient";

const transitions = { approved: ["execute", "登錄方案執行"], executed: ["outcome", "登錄實際結果"], outcome_observed: ["close", "結案"] } as const;
export function NetPlanLifecycleSection({ storeId, builder, api, onRecorded }: {
  storeId: string; builder: CanonicalBuilder; api: NetPlanBuilderApi;
  onRecorded: (builder: CanonicalBuilder, audit?: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [amount, setAmount] = useState("");
  const [snapshots, setSnapshots] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const context = JSON.stringify([storeId, builder.scenarioId, builder.problemHash, builder.status]);
  const lease = useRef({ context, api, active: true });
  if (lease.current.context !== context || lease.current.api !== api) { lease.current.active = false; lease.current = { context, api, active: true }; }
  const retry = useRef<{ fingerprint: string; key: string } | null>(null);
  useEffect(() => { setOpen(false); setBusy(false); setReason(""); setAmount(""); setSnapshots(""); setError(""); }, [context, api]);
  useEffect(() => { if (!lease.current.active) lease.current = { context, api, active: true };
    const current = lease.current; return () => { current.active = false; }; }, [context, api]);
  const currentTransition = transitions[builder.status as keyof typeof transitions];
  const lifecycle = builder.lifecycle;
  const outcome = lifecycle?.outcome;
  const execution = lifecycle?.execution;
  async function submit() {
    if (!currentTransition || !api.lifecycle || !lifecycle?.canExecute || busy || !reason.trim()) return;
    const operation: LifecycleOperation = currentTransition[0];
    let recorded: RecordedOutcome | undefined;
    if (operation === "outcome") {
      if (!amount.trim() || !Number.isFinite(Number(amount))) { setError("請填入有限的實際毛利數值。"); return; }
      recorded = { actualGrossMargin: Number(amount), sourceSnapshotIds: snapshots.split(/[,\n]/).map(value => value.trim()).filter(Boolean) };
    }
    const fingerprint = JSON.stringify([context, operation, reason.trim(), recorded]);
    if (retry.current?.fingerprint !== fingerprint) retry.current = { fingerprint, key: crypto.randomUUID() };
    const captured = lease.current;
    const current = () => captured.active && captured === lease.current;
    setBusy(true); setError("");
    try {
      const result = await api.lifecycle(storeId, operation, builder, reason.trim(), retry.current.key, recorded);
      if (!current()) return;
      if (result.ok) { retry.current = null; setOpen(false); onRecorded(result.builder, result.auditEventId); }
      else if (!("superseded" in result)) setError(result.message);
    } catch { if (current()) setError("執行紀錄暫時無法確認，請保留輸入後重試。"); }
    finally { if (current()) setBusy(false); }
  }
  return <section className={styles.rebalanceNetPlanBlock} aria-label="執行與結果">
    <h4>執行與結果</h4>
    {!lifecycle ? <p>尚未取得執行與結果紀錄。</p> : <>
      {execution ? <><h5>執行紀錄</h5><dl><dt>紀錄</dt><dd>{execution.execution_id}</dd>
        <dt>登錄人</dt><dd>{execution.executed_by}</dd><dt>登錄時間</dt><dd>{execution.executed_at}</dd></dl>
        <ul>{execution.actions.map((row, index) => <li key={`${row.entity_id}-${index}`}>{row.entity_id} · {row.action}</li>)}</ul></> : <p>尚無方案執行紀錄。</p>}
      {outcome ? <><h5>預期與實際比較</h5><dl><dt>預期毛利</dt><dd>{outcome.expected_gross_margin}</dd>
        <dt>已登錄實際毛利</dt><dd>{outcome.actual_gross_margin}</dd>
        <dt>差額</dt><dd>{outcome.variance > 0 ? "↑" : outcome.variance < 0 ? "↓" : "→"} {outcome.variance}</dd>
        <dt>差異比例</dt><dd>{(outcome.variance_pct * 100).toFixed(2)}%</dd>
        <dt>觀察時間</dt><dd>{outcome.observed_at}</dd><dt>填報來源識別</dt><dd>{outcome.source_snapshot_ids.join(", ") || "未填報"}</dd></dl>
        <p>以上為已保存的填報紀錄；外部標籤庫寫入狀態尚未提供。</p></>
        : <p>{builder.status === "executed" ? "待觀察結果" : "尚未登錄實際結果"}</p>}
      {builder.status === "closed" ? <p>此情境已結案，保留執行、結果與狀態歷程。</p> : currentTransition ? <>
        <Button type="button" disabled={!lifecycle.canExecute || !api.lifecycle || busy} onClick={() => setOpen(true)}>{currentTransition[1]}</Button>
        {!lifecycle.canExecute ? <p>目前帳號沒有此情境的執行權限。</p> : null}
      </> : null}
    </>}
    {currentTransition ? <Modal key={context} open={open} title={currentTransition[1]} closeDisabled={busy}
      onClose={() => { if (!busy) setOpen(false); }} primaryAction={{ id: "record-netplan-lifecycle", label: "確認送出", loading: busy,
        permitted: Boolean(reason.trim()) && !busy, onSelect: () => { void submit(); } }}>
      <p>情境 {builder.scenarioId} · {builder.status}</p><p>即將影響的實體：</p>
      <ul>{Object.keys(builder.optionsByEntity).map(id => <li key={id}>{id}</li>)}</ul>
      {currentTransition[0] === "execute" ? <p>確認原核准方案的執行登錄；後端會再次核對核准與功能旗標。</p> : null}
      {currentTransition[0] === "outcome" ? <><label className="odp-field"><span>實際毛利</span>
        <input className={styles.inputField} inputMode="decimal" value={amount} onChange={event => setAmount(event.target.value)} disabled={busy} /></label>
        <label className="odp-field"><span>填報來源識別（逗號或換行分隔）</span><textarea className={styles.textareaField} value={snapshots} onChange={event => setSnapshots(event.target.value)} disabled={busy} /></label>
        <p>請依實際紀錄填報；系統會計算與預期毛利的差額。</p></> : null}
      <label className="odp-field"><span>登錄理由</span><textarea className={styles.textareaField} value={reason} onChange={event => setReason(event.target.value)} disabled={busy} /></label>
      {error ? <p role="alert">{error}</p> : null}
    </Modal> : null}
  </section>;
}
