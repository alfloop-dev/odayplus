"use client";
import { useEffect, useRef, useState } from "react";
import { Button } from "@oday-plus/ui";
import { NetPlanLifecycleSection } from "./NetPlanLifecycleSection";
import { NetPlanNativeApprovalSection } from "./NetPlanNativeApprovalSection";
import { NetPlanOutcomeTrackingSection } from "./NetPlanOutcomeTrackingSection";
import styles from "../networkFindAreas.module.css";
import { NETPLAN_ACTIONS, type BuilderConstraints, type CanonicalBuilder, type NetPlanBuilderApi } from "./netplanBuilderClient";

const fields = [
  ["max_budget", "預算上限"], ["min_expected_gross_margin", "最低期望毛利"],
  ["min_capacity_delta", "最低產能變化"], ["max_average_risk", "平均風險上限"],
] as const;
function formFor(builder: CanonicalBuilder): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [key] of fields) result[key] = builder.constraints[key]?.toString() ?? "";
  for (const action of NETPLAN_ACTIONS) {
    result[`min_${action}`] = builder.constraints.min_action_counts[action]?.toString() ?? "";
    result[`max_${action}`] = builder.constraints.max_action_counts[action]?.toString() ?? "";
  }
  return result;
}
export function NetPlanScenarioBuilder({ storeId, api, onDirtyChange }: {
  storeId: string; api: NetPlanBuilderApi; onDirtyChange: (dirty: boolean) => void;
}) {
  const [builder, setBuilder] = useState<CanonicalBuilder | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [readAttempt, setReadAttempt] = useState(0);
  const [auditId, setAuditId] = useState<string | null>(null);
  const lease = useRef(0);
  const retry = useRef<{ fingerprint: string; key: string } | null>(null);
  const dirty = !!builder && JSON.stringify(form) !== JSON.stringify(formFor(builder));
  const dirtyCallback = useRef(onDirtyChange); dirtyCallback.current = onDirtyChange;
  useEffect(() => { dirtyCallback.current(dirty || busy); }, [dirty, busy]);
  useEffect(() => {
    const sequence = ++lease.current; setBuilder(null); setError(null); setReason(""); setAuditId(null);
    setBusy(true);
    void api.read(storeId).then(result => {
      if (sequence !== lease.current) return;
      if (result.ok) { setBuilder(result.builder); setForm(formFor(result.builder)); }
      else if (!("superseded" in result)) setError(result.message);
    }).finally(() => { if (sequence === lease.current) setBusy(false); });
    return () => { ++lease.current; dirtyCallback.current(false); };
  }, [api, storeId, readAttempt]);

  function updateField(key: string, value: string) {
    dirtyCallback.current(true); setForm(previous => ({ ...previous, [key]: value }));
  }

  async function command(operation: "save" | "solve") {
    if (!builder || busy || !reason.trim() || (operation === "solve" && dirty)) return;
    const constraints: BuilderConstraints = { max_budget: Number(form.max_budget),
      min_expected_gross_margin: form.min_expected_gross_margin.trim() ? Number(form.min_expected_gross_margin) : null,
      min_capacity_delta: form.min_capacity_delta.trim() ? Number(form.min_capacity_delta) : null,
      max_average_risk: form.max_average_risk.trim() ? Number(form.max_average_risk) : null,
      min_action_counts: {}, max_action_counts: {} };
    if (!form.max_budget.trim() || fields.some(([key]) => constraints[key] !== null && !Number.isFinite(constraints[key]))) {
      setError("請填入有限數值；預算不可空白。"); return;
    }
    for (const action of NETPLAN_ACTIONS) for (const side of ["min", "max"] as const) {
      const value = form[`${side}_${action}`]?.trim();
      if (value) {
        const count = Number(value);
        if (!Number.isInteger(count) || count < 0) { setError("動作數量必須為非負整數。"); return; }
        constraints[`${side}_action_counts`][action] = count;
      }
    }
    const fingerprint = JSON.stringify({ operation, id: builder.scenarioId, hash: builder.problemHash,
      reason: reason.trim(), ...(operation === "save" ? { constraints } : {}) });
    if (retry.current?.fingerprint !== fingerprint) retry.current = { fingerprint, key: crypto.randomUUID() };
    const sequence = lease.current; setBusy(true); setError(null);
    try {
      const result = await api.command(storeId, operation, builder, reason.trim(), retry.current.key, constraints);
      if (sequence !== lease.current) return;
      if (result.ok) {
        setBuilder(result.builder); setForm(formFor(result.builder)); setAuditId(result.auditEventId ?? null);
        setReason(""); retry.current = null;
      } else if (!("superseded" in result)) setError(result.message);
    } finally { if (sequence === lease.current) setBusy(false); }
  }
  return <section className={styles.rebalanceNetPlanBlock} aria-label="NetPlan 情境建構" data-testid="netplan-scenario-builder">
    <h4>情境建構與限制</h4>
    {busy ? <p role="status">NetPlan 處理中…</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    <Button type="button" disabled={busy} onClick={() => setReadAttempt(value => value + 1)}>重新讀取情境（取代未儲存輸入）</Button>
    {!builder ? <p>尚未取得情境資料，請重新讀取。</p> : <>
      <p>{builder.scenarioName} · {builder.planningHorizon} · {builder.status}</p>
      <details><summary>狀態歷程</summary><ul>{builder.statusHistory.map((h, i) =>
        <li key={i}>{h.from_status} → {h.to_status} · {h.actor} · {h.reason} · {h.occurred_at}</li>)}</ul></details>
      {dirty ? <p role="status">限制已修改；舊解不可選取或送審。請先儲存，再重新解算。</p> : null}
      <form className="odp-form" onSubmit={event => { event.preventDefault(); void command("save"); }}>
        <section className="odp-form__affected" aria-label="即將影響的實體">
          <h5>即將影響的實體</h5>
          <ul>{Object.keys(builder.optionsByEntity).map(entityId => <li key={entityId}><code>{entityId}</code></li>)}</ul>
        </section>
        <fieldset className="odp-form" disabled={busy || !builder.editable}>
          <legend>情境限制</legend>
          {fields.map(([key, label]) => <label className="odp-field" key={key}><span className={styles.reviewDialogFieldLabel}>{label}</span>
            <input className={styles.inputField} aria-label={label} inputMode="decimal"
              value={form[key] ?? ""} onChange={e => updateField(key, e.target.value)} /></label>)}
          {NETPLAN_ACTIONS.map(action => <div className={styles.reviewDialogField} key={action}><strong>{action}</strong>{(["min", "max"] as const).map(side =>
            <label className="odp-field" key={side}><span className={styles.reviewDialogFieldLabel}>{side === "min" ? "最少" : "最多"}</span>
              <input className={styles.inputField} aria-label={`${action} ${side}`} inputMode="numeric"
                value={form[`${side}_${action}`] ?? ""} onChange={e => updateField(`${side}_${action}`, e.target.value)} /></label>)}</div>)}
          <label className="odp-field"><span className={styles.reviewDialogFieldLabel}>變更／解算理由</span>
            <textarea className={styles.textareaField} value={reason} onChange={e => setReason(e.target.value)} /></label>
          <div className="odp-actions">
            <Button disabled={!reason.trim() || !dirty} type="submit" variant="primary">{builder.requiresNewDraft ? "建立新草稿" : "儲存限制"}</Button>
            <Button disabled={!reason.trim() || dirty || !builder.canSolve || builder.status !== "draft"} type="button" onClick={() => void command("solve")}>重新解算</Button>
          </div>
        </fieldset>
      </form>
      {!builder.editable ? <p>此情境已進入核准或執行流程；建構欄位唯讀。</p> : null}
      {!builder.policyAvailable ? <p>目前治理政策未取得；送審仍受原治理守門，不能由此核准。</p> : <p>治理政策：{builder.disclosurePolicyVersionId}</p>}
      <h5>目前正式來源的動作選項（唯讀）</h5>
      {Object.entries(builder.optionsByEntity).map(([entity, options]) => <div key={entity}><strong>{entity}</strong><ul>
        {options.map((option, index) => <li key={`${option.action}-${index}`}>{option.action} · 期望毛利 {option.expected_gross_margin}
          {` · 成本 ${option.budget_cost} · 風險 ${option.risk_score} · 產能 ${option.capacity_delta}`}
          <span>{option.notes.join("；")}</span><small>來源：{option.source_snapshot_ids.join(", ") || "未提供來源識別"}</small></li>)}
      </ul></div>)}
      <NetPlanLifecycleSection key={builder.scenarioId} storeId={storeId} builder={builder} api={api}
        onRecorded={(next, audit) => { setBuilder(next); setForm(formFor(next)); setAuditId(audit ?? null); }} />
      <NetPlanNativeApprovalSection storeId={storeId} builder={builder} api={api} disabled={dirty || busy}
        onRecorded={(next, audit) => { setBuilder(next); setForm(formFor(next)); setAuditId(audit ?? null); }} />
      <NetPlanOutcomeTrackingSection tracking={builder.outcomeTracking} />
      <p>模型 {builder.modelVersion} · 特徵 {builder.featureVersion} · 解算器 {builder.solverVersion} · 政策 {builder.policyVersion}</p>
      {auditId ? <p role="status">命令已記錄；稽核 {auditId}。外部封存狀態須另查實際回執。</p> : null}
    </>}
  </section>;
}
