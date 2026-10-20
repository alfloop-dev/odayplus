"use client";

import { useEffect, useState, type ComponentType } from "react";
import type { CanonicalIntakeSummary, CanonicalMatchCasePropertyOption, CanonicalMatchCasePropertyPage, MatchCaseReceipt, MatchCaseSummary } from "@oday-plus/openapi-client";
import { readCanonicalMatchCaseProperties, submitCanonicalMatchCase, type CanonicalIntakeReadContext, type CanonicalMatchCaseCommand } from "./canonicalIntakeReadClient";
import { useCanonicalIntakeCommand } from "./useCanonicalIntakeCommand";
import styles from "./intake.module.css";

type Props = { context: CanonicalIntakeReadContext; intake: CanonicalIntakeSummary; onBusyChange: (busy: boolean) => void;
  matchCase?: MatchCaseSummary; Failure: ComponentType<{ error: unknown }>; onCaseChanged?: (changed: boolean) => void };
type Load = { state: "loading" } | { state: "error"; error: unknown } | { state: "ready"; value: CanonicalMatchCasePropertyPage };
const address = (row: CanonicalMatchCasePropertyOption) => row.address_masked ? "地址已遮罩" : row.normalized_address || "地址未提供";

export function CanonicalMatchCaseCreation(props: Props) {
  const [opened, setOpened] = useState(Boolean(props.matchCase));
  return opened ? <CaseForm {...props} /> : <>
    <p>這筆收件目前沒有比對案件。</p>
    <button type="button" className={styles.secondaryButton} onClick={() => setOpened(true)}>建立人工覆核案件</button>
  </>;
}

function CaseForm({ context, intake, onBusyChange, matchCase, Failure, onCaseChanged }: Props) {
  const [cursor, setCursor] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [page, setPage] = useState<Load>({ state: "loading" });
  const [selected, setSelected] = useState<CanonicalMatchCasePropertyOption[]>([]);
  const [confidence, setConfidence] = useState("");
  const [evidence, setEvidence] = useState("");
  const [reason, setReason] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const command = useCanonicalIntakeCommand<CanonicalMatchCaseCommand, MatchCaseReceipt>(context,
    (packet, signal) => submitCanonicalMatchCase(context, packet, signal), onBusyChange);
  useEffect(() => { if (command.receipt && matchCase) onCaseChanged?.(true); }, [command.receipt, matchCase, onCaseChanged]);
  useEffect(() => {
    const controller = new AbortController(); let current = true;
    setPage({ state: "loading" });
    void readCanonicalMatchCaseProperties(context, intake, cursor, controller.signal).then(
      (value) => { if (current) setPage({ state: "ready", value }); },
      (error) => { if (current) setPage({ state: "error", error }); },
    );
    return () => { current = false; controller.abort(); };
  }, [context, intake, cursor, reload]);
  const locked = command.busy || command.hasPending;
  const existingRoots = new Set(matchCase?.candidates.map((row) => row.property_id) ?? []);
  const remaining = 100 - (matchCase?.candidates.length ?? 0);
  const title = matchCase ? "追加人工覆核關聯" : "建立人工覆核案件";
  const stale = page.state === "ready" && selected.some((saved) => page.value.items.some((row) => row.property_id === saved.property_id && row.version !== saved.version));
  const valid = page.state === "ready" && selected.length >= (matchCase ? 1 : 2) && selected.length <= remaining && !stale
    && /^(0(?:\.\d{1,4})?|1(?:\.0{1,4})?)$/.test(confidence) && evidence.trim().length >= 3 && reason.trim().length >= 3 && acknowledged;
  if (command.receipt) return <div role="status" className={styles.noteBox}>
    <p>{matchCase ? "已追加人工關聯至案件" : "人工覆核案件"} {command.receipt.match_case_id} · 版本 {command.receipt.version} · 待覆核</p>
    <p>收件版本 {command.receipt.intake_version}。</p>
    <p>稽核紀錄 {command.receipt.audit_event_id} · 查詢編號 {command.receipt.correlation_id}</p>
    <p>請重新讀取收件，再選取同一筆以提出合併／拆分或其他確認。</p>
  </div>;
  return <section aria-label={title} className={styles.sectionBox}>
    <p>{matchCase ? `案件版本 ${matchCase.version}，原有 ${matchCase.candidates.length} 個候選。選取要追加的正式主體；原候選、排序與信心值保留。`
      : "選取至少兩個正式主體。建立後，可依既有流程提出合併／拆分並交由獨立人員覆核。"}</p>
    {page.state === "loading" ? <p role="status">正在讀取可關聯的正式主體…</p>
      : page.state === "error" ? <><Failure error={page.error} /><button type="button" className={styles.secondaryButton}
        disabled={locked} onClick={() => setReload((value) => value + 1)}>重新讀取主體</button></>
        : <fieldset disabled={locked}>
          <legend>可選取的正式主體</legend>
          {!page.value.items.filter((row) => !existingRoots.has(row.property_id)).length ? <p>目前這一頁沒有可選取的主體。</p>
            : page.value.items.filter((row) => !existingRoots.has(row.property_id)).map((row) => {
            const saved = selected.find((item) => item.property_id === row.property_id);
            const shown = saved ?? row;
            return <label key={row.property_id} className={styles.fieldLabel}>
              <input type="checkbox" checked={Boolean(saved)} disabled={!saved && selected.length >= remaining}
                onChange={(event) => {
                  const checked = event.target.checked;
                  setSelected((items) => checked ? items.some((item) => item.property_id === row.property_id) ? items : [...items, row]
                    : items.filter((item) => item.property_id !== row.property_id));
                }} />
              {address(shown)} · {shown.property_id} · 版本 {shown.version}
              {saved && saved.version !== row.version ? "（已更新，請取消後重新選取）" : ""}
            </label>;
          })}
          <div className={styles.timelineActions}>
            <button type="button" className={styles.secondaryButton} disabled={cursor === null} onClick={() => setCursor(null)}>主體第一頁</button>
            <button type="button" className={styles.secondaryButton} disabled={!page.value.next_cursor} onClick={() => setCursor(page.value.next_cursor)}>主體下一頁</button>
          </div>
        </fieldset>}
    <ul aria-label="已選取的主體與送出版本">{selected.map((row) => <li key={row.property_id}>{address(row)} · {row.property_id} · 版本 {row.version}</li>)}</ul>
    <fieldset disabled={locked}>
      <label className={styles.fieldLabel}>人工判斷信心值（0～1，最多四位小數）<input className={styles.input}
        inputMode="decimal" value={confidence} onChange={(event) => setConfidence(event.target.value)} /></label>
      <label className={styles.fieldLabel}>人工關聯依據<textarea className={styles.textarea} maxLength={4000} value={evidence} onChange={(event) => setEvidence(event.target.value)} /></label>
      <label className={styles.fieldLabel}>{matchCase ? "追加關聯原因" : "建立案件原因"}<textarea className={styles.textarea} maxLength={4000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
      <label><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />我已核對所選正式主體、版本與人工關聯依據</label>
    </fieldset>
    {command.error ? <><Failure error={command.error} /><p>結果尚未確認；重試沿用相同主體、版本及內容。案件或版本已變動時，請重新讀取收件。</p>
      <button type="button" className={styles.secondaryButton} disabled={command.busy} onClick={() => void command.submit(() => { throw new Error("CAPTURED_COMMAND_REQUIRED"); })}>重試同一案件請求</button></>
      : <button type="button" className={styles.primaryButton} disabled={locked || !valid} onClick={() => void command.submit(() => ({
        intakeId: intake.intake_id, expectedVersion: intake.version, key: `canonical-case:${crypto.randomUUID()}`,
        body: { outcome: "POSSIBLE_MATCH", confidence, risk_acknowledged: true, reason: reason.trim(),
          ...(matchCase ? { append_to_case: { match_case_id: matchCase.match_case_id, version: matchCase.version } } : {}),
          supporting_signals: [{ kind: "HUMAN_REVIEW_CONTEXT", description: evidence.trim() }], contradictory_signals: [],
          candidates: selected.map((row) => ({ property_id: row.property_id, property_version: row.version, confidence,
            listing_id: null, listing_version: null, evidence: { context_origin: "HUMAN_REVIEW_CONTEXT" } })) },
      }))}>{matchCase ? "提交人工關聯" : title}</button>}
  </section>;
}
