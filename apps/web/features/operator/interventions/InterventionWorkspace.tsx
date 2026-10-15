"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Button, SectionPanel, StatusBadge } from "../components";
import shell from "../operator.module.css";
import styles from "./interventions.module.css";
import { LifecycleCommandForms, type LifecycleLease } from "./LifecycleCommandForms";
import { INTERVENTION_KINDS, INTERVENTION_STATUSES, id, object, makeAttempt, mayClaimCausal, mayClaimEffect, compareNativeInstants, currentEvidenceGrade, evaluationTimeUnverifiedNow, observationWindowMatureNow, parseActorScope, parseIntervention, parseOwnedRead, readbackMatches, receiptMatches, sameScope, type ActorScope, type Attempt, type DecisionAction, type Intervention } from "./interventionContract";

export type InterventionWorkspaceProps = { selectedId?: string; onSelect: (id?: string) => void; onDecisionConfirmed?: () => Promise<void> | void };
const timeline = [
  ["Triggered", "create"], ["Eligibility", "check_eligibility"], ["Action built", "propose_action"], ["Conflict checked", "check_conflict"],
  ["Approved", "approve", "reject"], ["Executed", "execute"], ["Observation Window", "start_observation"], ["Outcome", "collect_outcome"],
  ["Effect evaluated", "evaluate_effect"], ["Closed", "close", "stop", "rollback"],
];
const refusalText: Record<string, string> = {
  APPROVAL_PERMISSION_REQUIRED: "目前登入身分沒有干預核決權限。", APPROVAL_SESSION_REQUIRED: "目前登入工作階段無法進行高風險核決。",
  APPROVAL_PRINCIPAL_CHANGED: "登入授權範圍已更新，請重新載入審查。", PENDING_APPROVAL_REQUIRED: "案件尚未待核准，或已完成核決。", CREATOR_VERIFICATION_REQUIRED: "建立者身分尚未完成驗證，暫停核准。",
  CREATOR_APPROVAL_FORBIDDEN: "建立者不能核准自己的案件。", UNRESOLVED_CONFLICT: "需先解決或覆寫衝突。",
};
class ScopeChanged extends Error {}
const field = (value: unknown) => value === null || value === undefined || value === "" ? "尚無紀錄" : typeof value === "object" ? JSON.stringify(value) : String(value);
function Facts({ values }: { values: [string, unknown][] }) {
  return <dl className={shell.auditGrid}>{values.map(([label, value]) => <div className={styles.fact} key={label}><dt>{label}</dt><dd>{field(value)}</dd></div>)}</dl>;
}
function Badge({ value }: { value: string }) { return <StatusBadge tone={value === "PENDING_APPROVAL" ? "warning" : value === "APPROVED" || value === "COMPLETED" ? "success" : "neutral"}>◇ {value}</StatusBadge>; }
export function InterventionWorkspace({ selectedId, onSelect, onDecisionConfirmed }: InterventionWorkspaceProps) {
  const [row, setRow] = useState<Intervention | null>(null), [rows, setRows] = useState<Intervention[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error" | "permission">("loading");
  const [error, setError] = useState<string | null>(null), [reason, setReason] = useState("");
  const [dialog, setDialog] = useState<DecisionAction | null>(null), [busy, setBusy] = useState(false);
  const [confirmed, setConfirmed] = useState<string | null>(null), [unknown, setUnknown] = useState(false), [reload, setReload] = useState(0);
  const scopeRef = useRef<ActorScope | null>(null), epoch = useRef(0), busyRef = useRef(false), attempt = useRef<Attempt | null>(null);
  const previewId = typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("preview");
  const preview = rows.find(item => item.intervention_id === previewId);
  const targetRef = useRef(selectedId), dialogRef = useRef<HTMLDivElement>(null), previousFocus = useRef<HTMLElement | null>(null);
  targetRef.current = selectedId;
  const viewNowMs = Date.now();
  const [operationLocked, setOperationLocked] = useState(false), [operationRevision, setOperationRevision] = useState(0);
  const operation = useRef<{ key: string; owner: "approval" | "lifecycle"; opening: boolean } | null>(null);
  const rowOwner = useRef(row?.store_id); rowOwner.current = row?.store_id;
  // A version advance belongs to the same owner. It cannot discard an unknown
  // command's frozen body, context, key or correlation ID.
  const ownerLeaseKey = JSON.stringify([epoch.current, typeof window === "undefined" ? "" : window.location.href, selectedId, row?.intervention_id, row?.store_id,
    row?.review_context?.intervention_id, row?.review_context?.store_id, row?.review_context?.tenant_id, row?.review_context?.actor_subject_id,
    scopeRef.current?.actorSubjectId, scopeRef.current?.tenantId, scopeRef.current?.expiresAt]);
  useEffect(() => {
    const current = operation.current;
    if (!current) return;
    if (current.key !== ownerLeaseKey || status !== "ready" || current.owner === "approval" && !current.opening && !dialog && !unknown && !busy) {
      operation.current = null; setOperationLocked(false);
    }
  }, [ownerLeaseKey, status, dialog, unknown, busy, operationRevision]);
  async function openApprovalOwned(action: DecisionAction) {
    if (busyRef.current || operation.current && operation.current.key === ownerLeaseKey && (operation.current.owner !== "approval" || operation.current.opening)) return;
    const token = operation.current?.key === ownerLeaseKey ? operation.current : { key: ownerLeaseKey, owner: "approval" as const, opening: false };
    token.opening = true; operation.current = token; setOperationLocked(true);
    try { await openDecision(action); }
    finally { if (operation.current === token) { token.opening = false; setOperationRevision(value => value + 1); } }
  }
  const lifecycleLease: LifecycleLease | null = row && scopeRef.current ? (() => {
    const generation = epoch.current, href = window.location.href, target = selectedId, expected = { ...scopeRef.current! }, storeId = row.store_id;
    const assert = () => {
      try { owned(generation, href, target); }
      catch (exc) { if (epoch.current === generation && window.location.href !== href) invalidate(); throw exc; }
      if (rowOwner.current !== storeId || !sameScope(scopeRef.current, expected) || expected.expiresAt <= Date.now() / 1000) {
        if (epoch.current === generation && rowOwner.current === storeId) invalidate();
        throw new ScopeChanged("LIFECYCLE_OWNER_CHANGED");
      }
    };
    return { key: ownerLeaseKey, scope: expected, caseId: row.intervention_id, storeId, assert,
      verify: async () => { assert(); await checkScope(generation, href, target, expected); assert(); },
      json: async (url, init) => { assert(); const body = await guardedJson(url, expected, generation, href, target, init); assert(); return body; } };
  })() : null;
  const acquireLifecycle = () => {
    if (!lifecycleLease || busyRef.current || dialog || unknown || operation.current?.key === ownerLeaseKey) return false;
    lifecycleLease.assert(); operation.current = { key: ownerLeaseKey, owner: "lifecycle", opening: false }; setOperationLocked(true); return true;
  };
  const releaseLifecycle = () => {
    if (operation.current?.key === ownerLeaseKey && operation.current.owner === "lifecycle") { operation.current = null; setOperationLocked(false); }
  };

  const invalidate = () => {
    epoch.current += 1; scopeRef.current = null; attempt.current = null; busyRef.current = false;
    setRow(null); setRows([]); setDialog(null); setReason(""); setBusy(false); setUnknown(false); setConfirmed(null);
    setStatus("permission"); setError("登入身分或門市範圍已變更，請重新載入案件。");
  };
  async function principal(): Promise<ActorScope> {
    const response = await fetch("/auth/operator-context", { cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(15_000) });
    const body: unknown = await response.json(); const value = response.ok ? parseActorScope(body) : null;
    if (!value) throw new ScopeChanged("AUTHENTICATED_ACTOR_TENANT_REQUIRED");
    return value;
  }
  function owned(generation: number, href: string, target: string | undefined) {
    if (epoch.current !== generation || window.location.href !== href || targetRef.current !== target) throw new ScopeChanged("INTERVENTION_SELECTION_CHANGED");
  }
  async function checkScope(generation: number, href: string, target: string | undefined, expected: ActorScope) {
    owned(generation, href, target); const current = await principal(); owned(generation, href, target);
    if (!sameScope(current, expected)) { invalidate(); throw new ScopeChanged("ACTOR_TENANT_CONTEXT_CHANGED"); }
  }
  async function guardedJson(url: string, expected: ActorScope, generation: number, href: string, target: string | undefined, init?: RequestInit) {
    await checkScope(generation, href, target, expected);
    const response = await fetch(url, { ...init, cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(20_000) });
    await checkScope(generation, href, target, expected);
    const body: unknown = await response.json();
    await checkScope(generation, href, target, expected);
    if (!response.ok) {
      const code = object(body) && object(body.error) ? body.error.code : object(body) ? body.detail : undefined;
      throw new Error(`${response.status}: ${field(code)}`);
    }
    return body;
  }
  useEffect(() => {
    const generation = ++epoch.current, href = window.location.href, target = selectedId;
    attempt.current = null; busyRef.current = false; scopeRef.current = null; setRow(null); setRows([]); setDialog(null); setReason(""); setBusy(false); setConfirmed(null); setUnknown(false); setError(null); setStatus("loading");
    let disposed = false;
    void (async () => {
      try {
        const current = await principal(); owned(generation, href, target); scopeRef.current = current;
        const params = new URLSearchParams(window.location.search), filter = new URLSearchParams();
        for (const key of ["store_id", "status", "kind"]) { const value = params.get(key); if (value) filter.set(key, value); }
        const body = await guardedJson(target ? `/api/v1/interventions/${encodeURIComponent(target)}` : `/api/v1/interventions?${filter}`, current, generation, href, target);
        if (target) {
          const currentRow = parseOwnedRead(body, current, target); if (!currentRow) throw new Error("INTERVENTION_CANONICAL_READ_INVALID"); setRow(currentRow);
        } else {
          if (!object(body) || !Array.isArray(body.items) || body.count !== body.items.length) throw new Error("INTERVENTION_LIST_INVALID");
          const currentRows = body.items.map(parseIntervention);
          if (currentRows.some(item => !item) || new Set(currentRows.map(item => item?.intervention_id)).size !== currentRows.length) throw new Error("INTERVENTION_LIST_INVALID");
          const conflictFilter = params.get("has_conflict"), windowFilter = params.get("window_due"), from = params.get("from"), to = params.get("to");
          if (conflictFilter && !["true", "false"].includes(conflictFilter) || windowFilter && !["due", "future", "not_open"].includes(windowFilter)
            || from && (!/^\d{4}-\d{2}-\d{2}$/.test(from) || !Number.isFinite(Date.parse(from)))
            || to && (!/^\d{4}-\d{2}-\d{2}$/.test(to) || !Number.isFinite(Date.parse(to))) || from && to && from > to) throw new Error("INTERVENTION_FILTER_INVALID");
          const sort = params.get("sort") ?? "created_at", direction = params.get("direction") ?? "descending";
          if (!["intervention_id", "kind", "created_at"].includes(sort) || !["ascending", "descending"].includes(direction)) throw new Error("INTERVENTION_SORT_INVALID");
          setRows((currentRows as Intervention[]).filter(item => (!conflictFilter || Boolean(item.conflict?.has_conflict) === (conflictFilter === "true"))
            && (!windowFilter || windowFilter === "not_open" ? !windowFilter || !item.observation_window : Boolean(item.observation_window)
              && (windowFilter === "due" ? observationWindowMatureNow(item) : !observationWindowMatureNow(item)))
            && (!from || compareNativeInstants(item.created_at, from + "T00:00:00Z")! >= 0) && (!to || compareNativeInstants(item.created_at, new Date(Date.parse(to + "T00:00:00Z") + 86400000).toISOString())! < 0)).sort((a, b) => String(a[sort as "intervention_id" | "kind" | "created_at"]).localeCompare(String(b[sort as "intervention_id" | "kind" | "created_at"])) * (direction === "ascending" ? 1 : -1)));
        }
        setStatus("ready");
      } catch (exc) {
        if (disposed || epoch.current !== generation) return;
        if (exc instanceof ScopeChanged) { invalidate(); return; }
        setStatus(String(exc).includes("403") ? "permission" : "error"); setError(String(exc));
      }
    })();
    const observe = async () => {
      if (disposed || epoch.current !== generation || !scopeRef.current) return;
      try { await checkScope(generation, href, target, scopeRef.current); }
      catch { if (!disposed && epoch.current === generation) invalidate(); }
    };
    const timer = window.setInterval(() => void observe(), 1500);
    const changed = () => { if (!disposed && epoch.current === generation) invalidate(); };
    window.addEventListener("focus", observe); window.addEventListener("popstate", changed); window.addEventListener("operator-auth-changed", changed);
    // Storage is an invalidation signal only; its values never grant authority.
    window.addEventListener("storage", changed);
    return () => { disposed = true; if (epoch.current === generation) epoch.current += 1; window.clearInterval(timer); window.removeEventListener("focus", observe); window.removeEventListener("popstate", changed); window.removeEventListener("operator-auth-changed", changed); window.removeEventListener("storage", changed); };
  }, [selectedId, reload]);
  useEffect(() => {
    if (!dialog && !preview) return;
    previousFocus.current = document.activeElement as HTMLElement | null;
    dialogRef.current?.querySelector<HTMLElement>("textarea, button")?.focus();
    return () => previousFocus.current?.focus();
  }, [dialog, preview]);
  function decisionAuthority(value: Intervention) {
    const p = value.approval_permissions;
    return Boolean(p && (p.approve || p.reject || p.reject_refusal === "PENDING_APPROVAL_REQUIRED"));
  }
  async function openDecision(action: DecisionAction) {
    if (!row || !scopeRef.current || busyRef.current) return;
    const generation = epoch.current, href = window.location.href, target = selectedId;
    try {
      const body = await guardedJson(`/api/v1/interventions/${encodeURIComponent(row.intervention_id)}`, scopeRef.current, generation, href, target);
      const fresh = parseOwnedRead(body, scopeRef.current, row.intervention_id);
      if (!fresh) throw new Error("INTERVENTION_CANONICAL_READ_INVALID");
      setRow(fresh);
      if (attempt.current) {
        if (!decisionAuthority(fresh)) throw new Error("APPROVAL_PERMISSION_REQUIRED：目前登入者無法核對原核決請求。");
        setDialog(attempt.current.body.action); return;
      }
      if (!fresh.approval_permissions?.[action === "APPROVE" ? "approve" : "reject"]) throw new Error("APPROVAL_PERMISSION_REQUIRED");
      setError(null); setDialog(action);
    } catch (exc) { if (epoch.current === generation) { if (exc instanceof ScopeChanged) invalidate(); else setError(String(exc)); } }
  }
  async function submit() {
    if (!row || !scopeRef.current || !dialog || busyRef.current) return;
    if (!reason.trim() && !attempt.current) { setError("決策理由為必填。"); return; }
    const generation = epoch.current, href = window.location.href, target = selectedId, current = scopeRef.current;
    busyRef.current = true; setBusy(true); setError(null);
    try {
      await checkScope(generation, href, target, current);
      if (!attempt.current) {
        const freshBody = await guardedJson(`/api/v1/interventions/${encodeURIComponent(row.intervention_id)}`, current, generation, href, target);
        const fresh = parseOwnedRead(freshBody, current, row.intervention_id);
        if (!fresh || fresh.version !== row.version || fresh.review_context?.snapshot_sha256 !== row.review_context?.snapshot_sha256) throw new Error("APPROVAL_READ_CONTEXT_CONFLICT：案件已變更，請重新載入。");
        attempt.current = makeAttempt(fresh, current, dialog, reason);
      } else {
        const freshBody = await guardedJson(`/api/v1/interventions/${encodeURIComponent(row.intervention_id)}`, current, generation, href, target);
        const fresh = parseOwnedRead(freshBody, current, row.intervention_id);
        if (!fresh || !decisionAuthority(fresh)) throw new Error("APPROVAL_PERMISSION_REQUIRED：目前登入者無法重送原核決請求。");
      }
      const original = attempt.current;
      if (!sameScope(original.scope, current)) { invalidate(); throw new ScopeChanged(); }
      const result = await guardedJson(`/api/v1/interventions/${encodeURIComponent(row.intervention_id)}/approve`, current, generation, href, target,
        { method: "POST", body: original.serialized, headers: { "Content-Type": "application/json", "If-Match": `W/"${original.body.expected_version}"`, "If-Actor-Match": original.body.expectedActorSubjectId, "Idempotency-Key": original.key, "X-Correlation-Id": original.correlation } });
      if (!receiptMatches(result, row, original)) throw new Error("APPROVAL_RECEIPT_INVALID：原核決結果尚未確認。");
      const read = await guardedJson(`/api/v1/interventions/${encodeURIComponent(row.intervention_id)}`, current, generation, href, target);
      const persisted = parseOwnedRead(read, current, row.intervention_id);
      if (!persisted || !readbackMatches(persisted, result, original)) throw new Error("APPROVAL_READBACK_MISMATCH：核決與稽核尚未完成核對。");
      await checkScope(generation, href, target, current);
      // A successful receipt alone cannot refresh Today. Only this complete
      // readback and current-scope observation reaches the callback.
      await onDecisionConfirmed?.();
      await checkScope(generation, href, target, current);
      setRow(persisted); setConfirmed(`核決已保存並完成稽核核對 · ${original.correlation}`); setDialog(null); setUnknown(false); attempt.current = null;
    } catch (exc) {
      if (epoch.current === generation) {
        if (exc instanceof ScopeChanged) invalidate();
        else { setError(`${String(exc)}${attempt.current ? ` · correlation_id: ${attempt.current.correlation}` : ""}`); setUnknown(Boolean(attempt.current)); }
      }
    } finally { if (epoch.current === generation) { busyRef.current = false; setBusy(false); } }
  }
  function trap(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape" && !busyRef.current) { event.preventDefault(); if (dialog) setDialog(null); else filter("preview", ""); }
    if (event.key !== "Tab") return;
    const nodes = [...(dialogRef.current?.querySelectorAll<HTMLElement>("button:not([disabled]), textarea:not([disabled]), input:not([disabled]), [tabindex='0']") ?? [])];
    if (!nodes.length) return;
    const first = nodes[0], last = nodes[nodes.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  function filter(key: string, value: string) {
    const url = new URL(window.location.href); value ? url.searchParams.set(key, value) : url.searchParams.delete(key);
    window.history.replaceState(null, "", url); setReload(v => v + 1);
  }
  const refresh = () => setReload(v => v + 1);
  const sortBy = (key: string) => {
    const url = new URL(window.location.href); url.searchParams.set("sort", key);
    url.searchParams.set("direction", url.searchParams.get("direction") === "ascending" ? "descending" : "ascending");
    window.history.replaceState(null, "", url); setReload(v => v + 1);
  };
  const sortDirection = (key: string): "none" | "ascending" | "descending" => {
    const params = new URLSearchParams(window.location.search);
    return (params.get("sort") ?? "created_at") === key ? params.get("direction") === "ascending" ? "ascending" : "descending" : "none";
  };
  return <div className={styles.workspace} data-testid="native-intervention-owner">
    <header className={shell.workspaceHeader}><div><p>營運 Operations › Interventions{row ? ` › ${row.intervention_id}` : ""}</p><h1>{selectedId ? "干預生命週期詳情" : "干預工作台"}</h1><p>分流各店干預案、處理衝突、核准、觀察與效果評估。</p></div><div>{row && <Badge value={row.status} />}<Button onClick={() => onSelect()}>回到干預佇列</Button><Button onClick={refresh} disabled={busy || unknown || operationLocked}>重新載入</Button></div></header>
    <p className={styles.mobileHint}>完整生命週期與模型證據建議於桌機檢視；核決理由與二次確認同樣必須完成。</p>
    {error && <div role="alert" data-testid="intervention-error" className={shell.warningBlock}>{error}{unknown && <p>原請求結果尚未確認。再次提交會核對目前登入身分並重送原始內容、版本與識別碼。</p>}</div>}
    {confirmed && <div role="status" data-testid="intervention-confirmed" className={shell.successBlock}>{confirmed}</div>}
    {status === "loading" && <div role="status" aria-busy="true">干預案件與生命週期資料載入中…</div>}
    {(status === "error" || status === "permission") && <Button onClick={refresh}>重試</Button>}
    {status === "ready" && !selectedId && <SectionPanel title="干預佇列"><div className={styles.filters}>
      <label>門市<input aria-label="門市篩選" defaultValue={new URLSearchParams(window.location.search).get("store_id") ?? ""} onBlur={e => filter("store_id", e.target.value)} /></label>
      <label>狀態<select aria-label="狀態篩選" value={new URLSearchParams(window.location.search).get("status") ?? ""} onChange={e => filter("status", e.target.value)}><option value="">全部</option>{INTERVENTION_STATUSES.map(value => <option key={value}>{value}</option>)}</select></label>
      <label>類型<select aria-label="類型篩選" value={new URLSearchParams(window.location.search).get("kind") ?? ""} onChange={e => filter("kind", e.target.value)}><option value="">全部</option>{INTERVENTION_KINDS.map(value => <option key={value}>{value}</option>)}</select></label>
      <label>衝突<select aria-label="衝突篩選" value={new URLSearchParams(window.location.search).get("has_conflict") ?? ""} onChange={e => filter("has_conflict", e.target.value)}><option value="">全部</option><option value="true">有衝突</option><option value="false">無衝突</option></select></label>
      <label>觀察窗<select aria-label="觀察窗篩選" value={new URLSearchParams(window.location.search).get("window_due") ?? ""} onChange={e => filter("window_due", e.target.value)}><option value="">全部</option><option value="due">已達成熟時間</option><option value="future">觀察中</option><option value="not_open">尚未開啟</option></select></label>
      <label>建立日期（起）<input type="date" aria-label="建立日期起" value={new URLSearchParams(window.location.search).get("from") ?? ""} onChange={e => filter("from", e.target.value)} /></label>
      <label>建立日期（迄）<input type="date" aria-label="建立日期迄" value={new URLSearchParams(window.location.search).get("to") ?? ""} onChange={e => filter("to", e.target.value)} /></label>
    </div><p>符合目前範圍 {rows.length} 件 · 待核准 {rows.filter(item => item.status === "PENDING_APPROVAL").length} 件 · 觀察中 {rows.filter(item => item.status === "OBSERVING").length} 件 · 有衝突 {rows.filter(item => item.conflict?.has_conflict).length} 件</p>{rows.length === 0 ? <p>目前範圍尚無干預紀錄。</p> : <div className={styles.tableWrap}><table><thead><tr><th aria-sort={sortDirection("intervention_id")}><button type="button" onClick={() => sortBy("intervention_id")}>干預／門市</button></th><th aria-sort={sortDirection("kind")}><button type="button" onClick={() => sortBy("kind")}>類型與狀態</button></th><th aria-sort={sortDirection("created_at")}><button type="button" onClick={() => sortBy("created_at")}>觸發／建立者</button></th><th>衝突</th><th>觀察窗／證據</th><th>動作</th></tr></thead><tbody>{rows.map(item => <tr key={item.intervention_id}><td>{item.intervention_id}<br />{item.store_id}</td><td>{item.kind}<br /><Badge value={item.status} /></td><td>{item.trigger_ref}<br />{item.created_by}</td><td>{item.conflict?.has_conflict ? `⚠ ${item.conflict.conflicting_ids.length} 件 · ${item.conflict.blocks_approval ? "阻擋核准" : "已解決"}` : "未記錄衝突"}</td><td>{item.observation_window?.label_maturity_time ?? "尚未開啟觀察窗"}<br />{mayClaimEffect(item, viewNowMs) ? item.effect?.evidence_level : "尚無成熟成效"}</td><td><Button onClick={() => filter("preview", item.intervention_id)}>快速查看</Button><Button onClick={() => onSelect(item.intervention_id)}>開啟生命週期詳情</Button></td></tr>)}</tbody></table></div>}</SectionPanel>}
    {status === "ready" && row && lifecycleLease && <LifecycleCommandForms key={lifecycleLease.key} row={row} lease={lifecycleLease} locked={operationLocked || Boolean(dialog) || busy || unknown} acquire={acquireLifecycle} release={releaseLifecycle}
      onConfirmed={async persisted => { await lifecycleLease.verify(); await onDecisionConfirmed?.(); await lifecycleLease.verify(); setRow(persisted); }}>
      {life => <>
      <SectionPanel title="1. Summary"><Facts values={[["門市", row.store_id], ["Treatment", row.kind], ["目前狀態", row.status], ["預期結果", row.expected_outcome], ["觸發來源", row.trigger_ref], ["建立者", row.created_by], ["資料更新", row.read_at]]} />{life.section(1)}</SectionPanel>
      <SectionPanel title="2. Status & Timeline"><ol className={styles.timeline}>{timeline.map(([label, ...actions]) => { const nodes = row.history.filter(t => actions.includes(t.action)); return <li key={label}><strong>◇ {label}</strong>{nodes.length ? nodes.map((node, i) => <p key={i}>{node.to_status} · {node.actor} · <time dateTime={node.at}>{node.at}</time><br />{node.reason || "未記錄理由"}</p>) : <p>{label === "Triggered" ? `${row.created_by} · ${row.created_at}` : "尚無紀錄"}</p>}</li>; })}</ol></SectionPanel>
      <SectionPanel title="3. Eligibility" eyebrow="資格檢查紀錄 · 與模型建議分離"><p>{row.eligibility ? `${row.eligibility.eligible ? "✓ 符合資格" : "⛔ 不符合資格"} · ${row.eligibility.checked_at}` : "尚未完成資格檢查"}</p>{row.eligibility?.reasons.map(value => <p key={value}>{value}</p>)}<p>政策：{row.policy_version}</p>{life.section(3)}</SectionPanel>
      <SectionPanel title="4. Action Proposed"><Facts values={Object.entries(row.action_spec)} />{life.section(4)}</SectionPanel>
      <SectionPanel title="5. Conflict Control">{row.conflict ? <><p>{row.conflict.blocks_approval ? "⚠ 需先解決或覆寫衝突" : row.conflict.has_conflict ? "✓ 衝突已有解決紀錄" : "✓ 未發現重疊衝突"}</p><Facts values={[["檢查時間", row.conflict.checked_at], ["衝突類型", row.conflict.conflicting_kinds.join("、")], ["解決理由", row.conflict.resolution_reason]]} /><ul>{row.conflict.conflicting_ids.map(value => <li key={value}><button type="button" onClick={() => onSelect(value)}>{value}</button></li>)}</ul></> : <p>尚未完成衝突檢查</p>}{life.section(5)}</SectionPanel>
      <section className={styles.approvalSection}><SectionPanel title="6. Approval" eyebrow="人工決策 · 與系統建議分離">
        {row.approval ? <Facts values={[["核決", row.approval.approved ? "✓ 核准" : "⛔ 退回"], ["actor_id", row.approval.actor_id], ["決策理由", row.approval.decision_reason], ["決策時間", row.approval.approved_at], ["政策版本", row.approval.policy_version], ["correlation_id", row.approval_audit?.correlation_id], ["audit_event_id", row.approval_audit?.event_id]]} /> : <p>尚無人工核決紀錄。</p>}
        {row.approval_permissions?.approve_refusal && <p>{refusalText[row.approval_permissions.approve_refusal] ?? row.approval_permissions.approve_refusal}</p>}
        {row.approval_permissions?.reject_refusal && row.approval_permissions.reject_refusal !== row.approval_permissions.approve_refusal && <p>{refusalText[row.approval_permissions.reject_refusal] ?? row.approval_permissions.reject_refusal}</p>}
        <div className={styles.actions}>{unknown ? <Button disabled={!decisionAuthority(row) || operation.current?.owner === "lifecycle" || operation.current?.opening} onClick={() => void openApprovalOwned(attempt.current?.body.action ?? "APPROVE")}>核對原核決結果</Button> : <>{row.approval_permissions?.approve && <Button variant="primary" disabled={busy || operationLocked} onClick={() => void openApprovalOwned("APPROVE")}>核准干預</Button>}{row.approval_permissions?.reject && <Button variant="danger" disabled={busy || operationLocked} onClick={() => void openApprovalOwned("REJECT")}>退回干預</Button>}</>}</div>
        {life.section(6)}
      </SectionPanel></section>
      <SectionPanel title="7. Execution & Observation Window"><Facts values={[["計畫開始", row.planned_start], ["計畫結束", row.planned_end], ["預設觀察窗（天）", `${row.window_spec.outcome_window_days} + ${row.window_spec.maturity_buffer_days}`], ["執行者", row.execution?.executor], ["執行時間", row.execution?.executed_at], ["觀察窗開啟", row.observation_window?.opened_at], ["結果窗截止", row.observation_window?.outcome_window_end], ["成熟時間", row.observation_window?.label_maturity_time]]} /><p>{row.observation_window && row.execution ? observationWindowMatureNow(row, viewNowMs) ? "✓ 已達紀錄的成熟時間；仍須核對最新成效評估。" : "◷ 觀察窗成熟度尚未核驗，目前不宣稱成效。" : "尚未開啟觀察窗。"}</p>{life.section(7)}</SectionPanel>
      <SectionPanel title="8. Outcome & Effect"><div className={shell.warningBlock}><strong>證據限制</strong>{row.effect?.limitations.length ? row.effect.limitations.map(value => <p key={value}>{value}</p>) : <p>{row.effect ? "評估未列出限制" : "尚無成熟效果評估"}</p>}{evaluationTimeUnverifiedNow(row, viewNowMs) && <p data-testid="intervention-future-evaluation">保存的評估時間尚未由目前資料核驗；目前不宣稱成效。</p>}<p>{mayClaimCausal(row, viewNowMs) ? "✓ 原生證據符合因果宣稱閘門" : "僅描述，不可宣稱因果。"}</p></div><Facts values={[["目前可宣稱證據等級", currentEvidenceGrade(row, viewNowMs) ?? "尚未通過目前成熟度與評估核對"], ["保存的評估紀錄", row.effect ? `${row.effect.evidence_level} · 評估於 ${row.effect.evaluated_at}` : null], ["建議", row.effect?.recommendation], ["預趨勢", row.effect?.pretrend_status], ["評估方法", row.effect?.evaluation_method], ["Treatment / Control 店數", row.outcome ? `${field(row.outcome.treatment_store_count)} / ${field(row.outcome.control_store_count)}` : null]]} />{mayClaimEffect(row, viewNowMs) && row.effect ? <Facts values={[["增量營收", row.effect.incremental_revenue], ["增量毛利", row.effect.incremental_gross_margin], ["iROMI", row.effect.iromi]]} /> : <p data-testid="intervention-effect-withheld">觀察與證據尚未通過成效閘門；增量數值不顯示。</p>}{life.section(8)}</SectionPanel>
      <SectionPanel title="9. Version / Audit"><Facts values={[["Policy version", row.policy_version], ["Feature version", row.feature_version], ["案件版本", row.version], ["資料核對時間", row.read_at]]} /><p>模型版本及特徵快照未由此生命週期回傳。</p><ol>{row.history.map((entry, index) => <li key={index}><Facts values={[["狀態", `${entry.from_status} → ${entry.to_status}`], ["Actor", entry.actor], ["Action", entry.action], ["Reason", entry.reason], ["Timestamp", entry.at], ["Correlation", entry.correlation_id]]} /></li>)}</ol></SectionPanel>
      </>}
    </LifecycleCommandForms>}
    {preview && !selectedId && !dialog && status === "ready" && <div className={shell.modalBackdrop}><div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="intervention-preview-title" className={shell.modal} onKeyDown={trap}><header className={shell.modalHeader}><h2 id="intervention-preview-title">干預快速查看</h2><Button onClick={() => filter("preview", "")}>關閉</Button></header><Badge value={preview.status} /><Facts values={[["干預", preview.intervention_id], ["門市", preview.store_id], ["類型", preview.kind], ["預期結果", preview.expected_outcome], ["建立者", preview.created_by], ["衝突", preview.conflict?.blocks_approval ? "⚠ 阻擋核准" : preview.conflict?.has_conflict ? "已解決衝突" : "未記錄衝突"], ["觀察窗成熟時間", preview.observation_window?.label_maturity_time]]} /><p>核准與退回需進入完整 ApprovalPanel 審查。</p><div className={shell.modalActions}><Button disabled={rows.indexOf(preview) === 0} onClick={() => filter("preview", rows[rows.indexOf(preview)-1].intervention_id)}>上一筆</Button><Button disabled={rows.indexOf(preview) === rows.length-1} onClick={() => filter("preview", rows[rows.indexOf(preview)+1].intervention_id)}>下一筆</Button><Button variant="primary" onClick={() => onSelect(preview.intervention_id)}>開啟生命週期詳情</Button></div></div></div>}
    {dialog && <div className={shell.modalBackdrop}><div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="intervention-confirm-title" className={shell.modal} onKeyDown={trap} data-testid="intervention-decision-dialog"><header className={shell.modalHeader}><h2 id="intervention-confirm-title">確認{dialog === "APPROVE" ? "核准" : "退回"}干預</h2></header><div className={shell.modalForm}><p>案件：{row?.intervention_id} · 版本 {attempt.current?.body.expected_version ?? row?.version}</p><p>核決會保存目前登入者、理由、政策版本與稽核紀錄。</p><label>決策理由（必填）<textarea required aria-required="true" aria-invalid={!reason.trim()} data-testid="intervention-reason" value={reason} disabled={busy} onChange={e => setReason(e.target.value)} /></label>{unknown && <p>原核決尚未確認；將重送原始理由：{attempt.current?.body.reason}</p>}{error && <p role="alert">{error}</p>}<div className={shell.modalActions}><Button disabled={busy} onClick={() => setDialog(null)}>取消</Button><Button data-testid="intervention-submit" variant="primary" disabled={busy} onClick={() => void submit()}>{busy ? "核對中…" : unknown ? "核對並重送原請求" : "確認並提交核決"}</Button></div></div></div></div>}
  </div>;
}
