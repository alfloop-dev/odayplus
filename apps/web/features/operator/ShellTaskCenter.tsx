"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertChip, Button, Drawer, PageHeader, Table, Tabs, Toolbar, type TableColumnSpec, type TableSort } from "@oday-plus/ui";
import { payloadContainsSeedData } from "./operatorDataMode";
import styles from "./taskCenter.module.css";

type View = "assigned" | "approvals" | "initiated" | "completed";
type Task = {
  taskId: string; sourceId: string; sourceKind: string; title: string; taskType: string;
  status: string; priority: string | null; owner: string | null; createdBy: string | null;
  createdAt: string | null; updatedAt: string | null; completed: boolean; requiresApproval: boolean;
  reportId: string | null; reportVersion: number | null; previousDecisionId: string | null;
  assigneeId: string | null; assigneeName: string | null; slaDueAt: string | null;
  expiresAt?: string | null;
  slaState: "none" | "on-track" | "at-risk" | "breached";
  target: { workspace: string; tab?: string; entityId?: string; candidateId?: string; reviewId?: string } | null;
  sourceDetailAvailable: boolean;
  requestKey: string | null;
};
type State = { view: View; status: string; sla: string; sort: TableSort; selected: string | null; requestKey: string | null };
type Data = { rows: Task[]; selected: Task | null; selectionUnavailable: boolean; activeCount: number;
  counts: Record<View, number>; coverage: { complete: boolean; retentionComplete: boolean; domains: Array<{
    domain: string; state: string; creatorAvailable: boolean; retainedCompletedStatesAvailable: boolean;
  }> } };
type Lease = { scope: string; active: boolean };
export type ShellTaskCenterProps = {
  scopeKey: string;
  headers: Record<string, string>;
  createAuthenticatedReadGuard: (current: () => boolean) => () => Promise<boolean>;
  onConfirmedActiveCount: (count: number) => void;
  allowFixtureData?: boolean;
};
const views: Array<{ id: View; label: string }> = [
  { id: "assigned", label: "指派我" }, { id: "approvals", label: "待核准" },
  { id: "initiated", label: "我發起" }, { id: "completed", label: "已完成" },
];
function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function nullable(value: unknown, date = false): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "string" || !value.trim() || date && !Number.isFinite(Date.parse(value))) throw new Error("任務欄位格式不正確。");
  return value;
}
function parseTask(value: unknown): Task {
  if (!object(value)) throw new Error("任務格式不正確。");
  for (const field of ["taskId", "sourceId", "sourceKind", "title", "taskType", "status"]) {
    if (typeof value[field] !== "string" || !(value[field] as string).trim()) throw new Error("任務識別或狀態格式不正確。");
  }
  if (typeof value.completed !== "boolean" || typeof value.requiresApproval !== "boolean"
    || typeof value.sourceDetailAvailable !== "boolean" || !["none", "on-track", "at-risk", "breached"].includes(value.slaState as string)) throw new Error("任務狀態格式不正確。");
  const reportVersion = value.reportVersion ?? null;
  if (reportVersion !== null && (!Number.isInteger(reportVersion) || (reportVersion as number) < 0)) throw new Error("核決報告版本格式不正確。");
  let requestKey: string | null = null;
  if (value.notificationRequest !== undefined && value.notificationRequest !== null) {
    if (!object(value.notificationRequest) || typeof value.notificationRequest.key !== "string"
      || !/^[a-f0-9]{64}$/.test(value.notificationRequest.key)) throw new Error("核准請求識別格式不正確。");
    requestKey = value.notificationRequest.key;
  }
  let target: Task["target"] = null;
  if (value.target !== null && value.target !== undefined) {
    if (!object(value.target) || typeof value.target.workspace !== "string") throw new Error("任務來源格式不正確。");
    target = { workspace: value.target.workspace, entityId: nullable(value.target.entityId) ?? undefined, tab: nullable(value.target.tab) ?? undefined,
      candidateId: nullable(value.target.candidateId) ?? undefined, reviewId: nullable(value.target.reviewId) ?? undefined };
  }
  return { taskId: value.taskId as string, sourceId: value.sourceId as string, sourceKind: value.sourceKind as string,
    title: value.title as string, taskType: value.taskType as string, status: value.status as string,
    priority: nullable(value.priority), owner: nullable(value.owner), createdBy: nullable(value.createdBy),
    createdAt: nullable(value.createdAt, true), updatedAt: nullable(value.updatedAt, true),
    reportId: nullable(value.reportId), reportVersion: reportVersion as number | null, previousDecisionId: nullable(value.previousDecisionId),
    assigneeId: nullable(value.assigneeId), assigneeName: nullable(value.assigneeName), slaDueAt: nullable(value.slaDueAt, true),
    ...(value.expiresAt !== undefined ? { expiresAt: nullable(value.expiresAt, true) } : {}),
    slaState: value.slaState as Task["slaState"], completed: value.completed, requiresApproval: value.requiresApproval,
    sourceDetailAvailable: value.sourceDetailAvailable, target,
    requestKey };
}
export function normalizeTaskCenter(value: unknown): Data {
  if (!object(value) || !Array.isArray(value.items) || !object(value.meta) || !object(value.meta.coverage)
    || !object(value.viewCounts) || !Number.isInteger(value.activeCount) || (value.activeCount as number) < 0
    || typeof value.selectionUnavailable !== "boolean") throw new Error("任務中心回應格式不正確。");
  const rows = value.items.map(parseTask);
  if (value.count !== rows.length || !Number.isInteger(value.total) || (value.total as number) < rows.length
    || (value.activeCount as number) > (value.total as number)) throw new Error("任務數量無法核對。");
  if (new Set(rows.map((row) => row.taskId)).size !== rows.length) throw new Error("任務識別碼重複。");
  const counts = {} as Data["counts"];
  for (const { id } of views) {
    const count = value.viewCounts[id];
    if (!Number.isInteger(count) || (count as number) < 0 || (count as number) > (value.total as number)) throw new Error("任務數量格式不正確。");
    counts[id] = count as number;
  }
  const coverage = value.meta.coverage;
  if (typeof coverage.complete !== "boolean" || typeof coverage.retentionComplete !== "boolean" || !Array.isArray(coverage.domains)
    || coverage.domains.some((domain) => !object(domain) || typeof domain.domain !== "string" || !["available", "degraded", "unavailable"].includes(domain.state as string)
      || typeof domain.creatorAvailable !== "boolean" || typeof domain.retainedCompletedStatesAvailable !== "boolean")) throw new Error("任務來源覆蓋格式不正確。");
  return { rows, selected: value.selected === null ? null : parseTask(value.selected), selectionUnavailable: value.selectionUnavailable,
    activeCount: value.activeCount as number, counts, coverage: coverage as Data["coverage"] };
}
function urlState(): State {
  const url = new URL(window.location.href);
  let selected = url.searchParams.get("taskId");
  const match = /^\/tasks\/([^/]+)\/?$/.exec(url.pathname);
  if (match) { try { selected = decodeURIComponent(match[1]); } catch { selected = "__invalid_task_path__"; } }
  const view = views.find((item) => item.id === url.searchParams.get("view"))?.id ?? "assigned";
  const columnId = ["title", "status", "updatedAt"].includes(url.searchParams.get("sort") ?? "") ? url.searchParams.get("sort")! : "updatedAt";
  return { view, requestKey: url.searchParams.get("requestKey"), status: url.searchParams.get("status") ?? "", sla: url.searchParams.get("sla") ?? "", selected,
    sort: { columnId, direction: url.searchParams.get("direction") === "asc" ? "asc" : "desc" } };
}
export function taskSourceHref(row: Task): string | null {
  const target = row.target;
  if (!row.sourceDetailAvailable || !target?.entityId) return null;
  const valid = target.workspace === "today" && target.tab === "interventions"
    || target.workspace === "store" && ["overview", "forecastOps"].includes(target.tab ?? "")
    || target.workspace === "govern" && target.tab === "approvals"
    || target.workspace === "network" && target.candidateId === target.entityId
      && (target.tab === "candidates" || target.tab === "review" && Boolean(target.reviewId));
  if (!valid) return null;
  const query = new URLSearchParams({ ws: target.workspace, tab: target.tab!, entity: target.entityId });
  if (target.workspace === "network") {
    query.set("candidate", target.candidateId!);
    if (target.reviewId) query.set("review", target.reviewId);
  }
  return `/operator?${query}`;
}
function taskDetailHref(taskId: string, state: State) {
  const params = new URLSearchParams({ view: state.view, sort: state.sort.columnId, direction: state.sort.direction });
  if (state.sla) params.set("sla", state.sla);
  if (state.status) params.set("status", state.status);
  if (state.requestKey && state.selected === taskId) params.set("requestKey", state.requestKey);
  return `/tasks/${encodeURIComponent(taskId)}?${params}`;
}
function slaLabel(row: Task) {
  const label = { none: "未提供 SLA", "on-track": "期限內", "at-risk": "⚠ 即將逾期", breached: "⚠ 已逾 SLA" }[row.slaState];
  return <span data-sla-state={row.slaState}>{row.expiresAt ? <span>任務期限 <time dateTime={row.expiresAt}>{row.expiresAt}</time> · </span> : null}<AlertChip label={label} tone={row.slaState === "breached" ? "red" : row.slaState === "at-risk" ? "yellow" : row.slaState === "none" ? "gray" : "blue"} />{row.slaDueAt ? <time dateTime={row.slaDueAt}> {row.slaDueAt}</time> : null}</span>;
}

export function ShellTaskCenter(props: ShellTaskCenterProps) {
  const latest = useRef(props); latest.current = props;
  const sequence = useRef(0);
  const [view, setView] = useState<State>(() => typeof window === "undefined"
    ? { view: "assigned", status: "", sla: "", sort: { columnId: "updatedAt", direction: "desc" }, selected: null, requestKey: null } : urlState());
  const viewRef = useRef(view); viewRef.current = view;
  const [retry, setRetry] = useState(0);
  const queryKey = JSON.stringify([props.scopeKey, view.view, view.status, view.sla, view.selected, view.requestKey, retry]);
  const leaseRef = useRef<Lease>({ scope: queryKey, active: true });
  if (leaseRef.current.scope !== queryKey) {
    leaseRef.current.active = false; leaseRef.current = { scope: queryKey, active: true };
  }
  const [scope, setScope] = useState(queryKey);
  const [data, setData] = useState<Data | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => { const change = () => setView(urlState()); window.addEventListener("popstate", change); return () => window.removeEventListener("popstate", change); }, []);
  const update = useCallback((patch: Partial<State>) => {
    const next = { ...viewRef.current, ...patch,
      ...("selected" in patch && !("requestKey" in patch) ? { requestKey: null } : {}) };
    const url = new URL(window.location.href);
    url.pathname = next.selected ? `/tasks/${encodeURIComponent(next.selected)}` : "/tasks";
    url.searchParams.delete("taskId"); url.searchParams.set("view", next.view);
    if (next.requestKey) url.searchParams.set("requestKey", next.requestKey); else url.searchParams.delete("requestKey");
    for (const [key, value] of [["status", next.status], ["sla", next.sla], ["sort", next.sort.columnId], ["direction", next.sort.direction]]) {
      if (value) url.searchParams.set(key, value); else url.searchParams.delete(key);
    }
    const href = `${url.pathname}${url.search}${url.hash}`;
    if (href !== `${window.location.pathname}${window.location.search}${window.location.hash}`) window.history.pushState(null, "", href);
    viewRef.current = next; setView(next);
  }, []);
  const closeDrawer = useCallback(() => update({ selected: null }), [update]);
  useEffect(() => {
    const lease = leaseRef.current; const read = ++sequence.current;
    const current = () => lease.active && leaseRef.current === lease && sequence.current === read;
    const pin = latest.current.createAuthenticatedReadGuard(current);
    setScope(queryKey); setData(null); setError(""); setLoading(true);
    async function load() {
      try {
        if (!await pin() || !current()) return;
        const query = new URLSearchParams({ view: view.view });
        if (view.sla) query.set("sla", view.sla);
        if (view.status) query.set("status", view.status);
        if (view.selected) query.set("taskId", view.selected);
        if (view.requestKey) query.set("requestKey", view.requestKey);
        const response = await fetch(`/api/v1/operator/shell/tasks?${query}`, { headers: latest.current.headers, credentials: "include" });
        if (!await pin() || !current()) return;
        if (!response.ok) throw new Error(`任務讀取失敗（HTTP ${response.status}）。`);
        const value: unknown = await response.json();
        if (!await pin() || !current()) return;
        if (!latest.current.allowFixtureData && payloadContainsSeedData(value)) throw new Error("任務 API 回傳示範來源，未顯示為正式任務。");
        const next = normalizeTaskCenter(value);
        if (view.requestKey && next.selected && next.selected.requestKey !== view.requestKey) throw new Error("指定核准請求已更換；未改開其他送審輪次。");
        if (view.selected ? next.selected
          ? next.selected.taskId !== view.selected && next.selected.sourceId !== view.selected
          : !next.selectionUnavailable
          : next.selected !== null || next.selectionUnavailable) throw new Error("指定任務的回應識別碼無法核對。");
        setData(next); latest.current.onConfirmedActiveCount(next.activeCount);
      } catch (failure) {
        if (await pin() && current()) setError(failure instanceof Error ? failure.message : "任務讀取失敗。");
      } finally { if (current()) setLoading(false); }
    }
    void load();
    return () => { if (sequence.current === read) sequence.current += 1; };
  }, [props.scopeKey, view.view, view.sla, view.status, view.selected, view.requestKey, retry]);
  useEffect(() => () => { sequence.current += 1; }, []);
  const visible = scope === queryKey ? data : null;
  const rows = useMemo(() => [...(visible?.rows ?? [])].sort((a, b) => {
    const key = view.sort.columnId as "title" | "status" | "updatedAt";
    const left = a[key]; const right = b[key];
    if (left === null) return right === null ? a.taskId.localeCompare(b.taskId) : 1;
    if (right === null) return -1;
    const order = left.localeCompare(right) || a.taskId.localeCompare(b.taskId);
    return view.sort.direction === "asc" ? order : -order;
  }), [visible, view.sort]);
  const selected = visible?.selected ?? null;
  const position = selected ? rows.findIndex((item) => item.taskId === selected.taskId) : -1;
  async function openSource(row: Task) {
    const lease = leaseRef.current; const read = sequence.current;
    const current = () => leaseRef.current === lease && lease.active && sequence.current === read;
    const pin = latest.current.createAuthenticatedReadGuard(current); const href = taskSourceHref(row);
    if (href && await pin() && current()) window.location.assign(href);
  }
  const columns: TableColumnSpec<Task>[] = [
    { id: "title", header: "實體名稱", sortable: true, render: (row) => row.title },
    { id: "taskType", header: "任務類型", accessor: "taskType" }, { id: "status", header: "狀態", sortable: true, accessor: "status" },
    { id: "priority", header: "優先序", render: (row) => row.priority ?? "未提供" },
    { id: "owner", header: "負責人", render: (row) => row.assigneeName ?? row.assigneeId ?? row.owner ?? "未提供" },
    { id: "sla", header: "期限／SLA", render: slaLabel },
    { id: "updatedAt", header: "最後更新", sortable: true, render: (row) => row.updatedAt ?? "未提供" },
    { id: "action", header: "主要動作", render: (row) => <Button onClick={(event) => { event.stopPropagation(); update({ selected: row.taskId }); }}>查看任務</Button> },
  ];
  const panel = <>
    <Toolbar filters={[
      { id: "status", label: "任務狀態", value: view.status, onChange: (status) => update({ status, selected: null }), placeholder: "來源狀態" },
      { id: "sla", label: "SLA", value: view.sla, onChange: (sla) => update({ sla, selected: null }), options: [
        { value: "breached", label: "已逾期" }, { value: "at-risk", label: "即將逾期" },
        { value: "on-track", label: "期限內" }, { value: "none", label: "未提供 SLA" },
      ] },
    ]} />
    {error && scope === queryKey ? <div role="alert">{error} <Button onClick={() => setRetry((value) => value + 1)}>重試</Button></div> : null}
    <div className={styles.table}><Table columns={columns} data={rows} density="compact" loading={loading || scope !== queryKey}
      sort={view.sort} onSortChange={(sort) => update({ sort })} getRowKey={(row) => row.taskId}
      onRowOpen={(row) => update({ selected: row.taskId })} summary="任務中心" /></div>
    <div className={styles.cards} aria-label="任務中心卡片列表">
      {loading ? <p role="status">載入中</p> : rows.length === 0 ? <p>目前沒有待辦。<a href="/operator">瀏覽工作區 →</a></p> : null}
      {rows.map((row) => <article key={row.taskId}>
      <h2>{row.title}</h2><dl>{columns.slice(1, 7).map((column) => <div key={column.id}><dt>{column.header}</dt><dd>{column.render?.(row) ?? String(row[column.accessor!] ?? "未提供")}</dd></div>)}</dl>
      <Button onClick={() => update({ selected: row.taskId })}>查看任務</Button>
    </article>)}</div>
  </>;
  return <section className={styles.root} data-testid="canonical-task-center">
    <PageHeader title="任務中心" summary="只顯示目前帳號與資料範圍可讀的任務；核准沿原完整流程。" actions={<a href="/operator">瀏覽工作區 →</a>} />
    {props.allowFixtureData ? <p>POC：此畫面不作正式歷史或權限證明。</p> : null}
    {visible && (!visible.coverage.complete || !visible.coverage.retentionComplete) ? <p role="status">
      資料來源尚不完整；部分來源未提供發起人或已完成歷史，空清單不代表全部完成。
      {visible.coverage.domains.filter((item) => item.state !== "available" || !item.creatorAvailable || !item.retainedCompletedStatesAvailable)
        .map((item) => <span key={item.domain}> {item.domain}：{item.state === "unavailable" ? "來源不可用" : "僅已保存紀錄／部分欄位"}。</span>)}
    </p> : null}
    <div onKeyDown={(event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key) || !(event.target instanceof HTMLElement) || event.target.getAttribute("role") !== "tab") return;
      const index = views.findIndex((item) => item.id === view.view);
      const next = event.key === "Home" ? 0 : event.key === "End" ? views.length - 1 : (index + (event.key === "ArrowRight" ? 1 : views.length - 1)) % views.length;
      event.preventDefault(); update({ view: views[next].id, selected: null });
      document.getElementById(`odp-tab-${views[next].id}`)?.focus();
    }}><Tabs tabs={views.map((item) => ({ ...item, badge: visible?.counts[item.id], panel }))} active={view.view}
      onChange={(id) => update({ view: id as View, selected: null })} /></div>
    <Drawer open={view.selected !== null} onClose={closeDrawer} title={selected?.title ?? "任務詳情"}
      onPrev={position > 0 ? () => update({ selected: rows[position - 1].taskId }) : undefined}
      onNext={position >= 0 && position < rows.length - 1 ? () => update({ selected: rows[position + 1].taskId }) : undefined}
      deepLinkHref={selected ? taskDetailHref(selected.taskId, view) : undefined}>
      {loading ? <p role="status">載入任務詳情中…</p> : error ? <div role="alert">{error}<Button onClick={() => setRetry((value) => value + 1)}>重試任務詳情</Button></div>
        : !selected || visible?.selectionUnavailable ? <p>指定任務不存在、無權限或已不在目前來源；未改開其他任務。</p>
        : <><dl><dt>來源識別碼</dt><dd>{selected.sourceId}</dd><dt>狀態</dt><dd>{selected.status}</dd>
          <dt>實際發起人</dt><dd>{selected.createdBy ?? "來源未提供"}</dd><dt>建立時間</dt><dd>{selected.createdAt ?? "來源未提供"}</dd>
          <dt>最後更新</dt><dd>{selected.updatedAt ?? "來源未提供"}</dd><dt>期限／SLA</dt><dd>{slaLabel(selected)}</dd>
          {selected.sourceKind === "sitescore.decision" ? <><dt>本輪報告</dt><dd>{selected.reportId ?? "來源未提供"}／{selected.reportVersion ?? "版本未提供"}</dd>
            <dt>前一核決</dt><dd>{selected.previousDecisionId ?? "未提供"}</dd></> : null}</dl>
          {taskSourceHref(selected) ? <><p>{selected.requiresApproval ? "高風險核准須在來源的完整審查流程完成；此處不送出核准。" : "來源將再次核對目前帳號與物件權限。"}</p>
            <Button data-source-href={taskSourceHref(selected) ?? undefined} onClick={() => { void openSource(selected); }}>{selected.requiresApproval ? "開啟完整審查" : "查看來源詳情"}</Button></>
            : <p>來源詳情入口尚未提供；保留真實任務資料，未開啟其他事件。</p>}
        </>}
    </Drawer>
  </section>;
}
