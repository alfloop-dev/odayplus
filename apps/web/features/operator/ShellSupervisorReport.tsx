"use client";

import { useEffect, useRef, useState } from "react";
import { Button, PageHeader } from "@oday-plus/ui";
import { payloadContainsSeedData } from "./operatorDataMode";

type Report = { reportId: string; subjectId: string; storeId: string; category: string; message: string; status: string; createdAt: string };
type Props = { reportId: string; scopeKey: string; headers: Record<string, string>;
  createAuthenticatedReadGuard: (current: () => boolean) => () => Promise<boolean> };
function object(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === "object" && !Array.isArray(value); }
function normalize(value: unknown, expected: string): Report {
  if (!object(value) || value.source !== "canonical-franchisee-report-owner" || !object(value.report)) throw new Error("回報來源格式不正確。");
  const row = value.report;
  for (const field of ["reportId", "subjectId", "storeId", "category", "message", "status", "createdAt"]) {
    if (typeof row[field] !== "string" || !(row[field] as string).trim()) throw new Error("回報欄位格式不正確。");
  }
  if (row.reportId !== expected || row.subjectId === "franchisee-unknown" || row.status !== "received"
    || !["equipment", "staffing", "supply", "customer", "other"].includes(row.category as string)
    || !Number.isFinite(Date.parse(row.createdAt as string))) throw new Error("指定回報識別或狀態無法核對。");
  return row as Report;
}
export function ShellSupervisorReport(props: Props) {
  const latest = useRef(props); latest.current = props;
  const [retry, setRetry] = useState(0);
  const key = JSON.stringify([props.scopeKey, props.reportId, retry]);
  const lease = useRef({ key, active: true });
  if (lease.current.key !== key) { lease.current.active = false; lease.current = { key, active: true }; }
  const sequence = useRef(0);
  const [state, setState] = useState<{ key: string; report: Report | null; error: string; loading: boolean }>({ key, report: null, error: "", loading: true });
  useEffect(() => {
    const owner = lease.current; const read = ++sequence.current;
    const current = () => owner.active && lease.current === owner && read === sequence.current;
    const pin = latest.current.createAuthenticatedReadGuard(current);
    const headers = { ...latest.current.headers }; const expected = props.reportId;
    setState({ key, report: null, error: "", loading: true });
    async function load() {
      try {
        if (!await pin() || !current()) return;
        const response = await fetch(`/api/v1/operator/shell/franchisee/reports/${encodeURIComponent(expected)}`, { credentials: "include", cache: "no-store", headers });
        if (!await pin() || !current()) return;
        if (!response.ok) throw new Error(response.status === 404 ? "指定回報不存在或不在目前督導可讀範圍。" : `回報讀取失敗（HTTP ${response.status}）。`);
        const value: unknown = await response.json();
        if (!await pin() || !current()) return;
        if (payloadContainsSeedData(value)) throw new Error("回報來源不可使用示範資料。");
        const report = normalize(value, expected);
        if (current()) setState({ key, report, error: "", loading: false });
      } catch (failure) {
        if (await pin() && current()) setState({ key, report: null, error: failure instanceof Error ? failure.message : "回報無法讀取。", loading: false });
      }
    }
    void load(); return () => { ++sequence.current; };
  }, [key]);
  const own = state.key === key; const report = own ? state.report : null;
  return <section data-testid="supervisor-canonical-report">
    <PageHeader title="加盟主回報" summary="已保存表單的唯讀來源；門市與督導權限由後端重新核對。" actions={<a href="/notifications">返回通知中心</a>} />
    {!own || state.loading ? <p role="status">正在核對回報來源…</p> : null}
    {own && state.error ? <div role="alert">{state.error}<Button onClick={() => setRetry((value) => value + 1)}>重新讀取回報</Button></div> : null}
    {report ? <dl><dt>回報識別碼</dt><dd>{report.reportId}</dd><dt>門市</dt><dd>{report.storeId}</dd>
      <dt>實際回報帳號</dt><dd>{report.subjectId}</dd><dt>回報類型</dt><dd>{report.category}</dd>
      <dt>保存時間</dt><dd><time dateTime={report.createdAt}>{report.createdAt}</time></dd>
      <dt>狀態</dt><dd>{report.status}</dd><dt>回報內容</dt><dd>{report.message}</dd></dl> : null}
    <p>這是目前有權限督導可讀的站內通知來源；未提供 Email 或外部通道的投遞收條。</p>
  </section>;
}
