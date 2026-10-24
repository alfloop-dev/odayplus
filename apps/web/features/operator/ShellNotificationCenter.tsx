"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Drawer } from "@oday-plus/ui";
import { Button, Chip, StatusBadge, type Tone } from "./components";
import { payloadContainsSeedData } from "./operatorDataMode";
import { WORKSPACES } from "./navigation";
import type { ShellTarget } from "./TodayWorkspace";
import styles from "./operator.module.css";
import pageStyles from "./notificationsPage.module.css";

type Category = "alert" | "approval" | "job" | "system";
type ReadState = "all" | "unread" | "read";
type Row = {
  id: string;
  title: string;
  detail: string;
  tone: Tone;
  severity: "critical" | "warning" | "info";
  category: Category | null;
  occurredAt: string | null;
  acknowledged: boolean;
  target: ShellTarget | null;
  sourceId: string | null;
  sourceUnavailable: string | null;
  task: { id: string; requestKey: string } | null;
  reportId: string | null;
};
type Inbox = { rows: Row[]; unread: number; canAcknowledge: boolean; approvalCoverage?: "partial" | "unavailable" };
type Lease = { scope: string; active: boolean; keys: Map<string, string> };

export type ShellNotificationCenterProps = {
  scopeKey: string;
  headers: Record<string, string>;
  createAuthenticatedReadGuard: (current: () => boolean) => () => Promise<boolean>;
  onConfirmedUnreadCount: (count: number) => void;
  onTargetSelect: (target: ShellTarget, label: string) => void;
  allowFixtureFallback?: boolean;
  fixtureRows?: readonly unknown[];
  fullPage?: boolean;
  onTaskSelect?: (taskId: string, requestKey: string) => void;
  onReportSelect?: (reportId: string) => void;
};

const categories = [
  { id: "all", label: "全部" }, { id: "alert", label: "警示" },
  { id: "approval", label: "核准" }, { id: "job", label: "Job" },
  { id: "system", label: "系統" },
] as const;
const tones = new Set<Tone>(["neutral", "info", "success", "warning", "danger", "accent"]);
function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function target(value: unknown): ShellTarget | null {
  if (value === undefined || value === null) return null;
  if (!object(value) || !WORKSPACES.some((item) => item.id === value.workspace)
    || value.entityId !== undefined && (typeof value.entityId !== "string" || !value.entityId.trim())
    || value.tab !== undefined && typeof value.tab !== "string") throw new Error("通知來源資料格式不正確。");
  return { workspace: value.workspace as ShellTarget["workspace"],
    ...(typeof value.entityId === "string" ? { entityId: value.entityId } : {}),
    ...(typeof value.tab === "string" ? { tab: value.tab } : {}) };
}
function row(value: unknown): Row {
  if (!object(value) || typeof value.notificationId !== "string" || !value.notificationId.trim()
    || typeof value.title !== "string" || !value.title.trim() || typeof value.detail !== "string"
    || typeof value.acknowledged !== "boolean" || typeof value.tone !== "string" || !tones.has(value.tone as Tone)
    || !["critical", "warning", "info"].includes(value.severity as string)) {
    throw new Error("通知資料格式不正確。");
  }
  const category = value.category ?? null;
  const occurredAt = value.occurredAt ?? null;
  if (category !== null && !categories.some((item) => item.id !== "all" && item.id === category)
    || occurredAt !== null && (typeof occurredAt !== "string" || !occurredAt.trim() || !Number.isFinite(Date.parse(occurredAt)))) {
    throw new Error("通知分類或事件時間格式不正確。");
  }
  const source = value.sourceDetail;
  let resolved: ShellTarget | null = null;
  let task: Row["task"] = null;
  let reportId: string | null = null;
  let sourceUnavailable: string | null = "來源詳情入口未提供";
  if (source !== undefined && source !== null) {
    if (!object(source) || !["available", "unavailable"].includes(source.state as string)) throw new Error("通知來源解析格式不正確。");
    if (source.state === "available" && source.consumer === "supervisor-report") {
      if (category !== "alert" || !object(value.sourceEntity) || value.sourceEntity.kind !== "franchisee.report"
        || typeof value.sourceEntity.id !== "string" || !value.sourceEntity.id.trim()
        || value.sourceReportId !== value.sourceEntity.id || value.notificationId !== `franchisee-report:${value.sourceEntity.id}`
        || value.target !== null && value.target !== undefined) throw new Error("督導通知未提供同一已保存回報的識別。");
      reportId = value.sourceEntity.id;
      sourceUnavailable = null;
    } else if (source.state === "available" && source.consumer === "task-center") {
      if (category !== "approval" || !object(value.sourceEntity)
        || !["sitescore.decision", "intervention"].includes(value.sourceEntity.kind as string)
        || typeof value.sourceEntity.id !== "string" || !value.sourceEntity.id.trim()
        || value.sourceTaskId !== `${value.sourceEntity.kind}:${value.sourceEntity.id}`
        || typeof value.sourceRequestKey !== "string" || !/^[a-f0-9]{64}$/.test(value.sourceRequestKey)
        || value.notificationId !== `approval-request:${value.sourceRequestKey}`
        || value.target !== null && value.target !== undefined) throw new Error("核准通知未提供同次任務的來源識別。");
      task = { id: value.sourceTaskId as string, requestKey: value.sourceRequestKey };
      sourceUnavailable = null;
    } else if (source.state === "available") {
      resolved = target(value.target);
      if (!resolved?.entityId || resolved.workspace !== "store" || resolved.tab !== "forecastOps"
        || category !== "alert" || !object(value.sourceEntity) || value.sourceEntity.kind !== "forecastops.alert"
        || typeof value.sourceEntity.id !== "string" || !value.sourceEntity.id.trim()) {
        throw new Error("通知來源解析未提供目前可用的事件詳情入口。");
      }
      sourceUnavailable = null;
    } else if (source.reasonCode === "INGESTION_RUN_DETAIL_NOT_IMPLEMENTED") sourceUnavailable = "匯入執行詳情入口尚未提供";
    else sourceUnavailable = "來源事件不存在或不在目前可讀範圍";
  }
  const sourceId = object(value.sourceEntity) && typeof value.sourceEntity.id === "string" ? value.sourceEntity.id : null;
  return { id: value.notificationId, title: value.title, detail: value.detail, acknowledged: value.acknowledged,
    tone: value.tone as Tone, severity: value.severity as Row["severity"], category: category as Category | null,
    occurredAt: occurredAt as string | null, target: resolved, sourceId, sourceUnavailable, task, reportId };
}
export function normalizeNotificationInbox(value: unknown): Inbox {
  if (!object(value) || !Array.isArray(value.items) || !Number.isInteger(value.unacknowledged)
    || (value.unacknowledged as number) < 0) throw new Error("通知收件匣格式不正確。");
  const rows = value.items.map(row);
  if (new Set(rows.map((item) => item.id)).size !== rows.length) throw new Error("通知識別碼重複。");
  if (rows.filter((item) => !item.acknowledged).length !== value.unacknowledged) throw new Error("通知未讀數無法核對。");
  if (value.actions !== undefined && !object(value.actions)) throw new Error("通知操作權限格式不正確。");
  const actions = object(value.actions) ? value.actions : null;
  if (actions?.acknowledge !== undefined && !object(actions.acknowledge)) throw new Error("通知操作權限格式不正確。");
  const acknowledgement = actions && object(actions.acknowledge) ? actions.acknowledge : null;
  if (acknowledgement && typeof acknowledgement.allowed !== "boolean") throw new Error("通知操作權限格式不正確。");
  const approvalCoverage = object(value.meta) && object(value.meta.approvalCoverage) ? value.meta.approvalCoverage : undefined;
  if (approvalCoverage !== undefined && (approvalCoverage.complete !== false || !["partial", "unavailable"].includes(approvalCoverage.state as string))) throw new Error("核准通知來源覆蓋格式不正確。");
  return { rows, unread: value.unacknowledged as number, canAcknowledge: acknowledgement?.allowed === true,
    approvalCoverage: approvalCoverage?.state as Inbox["approvalCoverage"] };
}
function fixtures(values: readonly unknown[]): Row[] {
  return values.filter(object).flatMap((value, index) => {
    if (typeof value.title !== "string") return [];
    return [{ id: typeof value.id === "string" ? value.id : `fixture-notification-${index}`, title: value.title,
      detail: typeof value.detail === "string" ? value.detail : "",
      tone: tones.has(value.tone as Tone) ? value.tone as Tone : "neutral" as Tone,
      severity: value.tone === "danger" ? "critical" as const : value.tone === "warning" ? "warning" as const : "info" as const,
      category: null, occurredAt: null, acknowledged: false, target: null, sourceId: null,
      sourceUnavailable: "來源詳情入口未提供", task: null, reportId: null }];
  });
}

function readNotificationLocation() {
  const query = typeof window === "undefined" ? new URLSearchParams() : new URL(window.location.href).searchParams;
  return { category: categories.find((item) => item.id === query.get("category"))?.id ?? "all",
    read: (["all", "unread", "read"] as const).find((item) => item === query.get("read")) ?? "all",
    detail: query.get("notification") };
}
function writeNotificationLocation(patch: { category?: string; read?: string; detail?: string | null }) {
  const url = new URL(window.location.href);
  for (const [field, key] of [["category", "category"], ["read", "read"], ["detail", "notification"]] as const) {
    if (!(field in patch)) continue;
    const value = patch[field]; if (value) url.searchParams.set(key, value); else url.searchParams.delete(key);
  }
  const href = `${url.pathname}${url.search}${url.hash}`;
  if (href !== `${window.location.pathname}${window.location.search}${window.location.hash}`) window.history.pushState(null, "", href);
}

export function ShellNotificationCenter(props: ShellNotificationCenterProps) {
  const tabsId = useId();
  const latest = useRef(props); latest.current = props;
  const currentScope = useRef<Lease>({ scope: props.scopeKey, active: true, keys: new Map() });
  if (currentScope.current.scope !== props.scopeKey) {
    currentScope.current.active = false;
    currentScope.current = { scope: props.scopeKey, active: true, keys: new Map() };
  }
  const request = useRef(0);
  const [stateScope, setStateScope] = useState(props.scopeKey);
  const [inbox, setInbox] = useState<Inbox>(() => {
    const rows = props.allowFixtureFallback ? fixtures(props.fixtureRows ?? []) : [];
    return { rows, unread: rows.length, canAcknowledge: false };
  });
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error" | "fixture">(
    props.allowFixtureFallback ? "fixture" : "loading",
  );
  const [category, setCategory] = useState<Category | "all">(() => props.fullPage ? readNotificationLocation().category : "all");
  const [readState, setReadState] = useState<ReadState>(() => props.fullPage ? readNotificationLocation().read : "all");
  const [detailId, setDetailId] = useState<string | null>(() => props.fullPage ? readNotificationLocation().detail : null);
  const closeDetail = useCallback(() => { setDetailId(null); if (props.fullPage) writeNotificationLocation({ detail: null }); }, [props.fullPage]);
  useEffect(() => {
    if (!props.fullPage) return;
    const restore = () => { const next = readNotificationLocation(); setCategory(next.category); setReadState(next.read); setDetailId(next.detail); setSelected([]); };
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, [props.fullPage]);
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  function begin() {
    const scope = currentScope.current;
    const sequence = ++request.current;
    const current = () => scope.active && scope === currentScope.current && sequence === request.current;
    return { scope, current, pin: latest.current.createAuthenticatedReadGuard(current),
      headers: { ...latest.current.headers }, allowFixture: latest.current.allowFixtureFallback === true };
  }
  type Operation = ReturnType<typeof begin>;
  async function readInbox(operation: Operation, preserveSelection = false): Promise<boolean> {
    const { current, pin, headers, allowFixture } = operation;
    if (!current()) return false;
    setLoadState(allowFixture ? "fixture" : "loading"); setError(null);
    try {
      if (!await pin() || !current()) return false;
      const response = await fetch("/api/v1/operator/shell/notifications", { headers, cache: "no-store" });
      if (!await pin() || !current()) return false;
      if (!response.ok) throw new Error(`通知讀取失敗（HTTP ${response.status}）。`);
      const value: unknown = await response.json();
      if (!await pin() || !current()) return false;
      if (!allowFixture && payloadContainsSeedData(value)) throw new Error("通知來源不可使用示範資料。");
      const next = normalizeNotificationInbox(value);
      if (!current()) return false;
      setInbox(next); setLoadState("ready");
      setSelected((ids) => preserveSelection ? ids.filter((id) => next.rows.some((item) => item.id === id && !item.acknowledged)) : []);
      latest.current.onConfirmedUnreadCount(next.unread);
      return true;
    } catch (failure) {
      if (!await pin() || !current()) return false;
      if (allowFixture) {
        const rows = fixtures(latest.current.fixtureRows ?? []);
        setInbox({ rows, unread: rows.length, canAcknowledge: false }); setLoadState("fixture");
      } else {
        setInbox({ rows: [], unread: 0, canAcknowledge: false }); setLoadState("error");
        setError(failure instanceof Error ? failure.message : "通知暫時無法讀取。");
      }
      return false;
    }
  }
  useEffect(() => {
    const operation = begin();
    const initial = operation.allowFixture ? fixtures(latest.current.fixtureRows ?? []) : [];
    setStateScope(props.scopeKey); setInbox({ rows: initial, unread: initial.length, canAcknowledge: false });
    setSelected([]); setBusy(false); setMessage(null); setError(null);
    void readInbox(operation);
    return () => { ++request.current; };
    // Guard and callbacks live in latest; scope changes cancel both HTTP and JSON consumers.
  }, [props.scopeKey]);

  async function acknowledge() {
    if (stateScope !== props.scopeKey || busy || loadState !== "ready" || !inbox.canAcknowledge || selected.length === 0) return;
    const ids = selected.filter((id) => inbox.rows.some((item) => item.id === id && !item.acknowledged));
    if (ids.length === 0) return;
    const operation = begin(); const { current, pin, headers, scope, allowFixture } = operation;
    setBusy(true); setMessage(null); setError(null);
    let failure: string | null = null;
    try {
      for (const id of ids) {
        if (!await pin() || !current()) return;
        const key = scope.keys.get(id) ?? `notification-${crypto.randomUUID()}`;
        scope.keys.set(id, key);
        const response = await fetch(`/api/v1/operator/shell/notifications/${encodeURIComponent(id)}/acknowledgement`, {
          method: "POST", headers: { ...headers, "Idempotency-Key": key },
        });
        if (!await pin() || !current()) return;
        if (!response.ok) throw new Error(`通知確認失敗（HTTP ${response.status}）。`);
        const value: unknown = await response.json();
        if (!await pin() || !current()) return;
        if (!allowFixture && payloadContainsSeedData(value)) throw new Error("通知確認來源不可使用示範資料。");
        if (!object(value) || !object(value.notification) || value.notification.notificationId !== id
          || value.notification.acknowledged !== true) throw new Error("通知確認回應無法核對。");
      }
    } catch (cause) {
      if (!await pin() || !current()) return;
      failure = cause instanceof Error ? cause.message : "通知確認失敗。";
    } finally {
      if (current() && await pin() && current()) {
        // Even a partial batch is re-read. Neither rows nor Bell count are changed optimistically.
        const confirmed = await readInbox(operation, true);
        if (current()) {
          setBusy(false);
          if (failure) setError(failure);
          else if (confirmed) setMessage("已向後端重新核對通知狀態。");
        }
      }
    }
  }
  async function openSource(item: Row) {
    if ((!item.target && !item.task && !item.reportId) || stateScope !== props.scopeKey) return;
    const operation = begin();
    if (await operation.pin() && operation.current()) {
      if (item.reportId) {
        if (latest.current.onReportSelect) latest.current.onReportSelect(item.reportId);
        else window.location.assign(`/notifications/reports/${encodeURIComponent(item.reportId)}`);
      } else if (item.task) {
        if (latest.current.onTaskSelect) latest.current.onTaskSelect(item.task.id, item.task.requestKey);
        else window.location.assign(`/tasks/${encodeURIComponent(item.task.id)}?${new URLSearchParams({ view: "approvals", requestKey: item.task.requestKey })}`);
      } else if (item.target) latest.current.onTargetSelect(item.target, item.title);
    }
  }
  async function openDetail(item: Row) {
    if (!props.fullPage || stateScope !== props.scopeKey || busy || loadState === "loading") return;
    const operation = begin();
    if (await operation.pin() && operation.current()) {
      setDetailId(item.id); writeNotificationLocation({ detail: item.id });
    }
  }
  function changeCategory(next: Category | "all") {
    setCategory(next); setSelected([]);
    if (props.fullPage) writeNotificationLocation({ category: next });
  }
  const currentView = stateScope === props.scopeKey;
  const detail = currentView && (loadState === "ready" || loadState === "fixture") ? inbox.rows.find((item) => item.id === detailId) : undefined;
  const visible = (currentView && (!props.fullPage || loadState !== "loading") ? inbox.rows : []).filter((item) => (category === "all" || item.category === category)
    && (readState === "all" || item.acknowledged === (readState === "read")));
  const detailIndex = detail ? visible.findIndex((item) => item.id === detail.id) : -1;
  return <div className={props.fullPage ? pageStyles.inbox : styles.notificationPanel} data-screen-label="Notifications" aria-busy={!currentView || busy || loadState === "loading"}>
    {!props.fullPage ? <div className={styles.popoverTitle}>Notifications</div> : <p>
      目前通知包含已接入的警示與 Job。
      {inbox.approvalCoverage === "partial" ? "核准通知僅涵蓋目前可讀且具核准權限的 SiteScore / Intervention 真送審記錄；其他核准來源仍不完整。"
        : "核准通知來源尚未取得，空分類不代表沒有待核准事項。"}
      系統公告來源尚未提供。<a href="/tasks?view=approvals">查看目前待核准任務</a>
    </p>}
    {currentView && loadState === "fixture" ? <Chip>FIXTURE · 唯讀</Chip> : null}
    <div className={styles.tabBar} role="tablist" aria-label="通知分類">
      {categories.map((item, index) => <button key={item.id} type="button" role="tab" aria-selected={category === item.id}
        id={`${tabsId}-${item.id}`} aria-controls={`${tabsId}-panel`} tabIndex={category === item.id ? 0 : -1} disabled={busy}
        className={[styles.tab, category === item.id ? styles.tab_active : ""].join(" ")}
        onKeyDown={(event) => {
          const next = event.key === "ArrowRight" ? (index + 1) % categories.length
            : event.key === "ArrowLeft" ? (index + categories.length - 1) % categories.length
              : event.key === "Home" ? 0 : event.key === "End" ? categories.length - 1 : null;
          if (next === null) return;
          event.preventDefault(); changeCategory(categories[next].id);
          document.getElementById(`${tabsId}-${categories[next].id}`)?.focus();
        }}
        onClick={() => changeCategory(item.id)}>{item.label}</button>)}
    </div>
    <div role="tabpanel" id={`${tabsId}-panel`} aria-labelledby={`${tabsId}-${category}`}>
    <label>讀取狀態 <select aria-label="讀取狀態" value={readState} disabled={busy} onChange={(event) => {
      setReadState(event.target.value as ReadState); setSelected([]);
      if (props.fullPage) writeNotificationLocation({ read: event.target.value });
    }}><option value="all">全部</option><option value="unread">未讀</option><option value="read">已讀</option></select></label>
    {!currentView || loadState === "loading" ? <p role="status">正在讀取通知…</p> : null}
    {currentView && error ? <p role="alert">{error}</p> : null}
    {currentView && message ? <p role="status">{message}</p> : null}
    {currentView && loadState === "error" ? <Button size="sm" onClick={() => { setMessage(null); void readInbox(begin()); }}>重新讀取</Button> : null}
    {currentView && loadState !== "loading" && loadState !== "error" && visible.length === 0 ? <p>目前沒有新通知。</p> : null}
    {visible.map((item) => <article className={styles.notificationItem} key={item.id}>
      <StatusBadge tone={item.tone}><span aria-hidden="true">{item.severity === "info" ? "i " : "! "}</span>
        {item.severity === "critical" ? "危急" : item.severity === "warning" ? "警告" : "資訊"}</StatusBadge>
      <strong>{item.title}</strong>
      {props.fullPage ? <Button size="sm" disabled={busy || loadState === "loading"} onClick={() => { void openDetail(item); }}>查看通知詳情 {item.id}</Button> : null}
      <p>{item.detail}</p>
      <span>{item.acknowledged ? "已讀" : "未讀"} · {item.category ? categories.find((value) => value.id === item.category)?.label : "分類未提供"}</span>
      {item.occurredAt ? <time dateTime={item.occurredAt}>{new Date(item.occurredAt).toLocaleString("zh-TW")}</time> : <span>事件時間未提供</span>}
      {item.sourceId ? <span>來源：{item.sourceId}</span> : null}
      {item.target || item.task || item.reportId ? <Button size="sm" disabled={busy || loadState === "loading"} onClick={() => { void openSource(item); }}>
        {item.reportId ? `查看回報 ${item.reportId}` : item.task ? `查看核准任務 ${item.task.id}` : `查看來源 ${item.target!.entityId ?? item.target!.workspace}`}</Button> : <span>{item.sourceUnavailable}</span>}
      {loadState === "ready" && inbox.canAcknowledge && !item.acknowledged ? <label>
        <input type="checkbox" aria-label={`選取通知 ${item.id}`} disabled={busy} checked={selected.includes(item.id)}
          onChange={(event) => setSelected((ids) => event.target.checked ? [...ids, item.id] : ids.filter((id) => id !== item.id))} />選取</label> : null}
    </article>)}
    {currentView && loadState === "ready" && inbox.canAcknowledge ? <Button size="sm" disabled={busy || selected.length === 0}
      onClick={() => { void acknowledge(); }}>將選取通知標記已讀</Button> : null}
    </div>
    {!props.fullPage ? <a href="/notifications">開啟完整通知中心</a> : null}
    {props.fullPage ? <Drawer open={detailId !== null} onClose={closeDetail} title={detail?.title ?? "通知詳情"}
      onPrev={!busy && detailIndex > 0 ? () => { void openDetail(visible[detailIndex - 1]); } : undefined}
      onNext={!busy && detailIndex >= 0 && detailIndex < visible.length - 1 ? () => { void openDetail(visible[detailIndex + 1]); } : undefined}
      deepLinkHref={detail ? `/notifications?${new URLSearchParams({ notification: detail.id, category, read: readState })}` : undefined}>
      {!currentView || loadState === "loading" ? <p role="status">正在核對通知詳情…</p>
        : error ? <p role="alert">{error}</p>
        : !detail ? <p>指定通知不存在或不在目前可讀範圍；未改開其他通知。</p>
        : <>
          <StatusBadge tone={detail.tone}>{detail.severity === "critical" ? "! 危急" : detail.severity === "warning" ? "! 警告" : "i 資訊"}</StatusBadge>
          <p>{detail.detail}</p><p>{detail.acknowledged ? "已讀" : "未讀"}</p>
          <dl><dt>來源實體</dt><dd>{detail.sourceId ?? "來源未提供"}</dd><dt>事件時間</dt>
            <dd>{detail.occurredAt ? <time dateTime={detail.occurredAt}>{detail.occurredAt}</time> : "來源未提供"}</dd></dl>
          {detail.target || detail.task || detail.reportId ? <Button size="sm" disabled={busy} onClick={() => { void openSource(detail); }}>{detail.reportId ? `查看回報 ${detail.reportId}` : detail.task ? `查看核准任務 ${detail.task.id}` : `查看來源 ${detail.target!.entityId}`}</Button>
            : <p>{detail.sourceUnavailable}</p>}
        </>}
    </Drawer> : null}
  </div>;
}
