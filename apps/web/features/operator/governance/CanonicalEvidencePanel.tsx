"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import styles from "../governance.module.css";
import { operatorSubjectId } from "../operatorSecurityHeaders";
import {
  createCanonicalEvidence, downloadCanonicalEvidence, fetchCanonicalEvidenceHistory,
  fetchCanonicalEvidenceSources, previewCanonicalEvidence,
  type CanonicalEvidenceHistory, type CanonicalEvidencePreview, type CanonicalEvidenceSelection,
  type CanonicalEvidenceSource,
} from "./canonicalEvidenceApi";

type PreviewRow = Record<string, unknown>;
function row(value: unknown): PreviewRow {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? { ...value } : {};
}
function label(value: unknown): string {
  return typeof value === "string" && value.length ? value
    : typeof value === "number" && Number.isFinite(value) ? String(value) : "未提供";
}
function decisionLabel(value: unknown): string {
  const labels: Record<string, string> = { approved: "核准", rejected: "駁回", returned: "退回", GO: "GO", WAIT: "WAIT", REJECT: "REJECT", RETURN: "RETURN" };
  return typeof value === "string" ? labels[value] ?? value : "未提供";
}

export function CanonicalEvidencePanel({ roleId, canExport, requestedDecisionId }: { roleId?: string; canExport: boolean; requestedDecisionId?: string }) {
  const subject = operatorSubjectId();
  const tenant = typeof window === "undefined" ? "" : window.sessionStorage.getItem("oday.operator.tenant") ?? "";
  const context = useRef({ roleId, subject, tenant, canExport, requestedDecisionId, mounted: true });
  if (context.current.roleId !== roleId || context.current.subject !== subject || context.current.tenant !== tenant
      || context.current.canExport !== canExport || context.current.requestedDecisionId !== requestedDecisionId) context.current = { roleId, subject, tenant, canExport, requestedDecisionId, mounted: true };
  const current = context.current;
  const active = useCallback(() => context.current === current && current.mounted, [current]);
  const sequence = useRef(0);
  const busy = useRef(false);
  const intent = useRef<{ signature: string; key: string } | null>(null);
  const completed = useRef(new Map<string, string>());
  const [created, setCreated] = useState<Record<string, string>>({});
  const [readContext, setReadContext] = useState<object | null>(null);
  const [sources, setSources] = useState<CanonicalEvidenceSource[]>([]);
  const [history, setHistory] = useState<CanonicalEvidenceHistory[]>([]);
  const [selected, setSelected] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [reason, setReason] = useState("");
  const [audit, setAudit] = useState(true);
  const [decision, setDecision] = useState(true);
  const [format, setFormat] = useState<"pdf" | "csv">("pdf");
  const [preview, setPreview] = useState<{ signature: string; value: CanonicalEvidencePreview } | null>(null);
  const [running, setRunning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    current.mounted = true;
    const n = ++sequence.current;
    busy.current = false; intent.current = null; completed.current.clear(); setCreated({});
    setReadContext(current); setLoading(true); setSources([]); setHistory([]); setSelected("");
    setFrom(""); setTo(""); setReason(""); setPreview(null); setError(""); setNotice(""); setRunning(false);
    if (!canExport) { setLoading(false); return () => { current.mounted = false; sequence.current += 1; }; }
    void Promise.all([fetchCanonicalEvidenceSources(roleId), fetchCanonicalEvidenceHistory(roleId)]).then(([rows, records]) => {
      if (!active() || sequence.current !== n) return;
      setSources(rows); setHistory(records); setLoading(false);
      const requested = rows.find((row) => row.decision_id === current.requestedDecisionId);
      if (requested) {
        setSelected(requested.decision_id); setFrom(new Date(requested.period_start).toISOString().slice(0, 10)); setTo(new Date(requested.period_end).toISOString().slice(0, 10));
      }
    }).catch((err: unknown) => {
      if (!active() || sequence.current !== n) return;
      setError(err instanceof Error ? err.message : "正式證據來源讀取失敗。"); setLoading(false);
    });
    return () => { current.mounted = false; sequence.current += 1; };
    // Each identity/role/authority transition captures a new object, including A→B→A.
  }, [active, canExport, current, roleId]);

  const ready = readContext === current && !loading && canExport;
  const source = sources.find((row) => row.decision_id === selected);
  const sections: Array<"decision_log" | "audit"> = [...(decision ? ["decision_log" as const] : []), ...(audit ? ["audit" as const] : [])];
  const selection: CanonicalEvidenceSelection | null = source && from && to && reason.trim() && sections.length
    && from <= to ? { decision_id: source.decision_id, expected_decision_version: source.decision_version,
      sections, reason: reason.trim(), from_time: `${from}T00:00:00.000Z`, to_time: `${to}T23:59:59.999999Z`, format: "json" } : null;
  const signature = selection ? JSON.stringify(selection) : "";
  const shownPreview = ready && preview?.signature === signature ? preview.value : null;
  const previewKey = shownPreview ? `${signature}:${shownPreview.artifact_sha256}` : "";
  const knownCreatedId = previewKey ? created[previewKey] : undefined;

  async function perform(command: "preview" | "create") {
    if (!ready || !active() || busy.current || !selection || !source || (command === "create" && !shownPreview)) return;
    const n = sequence.current;
    if (command === "create" && completed.current.has(previewKey)) {
      setNotice(`已建立 ${completed.current.get(previewKey)}；請重新讀取投遞狀態，不會再次產生。`);
      return;
    }
    busy.current = true; setRunning(true); setError(""); setNotice("");
    try {
      if (command === "preview") {
        const value = await previewCanonicalEvidence(selection, roleId);
        if (!active() || sequence.current !== n) return;
        if (value.document.source_digest !== source.source_digest) throw new Error("決策來源已變更，請重新讀取來源後預覽。");
        setPreview({ signature, value });
      } else {
        if (!shownPreview) return;
        const capturedPreview = `${signature}:${shownPreview.artifact_sha256}`;
        if (intent.current?.signature !== capturedPreview) intent.current = { signature: capturedPreview, key: `ik-evidence-${crypto.randomUUID()}` };
        const id = await createCanonicalEvidence(selection, intent.current.key, shownPreview.artifact_sha256, roleId);
        if (!active() || sequence.current !== n) return;
        completed.current.set(capturedPreview, id);
        setCreated(Object.fromEntries(completed.current));
        intent.current = null;
        setNotice(`已建立 ${id}；投遞狀態待確認。`);
        try {
          const rows = await fetchCanonicalEvidenceHistory(roleId);
          if (!active() || sequence.current !== n) return;
          setHistory(rows);
          const actual = rows.find((row) => row.export_id === id);
          if (actual?.download_ready && actual.external_acknowledged) setNotice(`已核對 ${id} 的真實外部投遞收據，可下載已驗證的檔案。`);
        } catch {
          if (active() && sequence.current === n) setError(`證據包 ${id} 已建立；投遞狀態重讀失敗，請重新讀取，不需再次產生。`);
        }
      }
    } catch (err: unknown) {
      if (!active() || sequence.current !== n) return;
      setError(err instanceof Error ? err.message : "正式證據作業失敗。");
    } finally {
      if (active() && sequence.current === n) { busy.current = false; setRunning(false); }
    }
  }

  async function reload() {
    if (!active() || busy.current || loading || !canExport) return;
    const n = sequence.current;
    busy.current = true; setRunning(true); setError("");
    try {
      const [rows, records] = await Promise.all([fetchCanonicalEvidenceSources(roleId), fetchCanonicalEvidenceHistory(roleId)]);
      if (!active() || sequence.current !== n) return;
      setSources(rows); setHistory(records);
      const actual = rows.find((row) => row.decision_id === selected);
      if (selected && !actual) { setSelected(""); setPreview(null); }
      else if (actual && preview && actual.source_digest !== preview.value.document.source_digest) setPreview(null);
      if (!selected) {
        const requested = rows.find((row) => row.decision_id === current.requestedDecisionId);
        if (requested) {
          setSelected(requested.decision_id); setFrom(new Date(requested.period_start).toISOString().slice(0, 10));
          setTo(new Date(requested.period_end).toISOString().slice(0, 10));
        }
      }
      if (knownCreatedId) {
        const record = records.find((row) => row.export_id === knownCreatedId);
        setNotice(record?.download_ready && record.external_acknowledged
          ? `已核對 ${knownCreatedId} 的真實外部投遞收據，可下載已驗證的檔案。`
          : `證據包 ${knownCreatedId} 已建立；目前尚未確認可下載，不會再次產生。`);
      }
    } catch {
      if (active() && sequence.current === n) setError(knownCreatedId
        ? `證據包 ${knownCreatedId} 已建立；投遞狀態重讀失敗，請重新讀取，不需再次產生。`
        : "正式來源與投遞狀態重讀失敗；未再次產生證據包。");
    } finally {
      if (active() && sequence.current === n) { busy.current = false; setRunning(false); }
    }
  }

  async function download(row: CanonicalEvidenceHistory) {
    if (!ready || !active() || busy.current || !row.download_ready || !row.external_acknowledged) return;
    const n = sequence.current;
    busy.current = true; setRunning(true); setError("");
    try {
      const file = await downloadCanonicalEvidence(row.export_id, format, roleId);
      if (!active() || sequence.current !== n) return;
      const url = URL.createObjectURL(file.blob);
      try {
        const anchor = document.createElement("a"); anchor.href = url; anchor.download = file.filename;
        document.body.appendChild(anchor); anchor.click(); anchor.remove();
      } finally { URL.revokeObjectURL(url); }
      setNotice("已接收並核對檔案 SHA-256；本次匯出存取由伺服器記錄。");
    } catch (err: unknown) {
      if (active() && sequence.current === n) setError(err instanceof Error ? err.message : "證據檔案尚不可下載。");
    } finally {
      if (active() && sequence.current === n) { busy.current = false; setRunning(false); }
    }
  }

  const reference = shownPreview?.document.sections.decision_log?.[0];
  const auditRows = shownPreview?.document.sections.audit ?? [];
  const latestAudit = auditRows[auditRows.length - 1];
  const chain = shownPreview?.document.recorded_chain;
  const prediction = row(chain?.prediction);
  const intervals = row(prediction.intervals);
  const human = row(chain?.human_decision);
  const recommendation = row(chain?.recommendation);
  const execution = row(chain?.execution);
  const realized = row(execution.record);
  const snapshots = reference?.source_snapshot_ids;
  const confidence = prediction.confidence_status === "measured" && typeof prediction.confidence === "number"
    && Number.isFinite(prediction.confidence) ? `${(prediction.confidence * 100).toFixed(1)}%` : "未取得量測";
  const coverage = row(chain?.coverage);
  const missingLabels: Record<string, string> = { outcome: "成效", execution: "候選實現", input_snapshot_references: "輸入快照參照" };
  const missing = Array.isArray(coverage.missing) ? coverage.missing.map((item) => typeof item === "string" ? missingLabels[item] ?? "未提供" : "未提供").join("、") : "未提供";

  return <section className={styles.evidencePackageGrid} aria-label="Evidence Package export">
    <div className={styles.exportPanel} style={{ minWidth: 0 }}>
      <div className={styles.exportPanelTitle}>產生 Evidence Package</div>
      <p className={styles.exportPanelSubtitle}>目前僅支援 SiteScore 單筆 Decision Log／Audit 及已有紀錄的預測、簽核與候選實現。原始來源內容、補助資格、Outcome／SLA 匯出尚未支援；決策鏈仍為 PARTIAL。</p>
      {!canExport ? <p className={styles.readOnlyNotice} data-testid="governance-export-readonly">尚未取得伺服器驗證的證據匯出權限。</p> : null}
      {loading || readContext !== current ? <p role="status">讀取正式證據來源…</p> : null}
      {ready && sources.length === 0 && !error ? <p className={styles.emptyState}>目前沒有可驗證且有權限的已決策 SiteScore 來源。</p> : null}
      <fieldset className={styles.exportFields} disabled={!ready || running} aria-label="Evidence Package 匯出設定" aria-busy={running}>
        <label className={styles.formLabel} htmlFor="canonical-evidence-source">決策來源（SiteScore）</label>
        <select id="canonical-evidence-source" className={styles.formSelect} value={selected} onChange={(event) => {
          const row = sources.find((item) => item.decision_id === event.target.value);
          setSelected(row?.decision_id ?? ""); setFrom(row ? new Date(row.period_start).toISOString().slice(0, 10) : "");
          setTo(row ? new Date(row.period_end).toISOString().slice(0, 10) : ""); setPreview(null); setNotice("");
        }}><option value="">請選擇真實決策</option>{sources.map((row) => <option value={row.decision_id} key={row.decision_id}>{row.candidate_id} · {row.decision_id} · v{row.decision_version}</option>)}</select>
        <div className={styles.formGrid2Col}>
          <div><label className={styles.formLabel} htmlFor="governance-export-from">起始日（UTC）</label><input id="governance-export-from" className={styles.formInputDate} type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></div>
          <div><label className={styles.formLabel} htmlFor="governance-export-to">結束日（UTC）</label><input id="governance-export-to" className={styles.formInputDate} type="date" value={to} onChange={(e) => setTo(e.target.value)} /></div>
        </div>
        <div className={styles.checkboxGroup}>
          <label className={styles.checkboxLabel}><input type="checkbox" checked={decision} onChange={(e) => setDecision(e.target.checked)} />Decision Log</label>
          <label className={styles.checkboxLabel}><input type="checkbox" checked={audit} onChange={(e) => setAudit(e.target.checked)} />Audit Trail</label>
        </div>
        <label className={styles.formLabel} htmlFor="canonical-evidence-reason">匯出理由</label>
        <textarea id="canonical-evidence-reason" className={styles.formInputDate} value={reason} onChange={(e) => setReason(e.target.value)} />
        <label className={styles.formLabel} htmlFor="governance-export-format">下載格式</label>
        <select id="governance-export-format" className={styles.formSelect} value={format} onChange={(e) => setFormat(e.target.value === "csv" ? "csv" : "pdf")}><option value="pdf">PDF</option><option value="csv">CSV (zip)</option></select>
        <button className={styles.downloadButton} type="button" disabled={!selection} onClick={() => void perform("preview")}>預覽實際欄位與遮罩</button>
      </fieldset>
      {shownPreview ? <div className={styles.exportResult} data-testid="canonical-evidence-preview" style={{ minWidth: 0, overflowWrap: "anywhere" }}>
        <strong>CONFIDENTIAL · actor 已遮罩</strong><span>保留策略 {shownPreview.retention_class} · {shownPreview.retention_days} 天</span>
        <span>預覽不代表已留存或已投遞。以下為選定紀錄摘要；完整欄位、事件與雜湊保留於下載檔。</span>
        <dl>
          <div><dt>決策</dt><dd>{shownPreview.decision_id} · v{shownPreview.decision_version}</dd></div>
          {reference ? <>
            <div><dt>候選標的</dt><dd>{label(reference.candidate_id)}</dd></div>
            <div><dt>報告</dt><dd>{label(reference.report_id)} · v{label(reference.report_version)}</dd></div>
            <div><dt>簽核紀錄</dt><dd>{label(reference.approval_id)} · {decisionLabel(reference.approval_status)}</dd></div>
            <div><dt>決策人（已遮罩）</dt><dd>{label(reference.decision_actor)}</dd></div>
            <div><dt>決策時間</dt><dd>{label(reference.decided_at)}</dd></div>
            <div><dt>輸入快照參照</dt><dd>{Array.isArray(snapshots) && snapshots.length ? `${snapshots.length} 筆（完整參照見下載檔）` : "未提供"}</dd></div>
          </> : null}
          <div><dt>已選稽核紀錄</dt><dd>{auditRows.length} 筆{latestAudit ? ` · 最近 ${label(latestAudit.occurred_at)}` : ""}</dd></div>
        </dl>
        {chain ? <div><strong>已記錄決策鏈（PARTIAL）</strong><dl>
          {(["m1", "m3", "m6", "m12"] as const).map((name) => {
            const interval = row(intervals[name]);
            return <div key={name}><dt>第 {name.slice(1)} 個月預測</dt><dd>P50 {label(interval.p50)} · P10–P90 {label(interval.p10)}–{label(interval.p90)}</dd></div>;
          })}
          <div><dt>信心量測</dt><dd>{confidence}</dd></div>
          <div><dt>模型紀錄參照（非核准狀態）</dt><dd>{label(prediction.model_reference)}</dd></div>
          <div><dt>系統建議／最終決策</dt><dd>{decisionLabel(recommendation.recorded_recommendation)}／{decisionLabel(recommendation.recorded_network_action)}</dd></div>
          <div><dt>原決策理由（已遮罩）</dt><dd>{label(human.reason_masked)}</dd></div>
          <div><dt>候選實現</dt><dd>{execution.status === "recorded"
            ? `已記錄 · ${label(realized.recorded_at)}（不代表實體開店或營收）`
            : execution.status === "not_applicable" ? "此決策未授權新的候選實現" : "候選實現紀錄未取得"}</dd></div>
          <div><dt>成效</dt><dd>成效紀錄未取得</dd></div>
          <div><dt>缺漏</dt><dd>{missing}</dd></div>
        </dl></div> : <p>此筆僅含已記錄參照；未提供決策鏈明細。</p>}
      </div> : null}
      <button className={styles.exportButton} data-testid="governance-export-button" disabled={!shownPreview || running || Boolean(knownCreatedId)} type="button" onClick={() => void perform("create")}>{running ? "處理中…" : knownCreatedId ? "證據包已建立" : "產生 Evidence Package"}</button>
      {knownCreatedId && readContext === current ? <p data-testid="canonical-evidence-created">已建立證據包：{knownCreatedId}</p> : null}
      {notice && readContext === current ? <p role="status">{notice}</p> : null}
      {error && readContext === current ? <p role="alert">{error}</p> : null}
      <button type="button" className={styles.downloadButton} disabled={loading || running || !canExport} onClick={() => void reload()}>重新讀取正式來源與投遞狀態</button>
    </div>
    <div className={styles.exportPanel} style={{ minWidth: 0 }}>
      <div className={styles.exportPanelTitle}>匯出紀錄</div>
      <p className={styles.exportPanelSubtitle}>投遞收據對應留存的 JSON 紀錄；PDF／CSV 為下載時產生的相同欄位文件。</p>
      {ready ? history.map((row) => <div className={styles.historyItem} key={row.export_id} style={{ overflowWrap: "anywhere" }}>
        <span className={styles.historyFileId}>{row.export_id}</span><span>{row.decision_id}</span><span>{row.reason}</span><time>{row.generated_at}</time>
        <span>{row.purged_at ? "內容已清除" : row.external_acknowledged ? "已有真實外部投遞收據" : "等待外部投遞收據"}{row.legal_hold ? " · 法律保留" : ""}</span>
        <button className={styles.downloadButton} type="button" disabled={!row.download_ready || running} onClick={() => void download(row)}>下載 {format === "pdf" ? "PDF" : "CSV (zip)"}</button>
      </div>) : null}
      {ready && history.length === 0 ? <p>目前沒有本登入者可讀的正式匯出紀錄。</p> : null}
    </div>
  </section>;
}
