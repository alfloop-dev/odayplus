"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Button, Modal } from "@oday-plus/ui";
import styles from "../networkFindAreas.module.css";
import { SurveyEvidenceWork } from "./SurveyEvidenceWork";
import type { SurveyAssignmentApi, SurveyCreated, SurveyRead, SurveyTarget } from "./surveyAssignmentClient";

const STATUS: Record<string, string> = { UNASSIGNED: "待指派", ASSIGNED: "已指派", CLAIMED: "已領取",
  IN_PROGRESS: "現勘中", SUBMITTED: "已提交回報", EXPIRED: "已逾期", CANCELLED: "已取消" };

export function SurveyAssignmentDialog({ api, target, onClose }: {
  api: SurveyAssignmentApi; target: SurveyTarget; onClose: () => void;
}) {
  const context = useMemo(() => ({ api, kind: target.kind, id: target.id }), [api, target.kind, target.id]);
  const latest = useRef(context); latest.current = context;
  const mounted = useRef(false);
  const readSequence = useRef(0);
  const [observedContext, setObservedContext] = useState(context);
  const [loaded, setLoaded] = useState<{ context: typeof context; value: SurveyRead } | null>(null);
  const [created, setCreated] = useState<{ context: typeof context; value: SurveyCreated } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const [pending, setPending] = useState(false);
  const [workPending, setWorkPending] = useState(false);
  const workBusy = useRef(false);
  const [selectedWork, setSelectedWork] = useState<string | null>(null);
  const [assignedTo, setAssignedTo] = useState("");
  const [deadline, setDeadline] = useState("");
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState(false);
  const retry = useRef<{ fingerprint: string; key: string } | null>(null);
  const current = (captured: typeof context) => mounted.current && latest.current === captured;
  const value = loaded?.context === context ? loaded.value : null;
  const success = created?.context === context ? created.value : null;
  const visible = observedContext === context;

  async function reload(captured = context) {
    if (!current(captured) || workBusy.current) return;
    const sequence = ++readSequence.current;
    setReading(true); setReadError(null);
    try {
      const response = await captured.api.read({ kind: captured.kind, id: captured.id });
      if (!current(captured) || sequence !== readSequence.current) return;
      if (response.ok) setLoaded({ context: captured, value: response.value });
      else if (!("superseded" in response)) {
        setReadError(`狀態重讀失敗：${response.message}`);
        if (response.denied) { setLoaded(null); setCreated(null); setPreview(false); }
      }
    } catch {
      if (current(captured) && sequence === readSequence.current) setReadError("狀態重讀失敗；已保存的任務識別仍保留，請重新讀取。");
    } finally { if (current(captured) && sequence === readSequence.current) setReading(false); }
  }

  useEffect(() => {
    mounted.current = true;
    setObservedContext(context);
    setLoaded(null); setCreated(null); setError(null); setReadError(null);
    setAssignedTo(""); setDeadline(""); setReason(""); setPreview(false); setPending(false);
    workBusy.current = false; setWorkPending(false); setSelectedWork(null);
    retry.current = null;
    void reload(context);
    return () => { mounted.current = false; ++readSequence.current; };
  }, [context]);

  function input() {
    const time = new Date(deadline);
    if (!assignedTo || !value?.assignees.some(row => row.subjectId === assignedTo)
      || !deadline || !Number.isFinite(time.getTime()) || !reason.trim()) return null;
    return { assignedTo, expiresAt: time.toISOString(), reason: reason.trim() };
  }

  async function submit() {
    const captured = context, selected = input();
    if (!current(captured) || pending || workBusy.current || !preview || !value?.canAssign || success || !selected) return;
    const fingerprint = JSON.stringify({ target: { kind: captured.kind, id: captured.id }, ...selected });
    if (retry.current?.fingerprint !== fingerprint) retry.current = { fingerprint, key: crypto.randomUUID() };
    setPending(true); setError(null);
    try {
      const response = await captured.api.create({ kind: captured.kind, id: captured.id }, selected, retry.current.key);
      if (!current(captured)) return;
      if (response.ok) {
        setCreated({ context: captured, value: response.value }); setPreview(false); retry.current = null;
        setLoaded(previous => previous?.context === captured ? { context: captured, value: {
          ...previous.value, items: [...previous.value.items.filter(row => row.assignment_id !== response.value.assignment.assignment_id),
            response.value.assignment],
        } } : previous);
        // The accepted command owns this ID even if the subsequent read fails.
        // Re-reading is never another create intent.
        await reload(captured);
      } else if (!("superseded" in response)) {
        setError(response.message);
        if (response.denied) { setLoaded(null); setPreview(false); }
      }
    } catch {
      if (current(captured)) setError("指派回應尚未確認；請保留相同輸入與命令識別重試，或先重新讀取。");
    } finally { if (current(captured)) setPending(false); }
  }

  return <Modal open onClose={onClose} closeDisabled={visible && (pending || workPending)} title={target.kind === "HEAT_ZONE" ? "指派熱區任務" : "指派現勘"}>
    <p>目前實體：<code>{target.id}</code></p>
    {visible && reading ? <p role="status">讀取現勘任務中…</p> : null}
    {visible && readError ? <p role="alert">{readError}</p> : null}
    {visible && error ? <p role="alert">{error}</p> : null}
    <Button disabled={reading || pending || workPending} onClick={() => { void reload(); }}>重新讀取任務狀態</Button>
    {success ? <section role="status" aria-label="已建立的現勘任務">
      <p>任務已建立：<code>{success.assignment.assignment_id}</code>；建立回應狀態：{STATUS[success.assignment.status]}。</p>
      <p>稽核：{success.auditEventIds.join("、")}</p>
      <Button disabled={pending || workPending || reading || !value?.canAssign} onClick={() => {
        setCreated(null); setReason(""); setDeadline(""); setAssignedTo(""); setError(null); setPreview(false);
      }}>另建一筆任務</Button>
    </section> : null}
    {value ? <>
      {value.assigneeError ? <p role="status">{value.assigneeError}</p> : null}
      <h3>已保存任務</h3>
      {value.items.length ? <ul>{value.items.map(row => <li key={row.assignment_id}>
        <code>{row.assignment_id}</code> · {STATUS[row.status]} · 指派給 {row.assigned_to ?? "尚未指派"}
        <p>期限：{row.expires_at} · 建立者：{row.created_by} · 版本 {row.revision}</p>
        {api.work ? <Button disabled={pending || workPending || reading} onClick={() => setSelectedWork(row.assignment_id)}>現勘回報／附件 {row.assignment_id}</Button> : null}
      </li>)}</ul> : <p>目前沒有已保存的現勘任務。</p>}
      {!success ? <form className="odp-form" onSubmit={event => { event.preventDefault(); if (current(context) && !pending && !workBusy.current && !reading && value.canAssign && !success && input()) setPreview(true); }}>
        <section className="odp-form__affected"><h3>即將影響的實體</h3><code>{target.id}</code></section>
        <fieldset className="odp-form" disabled={pending || workPending || reading || !value.canAssign || preview}>
          <label className="odp-field"><span className={styles.reviewDialogFieldLabel}>指派帳號</span>
            <select className={styles.inputField} value={assignedTo} onChange={event => setAssignedTo(event.target.value)} required>
              <option value="">請選擇可存取目前實體的帳號</option>
              {value.assignees.map(row => <option value={row.subjectId} key={row.subjectId}>{row.displayName} · {row.subjectId}</option>)}
            </select></label>
          <label className="odp-field"><span className={styles.reviewDialogFieldLabel}>現勘期限（本機時間）</span>
            <input className={styles.inputField} type="datetime-local" value={deadline} onChange={event => setDeadline(event.target.value)} required /></label>
          <label className="odp-field"><span className={styles.reviewDialogFieldLabel}>指派理由</span>
            <textarea className={styles.textareaField} value={reason} onChange={event => setReason(event.target.value)} required /></label>
          <Button type="submit" disabled={!input()}>預覽指派</Button>
        </fieldset>
        {preview ? <section aria-label="指派預覽"><dl>
          <div><dt>實體</dt><dd>{target.id}</dd></div><div><dt>指派帳號</dt><dd>{assignedTo}</dd></div>
          <div><dt>期限</dt><dd>{input()?.expiresAt}</dd></div><div><dt>理由</dt><dd>{reason.trim()}</dd></div>
        </dl><div className="odp-actions">
          <Button disabled={pending || workPending || reading} onClick={() => setPreview(false)}>返回修改</Button>
          <Button variant="primary" disabled={pending || workPending || reading || !input() || !value.canAssign} onClick={() => { void submit(); }}>確認指派</Button>
        </div></section> : null}
      </form> : null}
    </> : null}
    {api.work && value?.items.find(row => row.assignment_id === selectedWork) ? <SurveyEvidenceWork
      api={api.work} assignment={value.items.find(row => row.assignment_id === selectedWork)!}
      onPendingChange={busy => { if (current(context)) { workBusy.current = busy; setWorkPending(busy); } }} /> : null}
    {!api.work ? <p>現勘照片／附件內容尚未接通；任務指派或目的紀錄不代表已上傳或可讀取影像。</p>
      : <p>請開啟實際任務的回報／附件。任務指派本身不代表照片已上傳、現勘已完成或審查已通過。</p>}
  </Modal>;
}
