"use client";
import React, { useEffect, useRef, useState } from "react";
import type { PriceOpsRecommendation } from "./growthViewModel";
import { fetchPriceComparison, priceIntent, simulatePriceScenario, writePriceDecision,
  type PriceComparison, type PriceDecision, type PriceIntent, type PriceScenario,
  type PriceSimulation, type PriceWriteback } from "./priceOpsApi";
import g from "./growth.module.css";

function Bands({ value }: { value?: PriceSimulation }) {
  if (!value) return <p>尚未取得 API 模擬結果</p>;
  return <>
    <div>價格: <strong>{value.price}</strong></div>
    {([['demand', '預期需求'], ['revenue', '預期營收'], ['gross_margin', '預期毛利']] as const).map(([key, label]) =>
      <div key={key}>{label} (P10/P50/P90): <strong>{value[key].p10} / {value[key].p50} / {value[key].p90}</strong></div>)}
  </>;
}
export function PriceOpsWorkbench({ recommendation: rec, segmentName }: { recommendation: PriceOpsRecommendation; segmentName: string }) {
  const [comparison, setComparison] = useState<PriceComparison | null>(null);
  const [loading, setLoading] = useState(!!(rec.planId && rec.itemId));
  const [candidate, setCandidate] = useState(rec.candidatePrice.replace(/[^0-9.]/g, ""));
  const [scenario, setScenario] = useState<PriceScenario | null>(null);
  const [decision, setDecision] = useState<PriceDecision>("scenario_selected");
  const [reason, setReason] = useState("");
  const [record, setRecord] = useState<PriceWriteback | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const mounted = useRef(true);
  const simulationIntent = useRef<{ price: number; intent: PriceIntent } | null>(null);
  const writeIntent = useRef<{ signature: string; intent: PriceIntent } | null>(null);
  useEffect(() => {
    mounted.current = true;
    if (rec.planId && rec.itemId) {
      void fetchPriceComparison(rec.planId, rec.itemId).then(value => {
        if (!mounted.current) return;
        setComparison(value); setLoading(false);
        if (!value) setError("定價計畫或模擬資料未取得，請重新選取或重新載入。");
      });
    }
    return () => { mounted.current = false; };
  }, [rec.planId, rec.itemId]);
  const item = comparison?.items.find(item => item.item_id === rec.itemId);
  const simulated = scenario?.items.find(item => item.item_id === rec.itemId);
  const price = Number(candidate);
  const invalidPrice = !candidate.trim() || !Number.isFinite(price) || price <= 0;
  const hardBlocked = rec.constraintStatus === "HARD_CONSTRAINT_FAILED" || !comparison?.is_feasible;
  const canSimulate = !!comparison?.command_permissions.simulate && !invalidPrice && !busy;
  const matchesOptimizedPrices = !!scenario && scenario.items.every(row => comparison?.items.find(item => item.item_id === row.item_id)?.candidate_price === row.candidate_price);
  const canWrite = !!comparison?.command_permissions.writeback && !!scenario && !!reason.trim() && !busy && !record &&
    (decision === "rejected" || (!hardBlocked && scenario.is_feasible && simulated?.is_feasible)) &&
    (decision !== "approved" || (comparison.is_approvable && comparison.plan_status === "pending_approval" && matchesOptimizedPrices));
  async function simulate() {
    if (!canSimulate || pending.current || !rec.planId || !rec.itemId) return;
    pending.current = true; setBusy(true); setError(null); setRecord(null); setScenario(null);
    if (simulationIntent.current?.price !== price) simulationIntent.current = { price, intent: priceIntent() };
    const result = await simulatePriceScenario(rec.planId, rec.itemId, price, simulationIntent.current.intent);
    if (!mounted.current) return;
    pending.current = false; setBusy(false);
    if (result) { setScenario(result); writeIntent.current = null; }
    else setError("尚未確認情境模擬結果；重試會沿用本次請求識別。");
  }
  async function writeback() {
    if (!canWrite || pending.current || !rec.planId || !scenario || !comparison) return;
    const signature = JSON.stringify([scenario.scenario_id, decision, reason.trim()]);
    if (writeIntent.current?.signature !== signature) writeIntent.current = { signature, intent: priceIntent() };
    pending.current = true; setBusy(true); setError(null);
    const result = await writePriceDecision({ planId: rec.planId, actor: comparison.actor_id, decision,
      reason: reason.trim(), scenarioId: scenario.scenario_id, intent: writeIntent.current.intent });
    if (!mounted.current) return;
    pending.current = false; setBusy(false);
    if (result) setRecord(result);
    else setError("尚未確認決策寫回；重試會沿用本次請求識別，請勿視為已核准。");
  }
  return <div data-testid="priceops-scenario-workbench" className={g.priceWorkbench}>
    <div className={g.priceWorkbenchHeading}>
      <h4>PriceOps 定價情境模擬 workbench — {rec.store ?? segmentName} ({rec.id})</h4>
      <span>Policy: {comparison?.policy_version ?? "待取得"} | Solver: {comparison?.solver_version ?? "待取得"}</span>
    </div>
    {!rec.planId || !rec.itemId ? <p role="status">此建議缺少正式計畫／品項連結，無法模擬或寫回。</p> : null}
    {loading ? <p role="status">正在讀取計畫模擬與操作權限…</p> : null}
    {comparison && !comparison.command_permissions.writeback ? <p role="status">目前帳號沒有定價決策寫回權限。</p> : null}
    {(invalidPrice || (comparison && hardBlocked) || (scenario && !scenario.is_feasible)) ?
      <p role="alert" data-testid="priceops-fail-closed-alert">候選價格無效或硬限制未通過，無法核准此情境。</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    <fieldset disabled={busy} className={g.priceWorkbenchControls} aria-label="定價情境參數與決策">
      <div className={g.priceWorkbenchInputs}>
        <label>調整情境價: <input type="number" value={candidate} onChange={event => {
          setCandidate(event.target.value); setScenario(null); setRecord(null); setError(null);
        }} /></label>
        <span>Baseline 現價: <strong>{item?.current_price ?? rec.currentPrice}</strong></span>
        <button type="button" data-testid="priceops-simulate-btn" className={g.segDraftBtn} disabled={!canSimulate} onClick={() => void simulate()}>執行情境模擬</button>
      </div>
      <div className={g.priceBands}>
        <div data-testid="priceops-baseline-band"><h5>Baseline (目前價格)</h5><Bands value={simulated?.baseline_simulation ?? item?.baseline_simulation} /></div>
        <div data-testid="priceops-alternative-band"><h5>Alternative Scenario (情境模擬)</h5><Bands value={simulated?.candidate_simulation} />
          {simulated ? <div>Δ 毛利預期（金額）: <strong>{simulated.expected_gross_margin_change}</strong></div> : null}</div>
      </div>
      <div className={g.priceDecisionControls}><h5>決策回寫 (Idempotent Decision Writeback)</h5>
        <div className={g.priceWorkbenchInputs}>
          <label>決策 <select aria-label="決策" value={decision} onChange={event => { setDecision(event.target.value as PriceDecision); setRecord(null); }}>
            <option value="approved">核准最佳化情境 (Approved)</option><option value="rejected">退回情境 (Rejected)</option><option value="scenario_selected">選擇特定情境 (Scenario Selected)</option>
          </select></label>
          <label>決策原因 <input value={reason} onChange={event => { setReason(event.target.value); setRecord(null); }} placeholder="輸入決策原因..." /></label>
          <button type="button" data-testid="priceops-writeback-btn" disabled={!canWrite} onClick={() => void writeback()} className={g.segDraftBtn}>寫回決策 (Writeback)</button>
        </div>
        {decision === "approved" && comparison?.approval_actor_blockers?.length ?
          <p role="status">核准受限：{comparison.approval_actor_blockers.map(reason =>
            reason === "plan creator cannot approve their own pricing plan"
              ? "建立者不能核准自己的調價計畫，請交由獨立核准者處理。"
              : "無法確認獨立核准條件，暫時不允許核准。"
          ).join("；")}</p> : null}
        {decision === "approved" && comparison?.plan_status !== "pending_approval" ? <p>計畫須先完成送審，才能核准；選擇情境不代表核准或套用價格。</p> : null}
      </div>
    </fieldset>
    {busy ? <p role="status">正在等待 API 確認…</p> : null}
    {record ? <div role="status" data-testid="priceops-audit-status">決策已寫回: ID <strong>{record.decision_id}</strong> | 操作者: <strong>{record.actor}</strong> | 決策: <strong>{record.decision}</strong> | 時間: <strong>{record.written_back_at}</strong> | 審計: <strong>{record.audit_event_id}</strong> | 關聯: <strong>{record.correlation_id}</strong></div> : null}
  </div>;
}
