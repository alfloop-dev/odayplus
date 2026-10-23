"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApprovalPanel, AuditMetadata, Badge, Button, Card, Drawer, Form, PageHeader, Table, type TableColumnSpec } from "@oday-plus/ui";
import { createAVMDealRoomClient, SupersededAVMRead, type Artifact, type Context, type Detail, type Report, type Row, type Receipt, type Operation, type ValuationInput } from "./avmDealRoomClient";

type Location = { caseId: string | null; selected: string | null; store: string; status: string; confidence: string; pending: boolean; room: boolean; from: string; to: string };
function location(): Location {
  if (typeof window === "undefined") return { caseId: null, selected: null, store: "", status: "", confidence: "", pending: false, room: false, from: "", to: "" };
  const url = new URL(window.location.href); const match = /^\/w\/dealroom\/cases\/([^/]+)\/?$/.exec(url.pathname);
  let caseId: string | null = null; if (match) { try { caseId = decodeURIComponent(match[1]); } catch { caseId = "__invalid_case__"; } }
  return { caseId, selected: url.searchParams.get("selected"), store: url.searchParams.get("store_id") ?? "", status: url.searchParams.get("status") ?? "",
    confidence: url.searchParams.get("confidence") ?? "", pending: url.searchParams.get("pendingFinance") === "true", room: url.searchParams.get("roomReady") === "true",
    from: url.searchParams.get("from") ?? "", to: url.searchParams.get("to") ?? "" };
}
const number = (value: number | null | undefined) => value === null || value === undefined ? "受限或未提供" : value.toLocaleString("zh-TW");
function Range({ report }: { report: Report }) {
  const points = [...report.lenses.flatMap((r) => [r.p10, r.p50, r.p90]), report.fair_price.p10, report.fair_price.p90,
    ...(report.reserve_price === null ? [] : [report.reserve_price]), ...(report.asking_price === null ? [] : [report.asking_price])];
  const low = Math.min(...points); const high = Math.max(...points); const x = (v: number) => 40 + ((v - low) / (high - low || 1)) * 520;
  return <section aria-label="ValuationRangeChart">
    <h3>各鏡 P10／P50／P90</h3>
    {["income", "asset", "market", "blended"].filter((name) => !report.lenses.some((lens) => lens.lens === name)).map((name) => <p key={name}>{name}：此報告來源未提供，未補算或借用其他鏡。</p>)}
    <svg viewBox={`0 0 620 ${report.lenses.length * 48 + 70}`} role="img" aria-label="同一金額尺度的三鏡與 blended 估值區間">
      {report.lenses.map((r, i) => <g key={r.lens}>
        <title>{r.lens}: P10 {r.p10}, P50 {r.p50}, P90 {r.p90}</title>
        <text x="0" y={i * 48 + 14} fill="currentColor">{r.lens}</text>
        <line x1={x(r.p10)} x2={x(r.p90)} y1={i * 48 + 29} y2={i * 48 + 29} stroke="var(--odp-color-border-default)" strokeWidth="8" />
        <circle cx={x(r.p50)} cy={i * 48 + 29} r="4" fill="currentColor" />
      </g>)}
      {report.reserve_price !== null ? <line aria-label={`模型 Reserve ${report.reserve_price}`} x1={x(report.reserve_price)} x2={x(report.reserve_price)} y1="0" y2={report.lenses.length * 48} stroke="currentColor" strokeDasharray="4 4" /> : null}
      {report.asking_price !== null ? <line aria-label={`模型 Asking ${report.asking_price}`} x1={x(report.asking_price)} x2={x(report.asking_price)} y1="0" y2={report.lenses.length * 48} stroke="currentColor" /> : null}
    </svg>
    <Table density="compact" getRowKey={(row) => row.lens} data={report.lenses} columns={[
      { id: "lens", header: "鏡", accessor: "lens" }, { id: "p10", header: "P10", render: (r) => number(r.p10) },
      { id: "p50", header: "P50", render: (r) => number(r.p50) }, { id: "p90", header: "P90", render: (r) => number(r.p90) },
      { id: "delta", header: "與 fair P50 差額", render: (r) => number(r.p50 - report.fair_price.p50) },
    ]} />
    <p>Fair P10／P50／P90：{number(report.fair_price.p10)}／{number(report.fair_price.p50)}／{number(report.fair_price.p90)}</p>
    <p>模型 Reserve（虛線）：{number(report.reserve_price)}；模型 Asking（實線）：{number(report.asking_price)}</p>
    {report.lenses.map((lens) => <details key={lens.lens}><summary>{lens.lens} 方法與來源證據</summary><p>{lens.method}</p>
      <dl>{Object.entries(lens.evidence).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{typeof value === "string" ? value : JSON.stringify(value)}</dd></div>)}</dl></details>)}
    <p>流動性分數與可比成交價格標記：來源未提供。可比倍數如有記錄，列於 market 來源證據。</p>
  </section>;
}

const numericInputs = [
  ["gm_ttm", "近十二月毛利", true], ["forecast_gm_next_12m", "未來十二月毛利預測", true],
  ["asset_book_value", "資產帳面價值", true], ["equipment_fair_value", "設備公允價值", true],
  ["lease_liability", "租賃負債", false], ["working_capital", "營運資金", false], ["liquidity_discount", "流動性折讓率", false],
  ["quality_score", "實際資料品質分數", false], ["equipment_original_cost", "設備原始成本", false],
  ["useful_life_months", "耐用年限（月）", false], ["residual_value_ratio", "殘值比率", false],
] as const;
function CreateCase({ submit, busy }: { submit: (value: ValuationInput) => Promise<void>; busy: boolean }) {
  const [draft, setDraft] = useState<Record<string, string>>({}); const [error, setError] = useState("");
  const input = (key: string, label: string, numeric = false) => <label className="odp-field" key={key}><span>{label}</span>
    <input className="odp-input" inputMode={numeric ? "decimal" : "text"} value={draft[key] ?? ""} disabled={busy}
      onChange={(event) => setDraft((previous) => ({ ...previous, [key]: event.target.value }))} /></label>;
  return <Card title="建立估值案件"><Form affectedEntities={draft.store_id?.trim() ? [{ entityType: "store", entityId: draft.store_id.trim(), label: draft.store_id.trim() }] : []}
    fieldErrors={error ? [{ field: "估值輸入", message: error }] : []}
    submitAction={{ id: "create-avm-case", label: "提交建立估值案件", permitted: !busy, loading: busy }}
    onSubmit={async (event) => { event.preventDefault(); setError(""); try {
      if (!draft.store_id?.trim()) throw new Error("請提供實際門市 ID。");
      const value: Record<string, unknown> = { store_id: draft.store_id.trim() };
      for (const [key, label, required] of numericInputs) {
        const raw = draft[key]?.trim(); if (!raw) { if (required) throw new Error(`請提供${label}。`); continue; }
        const number = Number(raw); if (!Number.isFinite(number)) throw new Error(`${label}必須為有限數值。`); value[key] = number;
      }
      if (draft.comparable_multiples?.trim()) {
        const values = draft.comparable_multiples.split(",").map((item) => item.trim());
        if (values.some((item) => !item || !Number.isFinite(Number(item)))) throw new Error("可比倍數請填入以逗號分隔的有限數值。");
        value.comparable_multiples = values.map(Number);
      }
      if (draft.source_snapshot_ids?.trim()) value.source_snapshot_ids = draft.source_snapshot_ids.split(",").map((item) => item.trim()).filter(Boolean);
      for (const key of ["prediction_origin_time", "equipment_depreciation_basis", "depreciation_method", "depreciation_effective_date", "asset_in_service_date"]) {
        if (draft[key]?.trim()) value[key] = draft[key].trim();
      }
      if (draft.asset_book_value_includes_equipment) value.asset_book_value_includes_equipment = draft.asset_book_value_includes_equipment === "true";
      await submit(value as ValuationInput);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "建立未完成；輸入已保留。"); } }}>
    <p>填入來源提供的財務資料與 snapshot ID。空白品質分數表示尚未量測；選填欄位留白沿用原生輸入契約，不補造觀測。</p>
    {input("store_id", "實際門市 ID")}{numericInputs.map(([key, label, required]) => input(key, `${label}${required ? "（必填）" : "（選填）"}`, true))}
    {input("comparable_multiples", "可比倍數（逗號分隔）")}{input("source_snapshot_ids", "原始 snapshot IDs（逗號分隔）")}
    {input("prediction_origin_time", "預測起點（含時區 ISO 時間；選填）")}
    <details><summary>來源提供的折舊明細（選填）</summary>
      {input("equipment_depreciation_basis", "設備折舊基礎")}{input("depreciation_method", "折舊方法")}
      {input("depreciation_effective_date", "折舊生效日")}{input("asset_in_service_date", "設備啟用日")}
      <label className="odp-field"><span>資產帳面價值是否含設備</span><select className="odp-input" disabled={busy} value={draft.asset_book_value_includes_equipment ?? ""}
        onChange={(event) => setDraft((previous) => ({ ...previous, asset_book_value_includes_equipment: event.target.value }))}>
        <option value="">來源未提供</option><option value="true">包含</option><option value="false">未包含</option></select></label>
    </details>
  </Form></Card>;
}

function CaseDetail({ detail, execute, download, busy, receipt }: { detail: Detail; busy: boolean; receipt: Receipt | null;
  execute: (operation: Operation, reason: string, reserve: number | null) => Promise<Receipt>;
  download: (artifact: Artifact) => Promise<void> }) {
  const [reserve, setReserve] = useState(""); const [reason, setReason] = useState(""); const [roomOperation, setRoomOperation] = useState<"build" | "export">("build");
  const [workflowReason, setWorkflowReason] = useState(""); const [workflowError, setWorkflowError] = useState("");
  const [roomError, setRoomError] = useState(""); const report = detail.report; const approval = report?.finance_approval;
  const override = reserve.trim() === "" ? null : Number(reserve); const validReserve = override === null || Number.isFinite(override);
  return <>
    <Card title="Summary" type="valuation"><dl><dt>案件</dt><dd>{detail.case.case_id}</dd><dt>門市</dt><dd>{detail.case.store_id}</dd>
      <dt>狀態</dt><dd>{detail.case.status}</dd><dt>Fair P50</dt><dd>{number(report?.fair_price.p50)}</dd><dt>信心</dt><dd>{report?.confidence ?? "未提供"}</dd>
      <dt>財務核准</dt><dd>{approval ? "已核准" : "未核准"}</dd><dt>資料室</dt><dd>{detail.dataRoom ? `${detail.dataRoom.completeness * 100}%` : "尚未建立"}</dd></dl></Card>
    <Card title="Status & History"><ol>{detail.case.status_history.map((row, i) => <li key={`${row.timestamp}-${i}`}>
      {row.from_status} → {row.to_status}；{row.actor}；{row.reason}；<time dateTime={row.timestamp}>{row.timestamp}</time>；CID {row.correlation_id}</li>)}</ol></Card>
    <Card title="Normalized Margin">{detail.actions.normalize || detail.actions.value ? <>
      <label className="odp-field"><span>正規化／估值理由</span><textarea className="odp-textarea" value={workflowReason} disabled={busy} onChange={(event) => setWorkflowReason(event.target.value)} /></label>
      {workflowError ? <p role="alert">{workflowError}</p> : null}
      {(["normalize", "value"] as const).filter((operation) => detail.actions[operation]).map((operation) => <Button key={operation} disabled={busy || !workflowReason.trim()}
        onClick={() => { setWorkflowError(""); void execute(operation, workflowReason, null).catch((failure) => setWorkflowError(failure instanceof Error ? failure.message : "操作未完成。")); }}>
        {operation === "normalize" ? "正規化毛利" : "執行估值"}</Button>)}
    </> : null}<dl>{detail.normalizedMargin ? <>
      <dt>GM TTM</dt><dd>{number(detail.normalizedMargin.gm_ttm)}</dd><dt>GM FWD</dt><dd>{number(detail.normalizedMargin.gm_fwd)}</dd>
      <dt>Normalized GM</dt><dd>{number(detail.normalizedMargin.normalized_gm)}</dd><dt>信心</dt><dd>{detail.normalizedMargin.confidence}</dd>
      <dt>調整原因</dt><dd>{detail.normalizedMargin.adjustment_reasons.join("；") || "來源未列調整"}</dd></> : <><dt>狀態</dt><dd>尚無正規化結果</dd></>}</dl></Card>
    <Card title="Three-Lens Valuation" type="valuation">{report ? <><p>由 AVM 模型產生 · {report.model_version}</p><Range report={report} /></> : <p>估值報告尚未取得；未使用預設估值。來源狀態：{detail.reportAvailability.reasonCode}</p>}</Card>
    <Card title="Approval (Finance)" type="decision">
      {!detail.creatorVerification.verified ? <p>原生建立者的受信來源尚未取得；此案保留唯讀，未補造建立者或自審證明。</p> : null}
      {report?.confidence === "low" ? <p className="odp-inline-warning" role="note">低信心估值：請檢視來源限制並在理由中說明判斷；低信心本身不新增核准禁令。</p> : null}
      {approval ? <><p>Decision id：{approval.decision_id} · CID {approval.correlation_id}</p><AuditMetadata meta={{ actor: approval.actor_id, timestamp: approval.approved_at,
        reason: approval.decision_reason, policyVersion: approval.policy_version, before: report?.reserve_price, after: approval.reserve_price }} /></> : null}
      {detail.actions.approve && report ? <>
        <label className="odp-field"><span>Reserve override（可留空）</span><input className="odp-input" inputMode="decimal" value={reserve} disabled={busy} onChange={(event) => setReserve(event.target.value)} /></label>
        <p>模型 Reserve {number(report.reserve_price)} → 人工提議 {override === null ? "維持模型值" : number(override)}；差額 {override === null || report.reserve_price === null || !validReserve ? "未覆寫" : number(override - report.reserve_price)}</p>
        <ApprovalPanel title="財務核准" decisionStatus="PENDING_REVIEW" allowedDecisions={["APPROVE"]}
          recommendation={{ text: "核對上方實際估值及原始證據後，提交財務核准。", modelVersion: report.model_version, requiresApproval: true }} audit={{ actor: "來源未提供（估值執行者）", timestamp: report.valued_at, modelVersion: report.model_version }}
          disabledReason={!validReserve ? "覆寫值必須為有限數值" : busy ? "命令正在處理" : undefined}
          onSubmit={async ({ decision, reason: decisionReason }) => {
            if (decision !== "APPROVE" || !validReserve) throw new Error("原生財務流程僅接受核准與有限覆寫值。");
            const result = await execute("approve", decisionReason, override);
            return { decisionId: result.sourceId, auditEventId: result.auditEventId, status: "APPROVED" };
          }} />
      </> : !approval ? <p>目前無可執行的財務核准；案件須為 REVIEW_REQUIRED，且建立者不得核准自己的案件。</p> : null}
    </Card>
    <Card title="DataRoom" type="evidence">
      {!detail.financeVerification.verified && approval ? <p>核准的受信來源尚未取得；未將歷史 actor 字串視為已核可資料室的權威。</p> : null}
      <p>以下為原生資料室清單與實際估值參照；原始附件檔案尚無提供者，未提供文件下載。</p>
      {detail.dataRoom ? <><p>資料室 {detail.dataRoom.dataroom_id} · 完整度 {detail.dataRoom.completeness * 100}% · {detail.dataRoom.is_complete ? "清單項目已就緒" : "仍有缺件"}</p>
        <dl><dt>Valuation card</dt><dd>{detail.dataRoom.valuation_card.case_id as string}／{detail.dataRoom.valuation_card.store_id as string}</dd>
          <dt>模型 Reserve／Asking</dt><dd>{number(detail.dataRoom.valuation_card.reserve_price as number | undefined)}／{number(detail.dataRoom.valuation_card.asking_price as number | undefined)}</dd></dl>
        <Table density="compact" data={detail.dataRoom.checklist} getRowKey={(r) => r.document_id} columns={[
          { id: "name", header: "文件", render: (r) => `${r.name} (${r.document_id})` }, { id: "status", header: "狀態", render: (r) => r.status === "ready" ? "就緒" : `缺件（${r.status}）` },
          { id: "source", header: "實際來源 ID", render: (r) => r.source_snapshot_id ?? "未提供" },
        ]} /><p>缺件：{detail.dataRoom.missing_documents.join("、") || "清單未列缺件"}</p></> : <p>財務核准後才能建立資料室。</p>}
      {detail.actions.build || detail.actions.export ? <Form onSubmit={async (event) => { event.preventDefault(); setRoomError(""); try { await execute(roomOperation, reason, null); } catch (error) { setRoomError(error instanceof Error ? error.message : "命令未完成。"); } }}
        affectedEntities={[{ entityType: "avm.case", entityId: detail.case.case_id, label: `${detail.case.case_id}／${detail.case.store_id}` }]}
        fieldErrors={roomError ? [{ field: "資料室", message: roomError }] : []}
        submitAction={{ id: "avm-room-command", label: roomOperation === "build" ? "建立資料室" : "建立本次資料室 JSON 匯出", permitted: !busy && Boolean(reason.trim()) && detail.actions[roomOperation], loading: busy }}>
        <fieldset disabled={busy}><legend>資料室操作</legend>
          <select aria-label="資料室操作" value={roomOperation} onChange={(event) => setRoomOperation(event.target.value as "build" | "export")}>
            <option value="build" disabled={!detail.actions.build}>建立資料室</option><option value="export" disabled={!detail.actions.export}>匯出資料室 JSON</option></select>
          <label className="odp-field"><span>操作理由</span><textarea className="odp-textarea" required value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        </fieldset></Form> : null}
      <h3>歷次匯出</h3>{detail.exports.length === 0 ? <p>尚無已記錄匯出。</p> : <ol>{detail.exports.map((row) => <li key={row.export_id}>
        {row.export_id} · {row.actor} · {row.reason} · {row.exported_at} · CID {row.correlation_id}
        {detail.actions.export && row.artifact ? <Button disabled={busy} onClick={() => { void download(row.artifact!); }}>下載已記錄 JSON {row.export_id}</Button> : <span> 原始匯出檔案未留存或目前無匯出權限</span>}
      </li>)}</ol>}
    </Card>
    <Card title="Version / Audit"><p>案件來源版本 {detail.expectedVersion}</p><p>原始 snapshot IDs：{detail.sourceSnapshotIds.join("、") || "來源未提供"}</p>
      {report ? <><p>Report {report.report_id}／{report.valuation_version} · Feature {report.feature_version} · Depreciation {report.depreciation_version}</p>
        <AuditMetadata meta={{ actor: "來源未提供（估值執行者）", modelVersion: report.model_version, timestamp: report.valued_at, featureSnapshotTime: report.prediction_origin_time }} /></> : null}
      <ol>{detail.reportHistory.map((row) => <li key={row.report_id}>{row.report_id}／{row.valuation_version} · {row.model_version} · {row.valued_at}</li>)}</ol>
      {receipt ? <p role="status">後端已記錄 {receipt.sourceId} · actor {receipt.actor} · audit {receipt.auditEventId} · CID {receipt.correlationId} · 稽核交付 {receipt.deliveryStatus}（非外部歸檔完成聲明）</p> : null}
    </Card>
  </>;
}

export function AVMDealRoomWorkspace() {
  const [view, setView] = useState(location); const [retry, setRetry] = useState(0); const query = JSON.stringify([view, retry]);
  const lease = useRef({ query, active: true }); if (lease.current.query !== query) { lease.current.active = false; lease.current = { query, active: true }; }
  const epoch = useRef(0); const busyRef = useRef(false); const latest = useRef(query); latest.current = query;
  const [scope, setScope] = useState(query); const [context, setContext] = useState<Context | null>(null); const [rows, setRows] = useState<Row[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null); const [error, setError] = useState(""); const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [busy, setBusy] = useState(false); const [receipt, setReceipt] = useState<Receipt | null>(null);
  const update = useCallback((patch: Partial<Location>) => {
    if (busyRef.current) return; const next = { ...location(), ...patch }; const url = new URL(window.location.href);
    url.pathname = next.caseId ? `/w/dealroom/cases/${encodeURIComponent(next.caseId)}` : "/w/dealroom/cases";
    const filters: [string, string | null][] = [["selected", next.selected], ["store_id", next.store], ["status", next.status], ["confidence", next.confidence], ["pendingFinance", next.pending ? "true" : ""], ["roomReady", next.room ? "true" : ""], ["from", next.from], ["to", next.to]];
    for (const [key, value] of filters) {
      if (value) url.searchParams.set(key, String(value)); else url.searchParams.delete(key);
    }
    window.history.pushState(null, "", `${url.pathname}${url.search}`); setView(next);
  }, []);
  useEffect(() => { const restore = () => setView(location()); const refresh = () => { if (!busyRef.current) setRetry((v) => v + 1); };
    window.addEventListener("popstate", restore); window.addEventListener("focus", refresh);
    return () => { window.removeEventListener("popstate", restore); window.removeEventListener("focus", refresh); }; }, []);
  useEffect(() => {
    const own = lease.current; const id = ++epoch.current; const current = () => own.active && lease.current === own && epoch.current === id;
    const client = createAVMDealRoomClient(current); setScope(query); setLoading(true); setContext(null); setDetail(null); setRows([]); setError(""); setReceipt(null); setBusy(false); busyRef.current = false;
    void (async () => { try { const ctx = await client.context(); const list = await client.list(ctx);
      const selected = view.caseId ?? view.selected; const found = selected ? await client.detail(selected, ctx) : null;
      if (current()) { setContext(ctx); setRows(list); setDetail(found); }
    } catch (failure) { if (current()) setError(failure instanceof Error ? failure.message : "AVM 無法取得。"); }
    finally { if (current()) setLoading(false); } })();
    return () => { if (epoch.current === id) epoch.current += 1; };
  }, [query]);
  const keys = useRef(new Map<string, string>());
  const execute = async (operation: Operation, reason: string, reserve: number | null) => {
    if (!detail || scope !== query || latest.current !== query || lease.current.query !== query || busyRef.current) throw new Error("請先重新核對目前案件。");
    const own = lease.current; const id = epoch.current; const current = () => own.active && lease.current === own && epoch.current === id;
    const client = createAVMDealRoomClient(current); const fingerprint = JSON.stringify([detail.context.contextKey, detail.case.case_id, detail.expectedVersion, operation, reason, reserve]);
    const key = keys.current.get(fingerprint) ?? `avm-${crypto.randomUUID()}`; keys.current.set(fingerprint, key); busyRef.current = true; setBusy(true); setError("");
    try { const result = await client.command(detail, operation, reason, reserve, key); const refreshed = await client.detail(detail.case.case_id, detail.context);
      if (!current()) throw new SupersededAVMRead(); setDetail(refreshed); setReceipt(result.receipt); keys.current.delete(fingerprint); return result.receipt;
    } catch (failure) {
      if (current() && failure instanceof SupersededAVMRead) { setRows([]); setDetail(null); setContext(null); setError(failure.message); }
      throw failure;
    } finally { if (current()) { busyRef.current = false; setBusy(false); } }
  };
  const create = async (value: ValuationInput) => {
    if (!context || !context.canCreate || scope !== query || latest.current !== query || busyRef.current) throw new Error("請重新核對目前建立權限。");
    const own = lease.current; const id = epoch.current; const current = () => own.active && lease.current === own && epoch.current === id;
    const client = createAVMDealRoomClient(current); const fingerprint = JSON.stringify([context.contextKey, "create", value]);
    const key = keys.current.get(fingerprint) ?? `avm-create-${crypto.randomUUID()}`; keys.current.set(fingerprint, key);
    busyRef.current = true; setBusy(true);
    try { const result = await client.create(context, value, key); await client.detail(result.detail.case.case_id, context);
      if (!current()) throw new SupersededAVMRead(); keys.current.delete(fingerprint); setShowCreate(false);
      busyRef.current = false; setBusy(false); update({ caseId: result.detail.case.case_id, selected: null });
    } catch (failure) { if (current() && failure instanceof SupersededAVMRead) { setRows([]); setDetail(null); setContext(null); setError(failure.message); } throw failure;
    } finally { if (current()) { busyRef.current = false; setBusy(false); } }
  };
  const download = async (artifact: Artifact) => {
    if (!detail || latest.current !== query || lease.current.query !== query || busyRef.current) return; const own = lease.current; const id = epoch.current; const current = () => own.active && lease.current === own && epoch.current === id;
    busyRef.current = true; setBusy(true); setError("");
    try { const blob = await createAVMDealRoomClient(current).download(detail, artifact); if (!current()) return;
      const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = artifact.filename; anchor.click(); URL.revokeObjectURL(url);
    } catch (failure) { if (current()) {
      if (failure instanceof SupersededAVMRead) { setRows([]); setDetail(null); setContext(null); }
      setError(failure instanceof Error ? failure.message : "匯出未完成。");
    } }
    finally { if (current()) { busyRef.current = false; setBusy(false); } }
  };
  const visible = scope === query; const filtered = (visible ? rows : []).filter((row) => (!view.store || row.case.store_id === view.store) && (!view.status || row.case.status === view.status)
    && (!view.confidence || row.report?.confidence === view.confidence) && (!view.pending || row.case.status === "REVIEW_REQUIRED") && (!view.room || row.dataRoom !== null)
    && (!view.from || row.case.created_at.slice(0, 10) >= view.from) && (!view.to || row.case.created_at.slice(0, 10) <= view.to));
  const columns: TableColumnSpec<Row>[] = [
    { id: "case", header: "Case／Store", render: (r) => `${r.case.case_id}／${r.case.store_id}` }, { id: "status", header: "狀態", render: (r) => <Badge label={r.case.status} tone="blue" /> },
    { id: "fair", header: "Fair P50", render: (r) => number(r.report?.fair_price.p50) }, { id: "reserve", header: "Reserve／Asking", render: (r) => `${number(r.report?.reserve_price)}／${number(r.report?.asking_price)}` },
    { id: "confidence", header: "信心", render: (r) => `${r.report?.confidence === "low" ? "⚠ " : ""}${r.report?.confidence ?? "未提供"}` },
    { id: "finance", header: "財務核准", render: (r) => r.report?.finance_approval ? `${r.report.finance_approval.actor_id}／${r.report.finance_approval.approved_at}` : "未核准" },
    { id: "room", header: "DataRoom", render: (r) => r.dataRoom ? `${r.dataRoom.completeness * 100}% · 已匯出 ${r.exportCount} 次` : "未建立" },
  ];
  return <main className="odp-stack" aria-busy={busy || loading}>
    <PageHeader title={view.caseId ? "估值案件詳情" : "估值案件"} summary="對門市做三鏡估值、財務核准並備妥交易資料室。"
      breadcrumb={[{ label: "估值案件", href: "/w/dealroom/cases" }, ...(view.caseId ? [{ label: view.caseId }] : [])]} />
    {visible && context ? <p>目前帳號 {context.subjectId} · 租戶 {context.tenantId}</p> : null}
    {!visible || loading ? <p role="status">正在核對目前帳號及案件來源…</p> : null}
    {visible && error ? <p role="alert">{error}；指定案件未改開其他案件。</p> : null}
    {visible && !loading ? <Button disabled={busy} onClick={() => setRetry((v) => v + 1)}>重新核對來源</Button> : null}
    {!view.caseId ? <>
      <fieldset disabled={busy}><legend>案件篩選（同步網址）</legend>
        <label className="odp-field">門市 ID<input className="odp-input" value={view.store} onChange={(e) => update({ store: e.target.value })} /></label>
        <label className="odp-field">狀態<select value={view.status} onChange={(e) => update({ status: e.target.value })}><option value="">全部</option>{Array.from(new Set(rows.map((r) => r.case.status))).map((s) => <option key={s}>{s}</option>)}</select></label>
        <label className="odp-field">信心<select value={view.confidence} onChange={(e) => update({ confidence: e.target.value })}><option value="">全部</option>{Array.from(new Set(rows.flatMap((r) => r.report ? [r.report.confidence] : []))).map((s) => <option key={s}>{s}</option>)}</select></label>
        <label><input type="checkbox" checked={view.pending} onChange={(e) => update({ pending: e.target.checked })} />待財務核准</label>
        <label><input type="checkbox" checked={view.room} onChange={(e) => update({ room: e.target.checked })} />已建立 DataRoom</label>
        <label>建立起日<input type="date" value={view.from} onChange={(e) => update({ from: e.target.value })} /></label><label>建立迄日<input type="date" value={view.to} onChange={(e) => update({ to: e.target.value })} /></label>
      </fieldset>
      {visible && context?.canCreate ? <Button disabled={busy || loading} onClick={() => setShowCreate((value) => !value)}>建立估值案件</Button> : null}
      {visible && context?.canCreate && showCreate ? <CreateCase key={context.contextKey} submit={create} busy={busy} /> : null}
      <Table data={filtered} columns={columns} density="compact" loading={loading} getRowKey={(r) => r.case.case_id} onRowOpen={(r) => update({ selected: r.case.case_id })} />
      <Drawer open={Boolean(view.selected)} title="估值案件摘要" onClose={() => update({ selected: null })} deepLinkHref={detail ? `/w/dealroom/cases/${encodeURIComponent(detail.case.case_id)}` : undefined}
        onPrev={filtered.findIndex((row) => row.case.case_id === view.selected) > 0 ? () => update({ selected: filtered[filtered.findIndex((row) => row.case.case_id === view.selected) - 1].case.case_id }) : undefined}
        onNext={filtered.findIndex((row) => row.case.case_id === view.selected) >= 0 && filtered.findIndex((row) => row.case.case_id === view.selected) < filtered.length - 1 ? () => update({ selected: filtered[filtered.findIndex((row) => row.case.case_id === view.selected) + 1].case.case_id }) : undefined}>
        {detail && visible ? <><p>{detail.case.case_id}／{detail.case.store_id} · {detail.case.status}</p><p>Fair P50 {number(detail.report?.fair_price.p50)}；Reserve {number(detail.report?.reserve_price)}；Asking {number(detail.report?.asking_price)}；信心 {detail.report?.confidence ?? "未提供"}</p>
          <a href={`/w/dealroom/cases/${encodeURIComponent(detail.case.case_id)}`}>開啟案件詳情</a></> : <p>{loading ? "正在核對案件…" : "指定案件不存在、無權限或來源尚未取得；未改開第一案。"}</p>}
      </Drawer>
    </> : detail && visible ? <CaseDetail key={`${detail.context.contextKey}:${detail.case.case_id}`} detail={detail} execute={execute} download={download} busy={busy} receipt={receipt} /> : null}
  </main>;
}
