"use client";

import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Button } from "../components";
import shell from "../operator.module.css";
import styles from "./interventions.module.css";
import { id, INTERVENTION_KINDS, object, type ActorScope, type Intervention } from "./interventionContract";
import { attemptScopeMatches, COMMAND_LABELS, COMMAND_SECTION, commandEligible, hasVerifiedDefaultChildDuration, LIFECYCLE_COMMANDS, lifecycleChildMatches, lifecycleLabelMatches, lifecycleReadbackMatches, makeLifecycleAttempt, parseCommandContext, parseLifecycleRead, parseLifecycleReceipt, type CommandContext, type LifecycleAttempt, type LifecycleCase, type LifecycleCommand } from "./lifecycleCommandContract";

export type LifecycleLease = {
  key: string; scope: ActorScope; caseId: string; storeId: string;
  assert: () => void; verify: () => Promise<void>; json: (url: string, init?: RequestInit) => Promise<unknown>;
};
export type LifecycleCommandFormsProps = {
  row: Intervention; lease: LifecycleLease; acquire: () => boolean; release: () => void; locked: boolean;
  onConfirmed: (row: LifecycleCase) => Promise<void>;
  children: (controls: { section: (section: number) => ReactNode }) => ReactNode;
};
type Capability = { context: CommandContext | null; error: string | null };
type Draft = { reason: string; assignee: string; role: string; eligible: boolean; reasons: string; overlap: boolean; action: Record<string, unknown>; disposition: string; followUp: boolean; followKind: string; expected: string; start: string; end: string; revenue: string; margin: string; spend: string; treatment: string; control: string; hasControl: boolean; randomized: boolean; pretrend: string; method: string; measurement: string };
const initialDraft = (row: Intervention): Draft => ({ reason: "", assignee: "", role: "", eligible: true, reasons: "", overlap: false, action: structuredClone(row.action_spec), disposition: "KEEP", followUp: false, followKind: "", expected: "", start: "", end: "", revenue: "", margin: "", spend: "", treatment: "", control: "", hasControl: false, randomized: false, pretrend: "INCONCLUSIVE", method: "BEFORE_AFTER", measurement: "" });
const labels: Record<string, string> = { price_change_pct: "價格調整百分比", campaign: "活動設定", rollback_plan: "回滾計畫" };
const pathLabel = (path: string) => path.split(".").map(part => labels[part] ?? part).join(" › ");
function ParameterValue({ value, path, change, disabled }: { value: unknown; path: string; change: (value: unknown) => void; disabled: boolean }) {
  if (object(value)) return <fieldset disabled={disabled}><legend>{pathLabel(path)}</legend><ParameterEditor value={value} path={path} change={change} disabled={disabled} /></fieldset>;
  if (Array.isArray(value)) return <fieldset disabled={disabled}><legend>{pathLabel(path)}（清單）</legend>{value.map((item, index) => <ParameterValue key={index} value={item} path={path + "." + (index + 1)} disabled={disabled} change={next => change(value.map((v, i) => i === index ? next : v))} />)}<Button disabled={disabled} onClick={() => change([...value, ""])}>新增清單項目</Button></fieldset>;
  if (value === null) return <label>{pathLabel(path)}<select disabled={disabled} value="null" onChange={e => change(e.target.value === "null" ? null : e.target.value === "number" ? 0 : e.target.value === "boolean" ? false : "")}><option value="null">空值</option><option value="text">文字</option><option value="number">數值</option><option value="boolean">是非</option></select></label>;
  if (typeof value === "boolean") return <label><input disabled={disabled} type="checkbox" checked={value} onChange={e => change(e.target.checked)} />{pathLabel(path)}</label>;
  return <label>{pathLabel(path)}<input disabled={disabled} aria-label={pathLabel(path)} type={typeof value === "number" ? "number" : "text"} step={typeof value === "number" ? "any" : undefined} value={typeof value === "number" && !Number.isFinite(value) ? "" : String(value)} onChange={e => change(typeof value === "number" ? e.target.value === "" ? NaN : Number(e.target.value) : e.target.value)} /></label>;
}
function ParameterEditor({ value, path = "", change, disabled }: { value: Record<string, unknown>; path?: string; change: (value: Record<string, unknown>) => void; disabled: boolean }) {
  const [name, setName] = useState(""), [kind, setKind] = useState("text"), [error, setError] = useState<string | null>(null);
  const add = () => {
    if (!name.trim() || Object.hasOwn(value, name.trim())) { setError("請填寫未重複的參數名稱。"); return; }
    const next = kind === "number" ? 0 : kind === "boolean" ? false : kind === "group" ? {} : kind === "list" ? [] : kind === "null" ? null : "";
    change({ ...value, [name.trim()]: next }); setName(""); setError(null);
  };
  return <div className={shell.modalForm}>{Object.entries(value).map(([key, item]) => <ParameterValue key={key} value={item} path={path ? path + "." + key : key} disabled={disabled} change={next => change({ ...value, [key]: next })} />)}
    <label>新增參數名稱<input disabled={disabled} value={name} onChange={e => setName(e.target.value)} /></label><label>參數內容<select disabled={disabled} value={kind} onChange={e => setKind(e.target.value)}><option value="text">文字</option><option value="number">數值</option><option value="boolean">是非</option><option value="group">群組</option><option value="list">清單</option><option value="null">空值</option></select></label>
    <Button disabled={disabled} onClick={add}>新增參數</Button>{error && <p role="alert">{error}</p>}<details><summary>檢查設定內容</summary><pre>{JSON.stringify(value, null, 2)}</pre></details></div>;
}
function business(command: LifecycleCommand, draft: Draft): Record<string, unknown> {
  if (command === "assign") return { assignee: draft.assignee.trim(), ...(draft.role.trim() ? { role: draft.role.trim() } : {}) };
  if (command === "eligibility") return { eligible: draft.eligible, reasons: draft.reasons.split("\n").map(v => v.trim()).filter(Boolean) };
  if (command === "action") return { action_spec: draft.action };
  if (command === "conflict-check") return { allow_overlap: draft.overlap, reason: draft.reason.trim() };
  if (command === "close") return { disposition: draft.disposition, reason: draft.reason.trim(), follow_up: draft.followUp, ...(draft.followUp && draft.followKind ? { follow_up_kind: draft.followKind } : {}) };
  if (command === "adjust") return { reason: draft.reason.trim(), action_spec: draft.action, ...(draft.expected.trim() ? { expected_outcome: draft.expected.trim() } : {}),
    ...(draft.start ? { planned_start: new Date(draft.start).toISOString() } : {}), ...(draft.end ? { planned_end: new Date(draft.end).toISOString() } : {}) };
  if (command === "stop" || command === "rollback") return { reason: draft.reason.trim() };
  if (command === "outcomes") {
    if ([draft.revenue, draft.margin, draft.spend, draft.treatment, draft.control].some(v => v.trim() === "")) throw new Error("量測數值與店數為必填；請確認實際紀錄。");
    return { incremental_revenue: Number(draft.revenue), incremental_gross_margin: Number(draft.margin), ad_spend: Number(draft.spend), treatment_store_count: Number(draft.treatment), control_store_count: Number(draft.control),
      has_control_group: draft.hasControl, randomized: draft.randomized, pretrend_status: draft.pretrend, evaluation_method: draft.method, ...(draft.measurement.trim() ? { measurement_method: draft.measurement.trim() } : {}) };
  }
  return {};
}
export function LifecycleCommandForms({ row, lease, acquire, release, locked, onConfirmed, children }: LifecycleCommandFormsProps) {
  const [capabilities, setCapabilities] = useState<Partial<Record<LifecycleCommand, Capability>>>({}), [loading, setLoading] = useState(true), [capabilityRevision, setCapabilityRevision] = useState(0);
  const [dialog, setDialog] = useState<LifecycleCommand | null>(null), [draft, setDraft] = useState(initialDraft(row)), [review, setReview] = useState(false);
  const [busy, setBusy] = useState(false), [unknown, setUnknown] = useState(false), [error, setError] = useState<string | null>(null), [confirmed, setConfirmed] = useState<string | null>(null);
  const original = useRef<LifecycleAttempt | null>(null), working = useRef(false), held = useRef(false), capabilityEpoch = useRef(0), modal = useRef<HTMLDivElement>(null), previousFocus = useRef<HTMLElement | null>(null), currentRow = useRef<LifecycleCase | null>(null);
  const owns = () => { try { lease.assert(); return true; } catch { return false; } };
  async function json(url: string, init?: RequestInit): Promise<unknown> { lease.assert(); const body = await lease.json(url, init); lease.assert(); return body; }
  useEffect(() => {
    const generation = ++capabilityEpoch.current; let disposed = false;
    if (original.current) return;
    setLoading(true); setCapabilities({});
    const eligible = LIFECYCLE_COMMANDS.filter(command => commandEligible(row, command));
    void Promise.all(eligible.map(async command => {
      let capability: Capability;
      try {
        const body = await json("/api/v1/interventions/" + encodeURIComponent(lease.caseId) + "/command-context?command=" + encodeURIComponent(command));
        const context = parseCommandContext(body, row, lease.scope, command);
        capability = { context, error: context ? null : "操作資料尚未通過核對。" };
      } catch (exc) { capability = { context: null, error: String(exc).includes("403") ? "目前登入者沒有此操作權限。" : "操作權限或資料暫時無法核對。" }; }
      if (!disposed && generation === capabilityEpoch.current && owns()) setCapabilities(value => ({ ...value, [command]: capability }));
    })).finally(() => { if (!disposed && generation === capabilityEpoch.current && owns()) setLoading(false); });
    return () => { disposed = true; capabilityEpoch.current += 1; };
  }, [lease.key, row.version, row.review_context?.snapshot_sha256, capabilityRevision]);
  useEffect(() => {
    if (!dialog) return;
    modal.current?.querySelector<HTMLElement>("input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled])")?.focus();
    return () => { if (owns() && previousFocus.current?.isConnected) previousFocus.current.focus(); previousFocus.current = null; };
  }, [dialog]);
  useEffect(() => { if (dialog) modal.current?.querySelector<HTMLElement>("input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled])")?.focus(); }, [review]);
  useEffect(() => () => { capabilityEpoch.current += 1; }, []);
  async function open(command: LifecycleCommand) {
    if (working.current || !owns()) return;
    if (original.current) { previousFocus.current = document.activeElement as HTMLElement | null; setDialog(original.current.command); setReview(true); return; }
    if (locked || !capabilities[command]?.context || !acquire()) return;
    previousFocus.current = document.activeElement as HTMLElement | null;
    held.current = true; working.current = true; setBusy(true); setError(null); setConfirmed(null);
    try {
      const read = await json("/api/v1/interventions/" + encodeURIComponent(lease.caseId));
      const fresh = parseLifecycleRead(read, lease.scope, lease.caseId);
      if (!fresh || fresh.store_id !== lease.storeId || !commandEligible(fresh, command) || !parseCommandContext(capabilities[command]?.context, fresh, lease.scope, command)) throw new Error("案件或操作範圍已更新，請重新載入。");
      currentRow.current = fresh; setDraft(initialDraft(fresh)); setDialog(command); setReview(false);
    } catch (exc) { if (owns()) { setError(String(exc)); held.current = false; release(); } }
    finally { if (owns()) { working.current = false; setBusy(false); } }
  }
  function close() {
    if (working.current) return;
    setDialog(null);
    if (!unknown) { original.current = null; setReview(false); held.current = false; release(); }
  }
  function prepare() {
    if (!dialog || working.current || !currentRow.current) return;
    try {
      lease.assert();
      const context = capabilities[dialog]?.context; if (!context) throw new Error("目前操作權限尚未核驗。");
      original.current = makeLifecycleAttempt(currentRow.current, lease.scope, context, business(dialog, draft));
      setError(null); setReview(true);
    } catch (exc) { if (owns()) setError(String(exc)); }
  }
  async function submit() {
    const attempt = original.current;
    if (!attempt || working.current || !owns() || !attemptScopeMatches(attempt, lease.scope)) return;
    working.current = true; setBusy(true); setError(null);
    let posted = unknown;
    try {
      await lease.verify(); lease.assert();
      if (!unknown) {
        const read = await json("/api/v1/interventions/" + encodeURIComponent(lease.caseId));
        const fresh = parseLifecycleRead(read, lease.scope, lease.caseId);
        if (!fresh || !commandEligible(fresh, attempt.command) || !parseCommandContext(attempt.context, fresh, lease.scope, attempt.command)) throw new Error("案件已變更，原確認內容尚未送出；請重新載入。");
      }
      // Unknown outcomes reuse the exact frozen transport. Current permission
      // and owner are checked by the native POST before replay lookup.
      posted = true;
      const raw = await json("/api/v1/interventions/" + encodeURIComponent(lease.caseId) + "/" + attempt.command, { method: "POST", body: attempt.serialized, headers: attempt.headers });
      const receipt = parseLifecycleReceipt(raw, attempt); if (!receipt) throw new Error("LIFECYCLE_RECEIPT_INVALID：操作結果尚未完成核對。");
      const read = await json("/api/v1/interventions/" + encodeURIComponent(lease.caseId) + "?lifecycle_correlation_id=" + encodeURIComponent(attempt.correlation));
      const persisted = parseLifecycleRead(read, lease.scope, lease.caseId);
      if (!persisted || !lifecycleReadbackMatches(persisted, receipt, attempt)) throw new Error("LIFECYCLE_READBACK_MISMATCH：保存紀錄與原操作稽核尚未核對。");
      if (receipt.label) {
        const label = await json("/api/v1/interventions/" + encodeURIComponent(lease.caseId) + "/label");
        if (!lifecycleLabelMatches(label, receipt)) throw new Error("LIFECYCLE_LABEL_READBACK_MISMATCH：原評估標籤尚未核對。");
      }
      const linked = attempt.command === "adjust" ? receipt.target.replacement_id : attempt.command === "close" ? receipt.target.close?.follow_up_intervention_id : null;
      if (linked) {
        const childRaw = await json("/api/v1/interventions/" + encodeURIComponent(linked) + "?lifecycle_correlation_id=" + encodeURIComponent(attempt.correlation));
        const child = parseLifecycleRead(childRaw, lease.scope, linked);
        if (!child || !lifecycleChildMatches(child, receipt, attempt)) throw new Error("LIFECYCLE_LINEAGE_READBACK_MISMATCH：追蹤案或替代案尚未核對。");
      }
      await lease.verify(); lease.assert(); await onConfirmed(persisted); lease.assert(); await lease.verify(); lease.assert();
      setConfirmed(COMMAND_LABELS[attempt.command] + "已保存並完成原生稽核核對" + (persisted.version > receipt.target.version ? "；案件後續已有更新。" : "。"));
      setDialog(null); setUnknown(false); original.current = null; held.current = false; release(); setCapabilityRevision(v => v + 1);
    } catch (exc) {
      if (owns()) { setError(String(exc)); setUnknown(posted); if (!posted) { original.current = null; setReview(false); } }
    } finally { if (owns()) { working.current = false; setBusy(false); } }
  }
  function trap(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape" && !working.current) { event.preventDefault(); close(); }
    if (event.key !== "Tab") return;
    const nodes = [...(modal.current?.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex='0']") ?? [])];
    if (!nodes.length) { event.preventDefault(); return; }
    const first = nodes[0], last = nodes[nodes.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  const update = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft(d => ({ ...d, [key]: value }));
  const details = parseLifecycleRead(row, lease.scope, lease.caseId);
  const controls = { section: (section: number) => {
    const eligible = LIFECYCLE_COMMANDS.filter(command => COMMAND_SECTION[command] === section && commandEligible(row, command));
    if (!eligible.length && !(original.current && COMMAND_SECTION[original.current.command] === section)) return null;
    return <div data-testid={"lifecycle-section-" + section}>{section === 1 && details?.assigned_to && <p>受指派者：{details.assigned_to} · 指派者：{details.assigned_by}</p>}
      <div className={styles.actions}>{unknown && original.current && COMMAND_SECTION[original.current.command] === section ? <Button disabled={busy} onClick={() => void open(original.current!.command)}>核對原操作結果</Button>
        : eligible.filter(command => capabilities[command]?.context).map(command => <Button key={command} disabled={busy || locked || held.current} variant={["stop", "rollback", "adjust"].includes(command) ? "danger" : "secondary"} onClick={() => void open(command)}>{COMMAND_LABELS[command]}</Button>)}</div>
      {!unknown && eligible.some(command => capabilities[command]?.error) && <p>部分操作權限或資料尚未通過核對。</p>}{loading && !unknown && <p>操作權限核對中…</p>}
    </div>;
  } };
  const reasonRequired = dialog && ["close", "adjust", "stop", "rollback"].includes(dialog);
  const defaultDurationVerified = hasVerifiedDefaultChildDuration(currentRow.current ?? row);
  const durationBlocked = !review && !defaultDurationVerified && (dialog === "adjust" && !draft.end || dialog === "close" && draft.followUp);
  const recovery = unknown && original.current;
  return <>{children(controls)}{confirmed && <p role="status" data-testid="lifecycle-confirmed" className={shell.successBlock}>{confirmed}</p>}
    {error && !dialog && <div role="alert" className={shell.warningBlock} data-testid="lifecycle-error">{error}</div>}
    {dialog && <div className={shell.modalBackdrop}><div ref={modal} role="dialog" aria-modal="true" aria-labelledby="lifecycle-dialog-title" className={shell.modal} onKeyDown={trap} data-testid="lifecycle-command-dialog">
      <header className={shell.modalHeader}><h2 id="lifecycle-dialog-title">{review ? "確認" : ""}{COMMAND_LABELS[dialog]}</h2></header><div className={shell.modalForm}>
        <p>案件：{lease.caseId} · 門市：{lease.storeId}</p>
        {recovery && <><p>原請求結果尚未確認。會使用原確認內容核對，請勿另開相同操作。</p><p>追蹤碼：{recovery.correlation}</p></>}
        {review ? <><p>請確認本次操作；將保存目前登入者與原生稽核紀錄。</p><dl className={shell.auditGrid}><dt>操作</dt><dd>{COMMAND_LABELS[dialog]}</dd>{original.current && Object.entries(original.current.body).filter(([key]) => businessKeysForPreview(dialog).includes(key)).map(([key, value]) => <div className={styles.fact} key={key}><dt>{previewLabel[key] ?? key}</dt><dd>{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd></div>)}</dl></> : <>
          {dialog === "assign" && <><label>受指派者（必填）<input required value={draft.assignee} onChange={e => update("assignee", e.target.value)} /></label><label>指派標籤（選填）<input value={draft.role} onChange={e => update("role", e.target.value)} /></label><p>指派標籤用於案件分工，並不授予系統權限。</p></>}
          {dialog === "eligibility" && <><p>這是人工填報的資格檢查紀錄，並非模型自動判斷。</p><label>資格結果<select value={draft.eligible ? "eligible" : "ineligible"} onChange={e => update("eligible", e.target.value === "eligible")}><option value="eligible">符合資格</option><option value="ineligible">不符合資格</option></select></label><label>人工檢查理由（每行一項）<textarea value={draft.reasons} onChange={e => update("reasons", e.target.value)} /></label></>}
          {(dialog === "action" || dialog === "adjust") && <><p>依本案提案確認行動參數；既有設定會保留。</p><ParameterEditor value={draft.action} change={v => update("action", v)} disabled={busy} /></>}
          {dialog === "conflict-check" && <><label><input type="checkbox" checked={draft.overlap} onChange={e => update("overlap", e.target.checked)} />允許重疊衝突</label><label>衝突處理理由{draft.overlap ? "（必填）" : "（選填）"}<textarea required={draft.overlap} value={draft.reason} onChange={e => update("reason", e.target.value)} /></label><p>重疊檢查會核對目前同店案件；送審時仍會重新核對。</p></>}
          {dialog === "execute" && <p>依已保存的核准執行；執行時間與觀察窗由伺服器建立。</p>}
          {dialog === "evaluate" && <p>使用目前已保存的量測結果與伺服器時間評估。成熟度與證據由原生評估決定，本次不宣稱複製研究證據。</p>}
          {dialog === "submit" && <p>把已通過衝突檢查的提案送交核准；送審本身不代表已核准。</p>}
          {dialog === "unassign" && <p>取消目前案件指派，保留案件及原有歷史。</p>}
          {dialog === "outcomes" && <><p>錄入實際量測資料；這些輸入尚不代表已核驗的成效或因果證據。</p>
            {([["revenue", "量測增量營收"], ["margin", "量測增量毛利"], ["spend", "廣告支出"], ["treatment", "Treatment 店數"], ["control", "Control 店數"]] as const).map(([key, label]) => <label key={key}>{label}（必填）<input type="number" required step={key === "treatment" || key === "control" ? 1 : "any"} min={key === "treatment" || key === "control" ? 0 : undefined} value={draft[key]} onChange={e => update(key, e.target.value)} /></label>)}
            <label><input type="checkbox" checked={draft.hasControl} onChange={e => update("hasControl", e.target.checked)} />有 control 組</label><label><input type="checkbox" checked={draft.randomized} onChange={e => update("randomized", e.target.checked)} />採隨機分派</label>
            <label>預趨勢<select value={draft.pretrend} onChange={e => update("pretrend", e.target.value)}><option value="INCONCLUSIVE">尚無定論</option><option value="PASS">PASS 通過</option><option value="FAIL">FAIL 未通過</option></select></label>
            <label>評估方法<select value={draft.method} onChange={e => update("method", e.target.value)}>{["NONE", "BEFORE_AFTER", "MATCHED_BEFORE_AFTER", "DID", "SYNTHETIC_CONTROL", "RCT"].map(v => <option key={v}>{v}</option>)}</select></label><label>量測方法（選填）<input value={draft.measurement} onChange={e => update("measurement", e.target.value)} /></label></>}
          {dialog === "close" && <><label>結案處置<select value={draft.disposition} onChange={e => update("disposition", e.target.value)}><option value="KEEP">保留變更</option><option value="REVERT">恢復原狀</option><option value="ITERATE">持續迭代</option><option value="ESCALATE">提報進一步審查</option></select></label><label><input type="checkbox" checked={draft.followUp} onChange={e => update("followUp", e.target.checked)} />建立追蹤干預案</label>{draft.followUp && <label>追蹤案類型<select value={draft.followKind} onChange={e => update("followKind", e.target.value)}><option value="">沿用原類型</option>{INTERVENTION_KINDS.map(v => <option key={v}>{v}</option>)}</select></label>}</>}
          {dialog === "adjust" && <><p>原案將停止，另建有完整連結的替代案；未填計畫時間時，由伺服器安排開始時間及沿用原期間。</p><label>替代案預期結果（選填）<input value={draft.expected} onChange={e => update("expected", e.target.value)} /></label><label>替代案計畫開始（選填，依本機時區）<input type="datetime-local" value={draft.start} onChange={e => update("start", e.target.value)} /></label><label>替代案計畫結束（選填，依本機時區）<input type="datetime-local" value={draft.end} onChange={e => update("end", e.target.value)} /></label></>}
          {reasonRequired && <label>操作理由（必填）<textarea required aria-required="true" value={draft.reason} onChange={e => update("reason", e.target.value)} /></label>}
        </>}
        {durationBlocked && <p role="alert" data-testid="lifecycle-duration-insufficient">{dialog === "adjust" ? "原案期間包含時區變化，請填寫替代案計畫結束時間後再確認。" : "現有紀錄不足以核對追蹤案的預設期限；請先取消建立追蹤案。"}</p>}
        {error && <p role="alert" data-testid="lifecycle-error">{error}</p>}
        <div className={shell.modalActions}><Button disabled={busy} onClick={close}>取消</Button>{review && !unknown && <Button disabled={busy} onClick={() => { original.current = null; setReview(false); }}>返回修改</Button>}
          <Button variant={["stop", "rollback", "adjust"].includes(dialog) ? "danger" : "primary"} disabled={busy || durationBlocked} data-testid="lifecycle-submit" onClick={() => review ? void submit() : prepare()}>{busy ? "核對中…" : unknown ? "核對並重送原操作" : review ? "確認並提交操作" : "下一步：確認操作"}</Button></div>
      </div></div></div>}
  </>;
}
const previewLabel: Record<string, string> = { assignee: "受指派者", role: "指派標籤", eligible: "資格結果", reasons: "人工檢查理由", action_spec: "行動設定", allow_overlap: "允許重疊", reason: "操作理由", disposition: "結案處置", follow_up: "建立追蹤案", follow_up_kind: "追蹤類型", incremental_revenue: "量測增量營收", incremental_gross_margin: "量測增量毛利", ad_spend: "廣告支出", has_control_group: "有 control 組", randomized: "隨機分派", pretrend_status: "預趨勢", treatment_store_count: "Treatment 店數", control_store_count: "Control 店數", evaluation_method: "評估方法", measurement_method: "量測方法", expected_outcome: "預期結果", planned_start: "計畫開始", planned_end: "計畫結束" };
const businessKeysForPreview = (_command: LifecycleCommand) => Object.keys(previewLabel);
