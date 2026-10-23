"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@oday-plus/ui";
import styles from "../networkFindAreas.module.css";
import type { SurveyAssignment } from "./surveyAssignmentClient";
import type { SurveyEvidence, SurveyEvidenceApi, SurveyReview, SurveyReviewed } from "./surveyEvidenceClient";

const labels = { PENDING_REVIEW: "待審查", APPROVED: "核准", REJECTED: "駁回", NEEDS_REVISION: "需補正" };

export function SurveyEvidenceReview({ api, assignment, onPendingChange }: {
  api: SurveyEvidenceApi; assignment: SurveyAssignment; onPendingChange: (pending: boolean) => void;
}) {
  const { assignment_id, tenant_id, target_entity_kind, target_entity_id, survey_id } = assignment;
  // Parent reads return fresh row objects. Only a different source or evidence
  // identity supersedes a command; a same-task reread must keep its fence.
  const context = useMemo(() => ({ api, assignment: { assignment_id, tenant_id, target_entity_kind, target_entity_id, survey_id } }),
    [api, assignment_id, tenant_id, target_entity_kind, target_entity_id, survey_id]);
  const latest = useRef(context); latest.current = context;
  const notify = useRef(onPendingChange); notify.current = onPendingChange;
  const pendingChanged = useCallback((value: boolean) => notify.current(value), []);
  const mounted = useRef(false), sequence = useRef(0), busy = useRef(false);
  const retry = useRef<{ fingerprint: string; key: string } | null>(null);
  const [scope, setScope] = useState(context), [data, setData] = useState<SurveyEvidence | null>(null);
  const [reading, setReading] = useState(false), [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null), [saved, setSaved] = useState<SurveyReviewed | null>(null);
  const [decision, setDecision] = useState<SurveyReview["decision"] | "">(""), [reason, setReason] = useState("");
  const [conditions, setConditions] = useState(""), [preview, setPreview] = useState<SurveyReview | null>(null);
  const current = useCallback((captured: typeof context) => mounted.current && latest.current === captured, []);
  const value = scope === context ? data : null;

  const reload = useCallback(async (captured = context) => {
    if (!current(captured)) return;
    const read = ++sequence.current; setReading(true); setError(null);
    try {
      const result = await captured.api.evidence(captured.assignment);
      if (!current(captured) || sequence.current !== read) return;
      if (result.ok) setData(result.value);
      else {
        // A failed fresh read cannot leave stale authority enabled.
        setData(null);
        if ("superseded" in result || result.denied) { setPreview(null); setSaved(null); }
        if (!("superseded" in result)) setError(result.message);
      }
    } catch { if (current(captured) && sequence.current === read) { setData(null); setError("審查狀態讀取失敗，請重新讀取。"); } }
    finally { if (current(captured) && sequence.current === read) setReading(false); }
  }, [context, current]);
  useEffect(() => {
    mounted.current = true; busy.current = false; retry.current = null;
    setScope(context); setData(null); setError(null); setSaved(null); setPreview(null);
    setDecision(""); setReason(""); setConditions(""); setPending(false); void reload(context);
    return () => {
      mounted.current = false;
      if (busy.current) { busy.current = false; pendingChanged(false); }
    };
  }, [context, reload, pendingChanged]);

  async function confirm() {
    if (!preview || !value?.canRequestReview || reading || busy.current || !current(context)) return;
    const captured = context, input = preview;
    const fingerprint = JSON.stringify(input);
    if (retry.current?.fingerprint !== fingerprint) retry.current = { fingerprint, key: crypto.randomUUID() };
    busy.current = true; setPending(true); setError(null); pendingChanged(true);
    try {
      const result = await captured.api.review(captured.assignment, input, retry.current.key);
      if (!current(captured)) return;
      if (result.ok) {
        setSaved(result.value); setData(result.value.evidence); setPreview(null); retry.current = null;
        // The receipt is retained even when the following fresh read fails.
        await reload(captured);
      } else {
        if ("superseded" in result || result.denied) { setData(null); setPreview(null); setSaved(null); }
        if (!("superseded" in result)) setError(result.message);
      }
    } catch { if (current(captured)) setError("審查回應未確認；請保留相同輸入重試，尚未顯示成功。"); }
    finally { if (current(captured)) { busy.current = false; setPending(false); pendingChanged(false); } }
  }

  return <section aria-label="現勘獨立審查">
    <h3>現勘獨立審查</h3>
    {scope === context && error ? <p role="alert">{error}</p> : null}
    <Button disabled={pending || reading} onClick={() => { void reload(); }}>重讀實際審查紀錄</Button>
    {reading ? <p role="status">讀取審查紀錄中…</p> : null}
    {scope === context && saved ? <p role="status">審查已保存：{labels[saved.evidence.review_status]} · 收條 {saved.receiptId} · 稽核 {saved.auditEventIds.join("、")}</p> : null}
    {value ? <>
      <dl><div><dt>回報／版本</dt><dd>{value.survey_id} · v{value.revision}</dd></div>
        <div><dt>提交者／現勘時間</dt><dd>{value.submitter_id} · {value.surveyed_at}</dd></div>
        <div><dt>回報位置</dt><dd>{value.location.latitude}, {value.location.longitude}</dd></div>
        <div><dt>審查狀態</dt><dd>{labels[value.review_status]}</dd></div></dl>
      <h4>已提交的現場觀察</h4><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(value.attributes, null, 2)}</pre>
      {value.review_record ? <dl><div><dt>審查者／時間</dt><dd>{value.review_record.reviewer_id} · {value.review_record.reviewed_at}</dd></div>
        <div><dt>審查理由</dt><dd>{value.review_record.review_comment ?? "未提供"}</dd></div>
        <div><dt>審查條件</dt><dd>{value.review_record.conditions.join("；") || "未提供"}</dd></div></dl> : null}
      <p>審查結果僅適用於這份現勘回報；後續評分及展店決策仍須各自完成審核。</p>
      {value.canRequestReview ? <>
        <fieldset className="odp-form" disabled={pending || reading || !!preview}>
          <legend>審查這份回報</legend>
          <label className="odp-field">審查結果<select className={styles.inputField} value={decision} onChange={e => setDecision(e.target.value as typeof decision)}>
            <option value="">請選擇</option><option value="APPROVED">核准</option><option value="REJECTED">駁回</option><option value="NEEDS_REVISION">需補正</option>
          </select></label>
          <label className="odp-field">審查理由<textarea className={styles.textareaField} value={reason} onChange={e => setReason(e.target.value)} /></label>
          <label className="odp-field">審查條件（選填，每行一項）<textarea className={styles.textareaField} value={conditions} onChange={e => setConditions(e.target.value)} /></label>
          <Button disabled={!decision || !reason.trim()} onClick={() => { if (decision && reason.trim()) setPreview({ surveyId: value.survey_id, expectedRevision: value.revision,
            decision, reason: reason.trim(), reviewChecklist: {}, conditions: conditions.split("\n").map(item => item.trim()).filter(Boolean) }); }}>預覽審查結果</Button>
        </fieldset>
        {preview ? <section aria-label="現勘審查預覽">
          <h4>確認審查這份回報</h4><p>{assignment.target_entity_id} · {value.survey_id} · v{preview.expectedRevision}</p>
          <p>結果：{labels[preview.decision]}</p><p>理由：{preview.reason}</p><p>條件：{preview.conditions.join("；") || "未提供"}</p>
          <div className="odp-actions"><Button disabled={pending || reading} onClick={() => setPreview(null)}>返回修改審查</Button>
            <Button disabled={pending || reading} variant="primary" onClick={() => { void confirm(); }}>確認保存審查</Button></div>
        </section> : null}
      </> : <p>目前帳號無法審查此回報；提交者須由另一位具權限的人員審查。</p>}
    </> : null}
  </section>;
}
