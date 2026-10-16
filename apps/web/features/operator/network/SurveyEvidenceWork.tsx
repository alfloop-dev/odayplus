"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@oday-plus/ui";
import styles from "../networkFindAreas.module.css";
import type { SurveyAssignment } from "./surveyAssignmentClient";
import type { SurveyAttachment, SurveyEvidenceApi, SurveySubmission, SurveySubmitted, SurveyUpload, SurveyWorkRead } from "./surveyEvidenceClient";

type Preview = { kind: "upload"; input: SurveyUpload; fileName: string } | { kind: "submit"; input: SurveySubmission };
const ACTIVE = ["ASSIGNED", "CLAIMED", "IN_PROGRESS"];

export function SurveyEvidenceWork({ api, assignment, onPendingChange }: {
  api: SurveyEvidenceApi; assignment: SurveyAssignment; onPendingChange: (pending: boolean) => void;
}) {
  const context = useMemo(() => ({ api, identity: assignment.assignment_id }), [api, assignment.assignment_id]);
  const latest = useRef(context); latest.current = context;
  const mounted = useRef(false), sequence = useRef(0), inFlight = useRef(false);
  const [scope, setScope] = useState(context);
  const [data, setData] = useState<SurveyWorkRead | null>(null);
  const [error, setError] = useState<string | null>(null), [readError, setReadError] = useState<string | null>(null);
  const [pending, setPending] = useState(false), [reading, setReading] = useState(false);
  const [reason, setReason] = useState("");
  const [file, setFile] = useState<File | null>(null), [capturedAt, setCapturedAt] = useState(""), [kind, setKind] = useState("");
  const [caption, setCaption] = useState("");
  const [latitude, setLatitude] = useState(""), [longitude, setLongitude] = useState("");
  const [surveyedAt, setSurveyedAt] = useState(""), [observation, setObservation] = useState(""), [confidence, setConfidence] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [uploaded, setUploaded] = useState<SurveyAttachment | null>(null), [submitted, setSubmitted] = useState<SurveySubmitted | null>(null);
  const retry = useRef<{ fingerprint: string; key: string } | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const current = (captured: typeof context) => mounted.current && captured === latest.current;
  const value = scope === context ? data : null;
  const row = value?.assignment ?? assignment;
  const canManage = value?.canRequestUpdate === true;
  const sameSurveyor = value && (!row.assigned_to || row.assigned_to === value.currentSubjectId);
  const canSubmit = Boolean(canManage && sameSurveyor && ACTIVE.includes(row.status) && !submitted);

  async function reload(captured = context, selected = row) {
    if (!current(captured)) return;
    const read = ++sequence.current;
    setReading(true); setReadError(null);
    try {
      const result = await captured.api.read(selected);
      if (!current(captured) || sequence.current !== read) return;
      if (result.ok) setData(result.value);
      else if (!("superseded" in result)) {
        setReadError(`狀態重讀失敗：${result.message}`);
        if (result.denied) { setData(null); setPreview(null); setUploaded(null); setSubmitted(null); }
      }
    } catch {
      if (current(captured) && sequence.current === read) setReadError("狀態重讀失敗；已確認的命令識別仍保留，請重新讀取。");
    } finally { if (current(captured) && sequence.current === read) setReading(false); }
  }
  useEffect(() => {
    mounted.current = true; inFlight.current = false; setScope(context); setData(null); setError(null); setReadError(null);
    setPending(false); setReading(false); setReason(""); setFile(null); setCapturedAt(""); setKind(""); setCaption("");
    setLatitude(""); setLongitude(""); setSurveyedAt(""); setObservation(""); setConfidence("");
    setPreview(null); setUploaded(null); setSubmitted(null); retry.current = null;
    onPendingChange(false); void reload(context, assignment);
    return () => { mounted.current = false; ++sequence.current; };
  }, [context]);

  function begin() {
    if (!current(context) || inFlight.current || reading) return false;
    inFlight.current = true; setPending(true); onPendingChange(true); setError(null); return true;
  }
  function end(captured: typeof context) {
    if (current(captured)) { inFlight.current = false; setPending(false); onPendingChange(false); }
  }
  function intent(kind: string, input: unknown) {
    const fingerprint = JSON.stringify({ kind, identity: context.identity, input });
    if (retry.current?.fingerprint !== fingerprint) retry.current = { fingerprint, key: crypto.randomUUID() };
    return retry.current.key;
  }
  function failure(result: { message: string; denied?: boolean }) {
    setError(result.message);
    if (result.denied) { setData(null); setPreview(null); setUploaded(null); setSubmitted(null); }
  }
  async function change(operation: "claim" | "start" | "cancel") {
    if (!canManage || preview || !reason.trim()
      || (operation !== "cancel" && !sameSurveyor)
      || !(operation === "claim" ? ["UNASSIGNED", "ASSIGNED"] : operation === "start" ? ["ASSIGNED", "CLAIMED"] : ACTIVE).includes(row.status)
      || !begin()) return;
    const captured = context, selected = row;
    try {
      const result = await captured.api.change(selected, operation, reason.trim(), intent(operation, { revision: selected.revision, reason: reason.trim() }));
      if (!current(captured)) return;
      if (result.ok) { setData(old => old ? { ...old, assignment: result.value.assignment } : old); retry.current = null; await reload(captured, result.value.assignment); }
      else if (!("superseded" in result)) failure(result);
    } catch { if (current(captured)) setError("狀態命令回應未確認；保留相同輸入與命令識別重試。"); }
    finally { end(captured); }
  }
  function submission(): SurveySubmission | null {
    if (!canSubmit || !reason.trim() || !latitude.trim() || !longitude.trim() || !surveyedAt) return null;
    const lat = Number(latitude), lon = Number(longitude), time = new Date(surveyedAt);
    const level = confidence.trim() ? Number(confidence) : null;
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180
      || !Number.isFinite(time.getTime()) || level !== null && (!Number.isFinite(level) || level < 0 || level > 1)) return null;
    return { expectedRevision: row.revision, location: { latitude: lat, longitude: lon, srid: 4326 },
      surveyedAt: time.toISOString(), attributes: { observation: observation.trim() }, confidence: level,
      mediaIds: value?.items.filter(item => item.uploadedBy === value.currentSubjectId).map(item => item.mediaId) ?? [], reason: reason.trim() };
  }
  async function previewUpload() {
    if (!file || !capturedAt || !kind || !reason.trim() || !canSubmit || !begin()) return;
    const captured = context, selectedFile = file, selectedRow = row;
    try {
      const time = new Date(capturedAt); if (!Number.isFinite(time.getTime())) throw new Error("invalid-capture-time");
      const bytes = new Uint8Array(await selectedFile.arrayBuffer());
      if (!current(captured)) return;
      if (!bytes.byteLength) throw new Error("empty-file");
      let raw = ""; for (let i = 0; i < bytes.length; i += 8192) raw += String.fromCharCode(...bytes.subarray(i, i + 8192));
      setPreview({ kind: "upload", fileName: selectedFile.name, input: { expectedRevision: selectedRow.revision,
        dataBase64: btoa(raw), contentType: selectedFile.type || "application/octet-stream", mediaKind: kind,
        capturedAt: time.toISOString(), caption: caption.trim() || null, reason: reason.trim() } });
    } catch { if (current(captured)) setError("尚未讀取到實際附件 bytes 或有效的回報拍攝時間；未送出上傳。"); }
    finally { end(captured); }
  }
  async function confirm() {
    if (!preview || !canSubmit || !begin()) return;
    const captured = context, selected = row, selectedPreview = preview;
    try {
      if (selectedPreview.kind === "upload") {
        const result = await captured.api.upload(selected, selectedPreview.input, intent("upload", selectedPreview.input));
        if (!current(captured)) return;
        if (result.ok) {
          setUploaded(result.value.attachment); setPreview(null); retry.current = null; setFile(null);
          if (fileInput.current) fileInput.current.value = "";
          setData(old => old ? { ...old, items: [...old.items.filter(item => item.mediaId !== result.value.attachment.mediaId), result.value.attachment] } : old);
          await reload(captured, selected);
        } else if (!("superseded" in result)) failure(result);
      } else {
        const result = await captured.api.submit(selected, selectedPreview.input, intent("submit", selectedPreview.input));
        if (!current(captured)) return;
        if (result.ok) {
          setSubmitted(result.value); setPreview(null); retry.current = null;
          setData(old => old ? { ...old, assignment: result.value.assignment } : old);
          await reload(captured, result.value.assignment);
        } else if (!("superseded" in result)) failure(result);
      }
    } catch { if (current(captured)) setError("命令回應未確認；請保留相同輸入與命令識別後重試，尚未顯示成功。"); }
    finally { end(captured); }
  }
  async function download(attachment: SurveyAttachment) {
    if (!begin()) return;
    const captured = context;
    try {
      const result = await captured.api.download(row, attachment);
      if (!current(captured)) return;
      if (result.ok) {
        const url = URL.createObjectURL(result.value); const link = document.createElement("a");
        link.href = url; link.download = `survey-${attachment.mediaId}.bin`; link.click(); URL.revokeObjectURL(url);
      } else if (!("superseded" in result)) failure(result);
    } catch { if (current(captured)) setError("附件內容未核驗，未開啟下載。"); }
    finally { end(captured); }
  }

  return <section aria-label="現勘回報與附件">
    <h3>現勘回報與附件</h3>
    <p>目前實體：<code>{assignment.target_entity_id}</code> · 任務：<code>{assignment.assignment_id}</code></p>
    {scope === context && error ? <p role="alert">{error}</p> : null}
    {scope === context && readError ? <p role="alert">{readError}</p> : null}
    <Button disabled={pending || reading} onClick={() => { void reload(); }}>重讀回報狀態</Button>
    {uploaded && scope === context ? <p role="status">已核驗並保存附件：<code>{uploaded.mediaId}</code> · SHA-256 {uploaded.sha256}</p> : null}
    {submitted && scope === context ? <p role="status">現勘回報已保存：<code>{submitted.surveyId}</code> · Observation {submitted.observationId} · 稽核 {submitted.auditEventIds.join("、")}；等待獨立審查，尚未批准或提升為正式資料。</p> : null}
    {value ? <>
      {row.status === "SUBMITTED" && row.survey_id && !submitted ? <p role="status">目前已保存回報：<code>{row.survey_id}</code>；不重送建立命令，審查狀態以實際證據紀錄為準。</p> : null}
      <dl><div><dt>已驗證登入帳號</dt><dd>{value.currentSubjectId}</dd></div>
        <div><dt>受派帳號／保存狀態</dt><dd>{row.assigned_to ?? "未指派"} · {row.status} · v{row.revision}</dd></div></dl>
      <label className="odp-field"><span className={styles.reviewDialogFieldLabel}>作業理由</span>
        <textarea className={styles.textareaField} value={reason} disabled={pending || !!preview} onChange={e => setReason(e.target.value)} /></label>
      <div className="odp-actions">
        <Button disabled={pending || reading || !canManage || !!preview || !sameSurveyor || !["UNASSIGNED", "ASSIGNED"].includes(row.status) || !reason.trim()} onClick={() => { void change("claim"); }}>領取任務</Button>
        <Button disabled={pending || reading || !canManage || !!preview || !sameSurveyor || !["ASSIGNED", "CLAIMED"].includes(row.status) || !reason.trim()} onClick={() => { void change("start"); }}>開始現勘</Button>
        <Button disabled={pending || reading || !canManage || !!preview || !ACTIVE.includes(row.status) || !reason.trim()} onClick={() => { void change("cancel"); }}>取消任務</Button>
      </div>
      <h4>已保存附件</h4>
      {value.items.length ? <ul>{value.items.map(item => <li key={item.mediaId}>
        <code>{item.mediaId}</code> · {item.sizeBytes} bytes · {item.caption ?? "未填說明"}
        <p>回報拍攝時間：{item.submittedCapturedAt} · 上傳者：{item.uploadedBy}</p>
        <Button disabled={pending || reading} onClick={() => { void download(item); }}>下載已核驗附件 {item.mediaId}</Button>
      </li>)}</ul> : <p>尚無已保存附件；未將未上傳內容顯示為照片。</p>}
      {row.mediaAvailability.state === "unavailable" ? <p role="status">附件內容儲存尚未設定或不可用；不接受 URI 字串當作已上傳照片。回報可依原生契約不附照片提交。</p> : <p>已綁定內容介面；每次上傳與下載仍須驗證實際 bytes、雜湊與目前權限。</p>}
      <fieldset className="odp-form" disabled={pending || reading || !canSubmit || !!preview}>
        <legend>新增附件</legend>
        <label className="odp-field">附件檔案<input ref={fileInput} type="file" onChange={e => { setFile(e.target.files?.[0] ?? null); setUploaded(null); }} /></label>
        <label className="odp-field">附件種類<select className={styles.inputField} value={kind} onChange={e => setKind(e.target.value)}>
          <option value="">請選擇</option>{[["PHOTO", "照片"], ["VIDEO", "影片"], ["AUDIO", "音訊"], ["DOCUMENT", "文件"], ["SIGNATURE", "簽名"], ["OTHER", "其他"]].map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
        <label className="odp-field">回報拍攝時間（本機時間）<input className={styles.inputField} type="datetime-local" value={capturedAt} onChange={e => setCapturedAt(e.target.value)} /></label>
        <label className="odp-field">附件說明<textarea className={styles.textareaField} value={caption} onChange={e => setCaption(e.target.value)} /></label>
        <Button disabled={!file || !kind || !capturedAt || !reason.trim() || row.mediaAvailability.state !== "configured"} onClick={() => { void previewUpload(); }}>預覽附件上傳</Button>
      </fieldset>
      <fieldset className="odp-form" disabled={pending || reading || !canSubmit || !!preview}>
        <legend>提交現勘回報</legend>
        <p>回報只納入目前登入者上傳的已核驗附件；其他上傳者的附件不會借用為本次回報。</p>
        <label className="odp-field">緯度（回報值）<input className={styles.inputField} value={latitude} onChange={e => setLatitude(e.target.value)} inputMode="decimal" /></label>
        <label className="odp-field">經度（回報值）<input className={styles.inputField} value={longitude} onChange={e => setLongitude(e.target.value)} inputMode="decimal" /></label>
        <label className="odp-field">現勘時間（本機時間）<input className={styles.inputField} type="datetime-local" value={surveyedAt} onChange={e => setSurveyedAt(e.target.value)} /></label>
        <label className="odp-field">現場觀察<textarea className={styles.textareaField} value={observation} onChange={e => setObservation(e.target.value)} /></label>
        <label className="odp-field">回報信心值（選填 0–1）<input className={styles.inputField} value={confidence} onChange={e => setConfidence(e.target.value)} inputMode="decimal" /></label>
        <Button disabled={!submission()} onClick={() => { const input = submission(); if (input) setPreview({ kind: "submit", input }); }}>預覽現勘回報</Button>
      </fieldset>
      {preview ? <section aria-label={preview.kind === "upload" ? "附件上傳預覽" : "現勘回報預覽"}>
        <h4>即將影響的實體</h4><p>{assignment.target_entity_id} · {row.assignment_id}</p>
        {preview.kind === "upload" ? <dl><div><dt>檔案／種類</dt><dd>{preview.fileName} · {preview.input.mediaKind}</dd></div>
          <div><dt>回報拍攝時間</dt><dd>{preview.input.capturedAt}</dd></div></dl>
          : <dl><div><dt>回報位置</dt><dd>{preview.input.location.latitude}, {preview.input.location.longitude}</dd></div>
            <div><dt>現勘時間</dt><dd>{preview.input.surveyedAt}</dd></div><div><dt>現場觀察</dt><dd>{preview.input.attributes.observation || "未提供"}</dd></div>
            <div><dt>信心值／附件</dt><dd>{preview.input.confidence ?? "未提供"} · {preview.input.mediaIds.length} 件已保存附件</dd></div></dl>}
        <p>理由：{preview.input.reason}</p>
        <div className="odp-actions"><Button disabled={pending || reading} onClick={() => setPreview(null)}>返回修改回報</Button>
          <Button disabled={pending || reading || !canSubmit} variant="primary" onClick={() => { void confirm(); }}>{preview.kind === "upload" ? "確認上傳附件" : "確認提交回報"}</Button></div>
      </section> : null}
    </> : scope === context && reading ? <p role="status">讀取實際回報狀態中…</p> : null}
  </section>;
}
