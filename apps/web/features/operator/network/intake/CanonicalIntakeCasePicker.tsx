"use client";

import { useEffect, useMemo, useState } from "react";
import type { CanonicalIntakeAssistedEntryRequestReceipt, CanonicalIntakePage, CanonicalIntakeReceipt, CanonicalIntakeSummary, NativeIntakeScope } from "@oday-plus/openapi-client";
import { IntakeDialogShell } from "./IntakeDialogShell";
import {
  CANONICAL_INTAKE_STAGE_LABEL, readCanonicalIntakeCase, readCanonicalIntakePage, submitCanonicalAssistedEntry, submitCanonicalURL,
  type CanonicalAssistedEntryCommand, type CanonicalIntakeReadContext, type CanonicalURLCommand,
} from "./canonicalIntakeReadClient";
import { CanonicalIdentityDecisionPanel, CanonicalIntakeFailure as ReadFailure } from "./CanonicalIdentityDecisionPanel";
import { AddListingFromUrlDialog } from "./AddListingFromUrlDialog";
import { AssistedEntryForm } from "./AssistedEntryForm";
import { useCanonicalIntakeCommand } from "./useCanonicalIntakeCommand";
import { CanonicalMatchCaseCreation } from "./CanonicalMatchCaseCreation";
import styles from "./intake.module.css";

type Load<T> = { status: "loading" } | { status: "ready"; value: T } | { status: "error"; error: unknown };

// Only the Console's account-bound transport is accepted. Replacing that
// transport or its context closes/unmounts the old dialog during render.
export function CanonicalIntakeCasePicker({ context }: { context: CanonicalIntakeReadContext }) {
  const { fetchImpl, contextKey, headers } = context;
  const binding = useMemo(() => ({ fetchImpl, contextKey, headers }), [fetchImpl, contextKey, headers]);
  const [opened, setOpened] = useState<CanonicalIntakeReadContext | null>(null);
  return (
    <div className={styles.timelineActions}>
      <button className={styles.secondaryButton} type="button" onClick={() => setOpened(binding)}>選取收件／比對案件</button>
      {opened === binding ? <SelectionDialog context={binding} onClose={() => setOpened(null)} /> : null}
    </div>
  );
}

function SelectionDialog({ context, onClose }: { context: CanonicalIntakeReadContext; onClose: () => void }) {
  const [cursor, setCursor] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [list, setList] = useState<Load<CanonicalIntakePage>>({ status: "loading" });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [commandBusy, setCommandBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [created, setCreated] = useState<CanonicalIntakeReceipt | null>(null);
  const [detail, setDetail] = useState<Load<Awaited<ReturnType<typeof readCanonicalIntakeCase>>>>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setList({ status: "loading" }); setSelectedId(null); setDetail({ status: "loading" });
    void readCanonicalIntakePage(context, cursor, controller.signal).then(
      (value) => { if (current) setList({ status: "ready", value }); },
      (error) => { if (current) setList({ status: "error", error }); },
    );
    return () => { current = false; controller.abort(); };
  }, [context, cursor, reload]);

  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    let current = true;
    setDetail({ status: "loading" });
    void readCanonicalIntakeCase(context, selectedId, controller.signal).then(
      (value) => { if (current) setDetail({ status: "ready", value }); },
      (error) => { if (current) setDetail({ status: "error", error }); },
    );
    return () => { current = false; controller.abort(); };
  }, [context, selectedId, reload]);

  const select = (id: string) => { if (id !== selectedId) { setDetail({ status: "loading" }); setSelectedId(id); } };
  const changePage = (next: string | null) => { setList({ status: "loading" }); setSelectedId(null); setCursor(next); };
  return (
    <IntakeDialogShell ariaLabel="選取收件與比對案件" className={styles.panelWide} dismissible={!commandBusy}
      onClose={() => { if (!commandBusy) onClose(); }}
      screenLabel="Dialog 選取正式收件與比對案件" testId="canonical-intake-case-picker">
      <header className={styles.dialogHead}>
        <h2 className={styles.dialogTitle}>選取收件與比對案件</h2>
        <button aria-label="關閉" disabled={commandBusy} className={styles.dialogClose} onClick={onClose} type="button">×</button>
      </header>
      <div className={styles.dialogBody}>
        <p>選取收件以查看目前的比對案件與候選主體。</p>
        {list.status === "ready" ? <button className={styles.secondaryButton} disabled={commandBusy} type="button"
          onClick={() => setAdding(true)}>從網址建立正式收件</button> : null}
        {created ? <div role="status" className={styles.noteBox}>
          <p>正式收件 {created.intake_id} · 已送出 · 版本 {created.version}</p>
          <p>稽核紀錄 {created.audit_event_id} · 查詢編號 {created.correlation_id}</p>
          <p>提交回執僅表示已收件；後續處理狀態以重新讀取的結果為準。</p>
          <button className={styles.secondaryButton} disabled={commandBusy} type="button" onClick={() => select(created.intake_id)}>開啟已提交收件</button>
        </div> : null}
        {list.status === "loading" ? <p role="status">正在讀取收件…</p> : list.status === "error" ? <ReadFailure error={list.error} /> : (
          <>
            {list.value.items.length === 0 ? <p>目前可讀範圍沒有收件。</p> : (
              <ul aria-label="可選取的收件">
                {list.value.items.map((row) => <li key={row.intake_id}>
                  <button aria-pressed={row.intake_id === selectedId} disabled={commandBusy} className={styles.secondaryButton} onClick={() => select(row.intake_id)} type="button">
                    {row.intake_id} · {CANONICAL_INTAKE_STAGE_LABEL[row.processing_state]} · {row.intake_method} · {new Date(row.submitted_at).toLocaleString("zh-TW")}
                  </button>
                </li>)}
              </ul>
            )}
            <div className={styles.timelineActions}>
              <button className={styles.secondaryButton} disabled={commandBusy || cursor === null} onClick={() => changePage(null)} type="button">返回第一頁</button>
              <button className={styles.secondaryButton} disabled={commandBusy || !list.value.next_cursor} onClick={() => changePage(list.value.next_cursor)} type="button">下一頁</button>
            </div>
          </>
        )}
        {selectedId ? <section aria-label="所選收件與比對案件" className={styles.sectionBox}>
          {detail.status === "loading" ? <p role="status">正在讀取所選收件與案件…</p> : detail.status === "error" ? <ReadFailure error={detail.error} /> : (
            <>
              <p>收件 {detail.value.intake.intake_id} · 版本 {detail.value.intake.version} · {CANONICAL_INTAKE_STAGE_LABEL[detail.value.intake.processing_state]}</p>
              {detail.value.intake.resolution ? <section aria-label="已落庫物件" className={styles.noteBox}>
                <p>物件 {detail.value.intake.resolution.listing_id} · 版本 {detail.value.intake.resolution.listing_version} · {detail.value.intake.resolution.lifecycle_state}</p>
                <p>目前主體 {detail.value.intake.resolution.effective_property_id} · 版本 {detail.value.intake.resolution.property_version}</p>
                <p>建立時主體 {detail.value.intake.resolution.property_id_at_creation}</p>
                <p>目前修訂 {detail.value.intake.resolution.current_revision_id ?? "未提供"}</p>
                {detail.value.intake.resolution.fields?.length ? <>
                  <table className={styles.compareGrid} aria-label="已落庫欄位">
                    <thead><tr><th>欄位</th><th>已保存值</th><th>資料分類</th></tr></thead>
                    <tbody>{detail.value.intake.resolution.fields.map((field) => <tr key={field.field_path}>
                      <th scope="row">{field.field_path}</th>
                      <td className={styles.summaryValue}>{field.masked ? "無法檢視" : field.effective === null ? "未提供" : String(field.effective)}</td>
                      <td>{field.classification ?? "分類未提供"}</td>
                    </tr>)}</tbody>
                  </table>
                  {detail.value.intake.resolution.fields.some((field) => field.masked)
                    ? <p>部分欄位因權限或分類資料不足而遮罩。</p> : null}
                </> : <p>目前沒有可顯示的已保存欄位資料。</p>}
              </section> : null}
              {detail.value.intake.intake_method === "URL" && detail.value.intake.processing_state === "AWAITING_ASSISTED_ENTRY"
                ? <CanonicalAssistedEntry key={`${detail.value.intake.intake_id}:${detail.value.intake.version}`}
                  context={context} intake={detail.value.intake} onBusyChange={setCommandBusy} /> : null}
              {detail.value.intake.processing_state === "AWAITING_SOURCE_AUTHORIZATION" ? <p>此收件等待來源授權；人工補錄須先由既定流程確認另有明確核准。</p> : null}
              {detail.value.matchCase === null ? (["MATCHING", "NEEDS_REVIEW"].includes(detail.value.intake.processing_state)
                ? <CanonicalMatchCaseCreation key={`${detail.value.intake.intake_id}:${detail.value.intake.version}`}
                  context={context} intake={detail.value.intake} onBusyChange={setCommandBusy} Failure={ReadFailure} />
                : <p>這筆收件目前沒有比對案件。</p>) : (
                <>
                  <p>比對案件 {detail.value.matchCase.match_case_id} · 版本 {detail.value.matchCase.version} · {detail.value.matchCase.status} · {detail.value.matchCase.outcome}</p>
                  <ul aria-label="案件候選主體">
                    {detail.value.matchCase.candidates.map((candidate) => <li key={candidate.match_candidate_id}>
                      {candidate.manual_context_version ? <p>追加人工關聯 · 案件版本 {candidate.manual_context_version}</p> : null}
                      主體 {candidate.property_id ?? "未提供"} · 物件 {candidate.listing_id ?? "未提供"} · 信心值 {candidate.confidence}
                      <p>觀察主體版本 {candidate.observed_property_version ?? "未提供"} · 觀察物件版本 {candidate.observed_listing_version ?? "未提供"}</p>
                    </li>)}
                  </ul>
                  <CanonicalIdentityDecisionPanel key={`${detail.value.matchCase.match_case_id}:${detail.value.matchCase.version}:${detail.value.intake.version}`}
                    context={context} selected={detail.value.matchCase} intake={detail.value.intake} onBusyChange={setCommandBusy} busy={commandBusy} />
                </>
              )}
            </>
          )}
        </section> : null}
      </div>
      <footer className={styles.dialogFooter}>
        <button className={styles.secondaryButton} disabled={commandBusy} onClick={() => { setList({ status: "loading" }); setSelectedId(null); setReload((n) => n + 1); }} type="button">重新讀取</button>
        <button className={styles.secondaryButton} disabled={commandBusy} onClick={onClose} type="button">關閉</button>
      </footer>
      {adding && list.status === "ready" ? <CanonicalURLSubmission context={context} onBusyChange={setCommandBusy}
        scopes={[{ label: "不指定範圍（依目前帳號權限核對）", scope: { brand_id: null, region_id: null, assigned_area_id: null, heat_zone_id: null } },
          ...list.value.items.map((row) => ({ label: `沿用收件 ${row.intake_id} 的範圍`, scope: row.scope }))]}
        onClose={() => setAdding(false)} onCreated={(value) => { setCreated(value); setAdding(false); }} /> : null}
    </IntakeDialogShell>
  );
}

function CanonicalURLSubmission({ context, scopes, onClose, onCreated, onBusyChange }: {
  context: CanonicalIntakeReadContext; scopes: ReadonlyArray<{ label: string; scope: NativeIntakeScope }>;
  onClose: () => void; onCreated: (receipt: CanonicalIntakeReceipt) => void; onBusyChange: (busy: boolean) => void;
}) {
  const command = useCanonicalIntakeCommand<CanonicalURLCommand, CanonicalIntakeReceipt>(context,
    (packet, signal) => submitCanonicalURL(context, packet, signal), onBusyChange);
  return <AddListingFromUrlDialog busy={command.busy} locked={command.hasPending} canonicalScopes={scopes}
    error={null} submitterLabel="目前登入身分" onClose={onClose}
    errorContent={command.error ? <><ReadFailure error={command.error} /><p>結果尚未確認；重試會沿用同一請求。返回收件後可重新讀取目前狀態。</p></> : null}
    onSubmit={async (input) => {
      const receipt = await command.submit(() => {
        if (!input.reason || !input.scope) throw new Error("EXPLICIT_NATIVE_SUBMISSION_REQUIRED");
        return { key: `canonical-url:${crypto.randomUUID()}`, body: { intake_method: "URL", original_url: input.url,
          scope: input.scope, reason: input.reason } };
      });
      if (receipt) onCreated(receipt);
    }} />;
}

function CanonicalAssistedEntry({ context, intake, onBusyChange }: {
  context: CanonicalIntakeReadContext; intake: CanonicalIntakeSummary; onBusyChange: (busy: boolean) => void;
}) {
  const command = useCanonicalIntakeCommand<CanonicalAssistedEntryCommand, CanonicalIntakeAssistedEntryRequestReceipt>(context,
    (packet, signal) => submitCanonicalAssistedEntry(context, packet, signal), onBusyChange);
  if (command.receipt) return <div role="status" className={styles.noteBox}>
    <p>補錄提案 {command.receipt.request_id} · 已受理 · {command.receipt.correction_ids.length} 個欄位</p>
    <p>稽核紀錄 {command.receipt.audit_event_id} · 查詢編號 {command.receipt.correlation_id}</p>
    <p>補錄內容尚待工作處理，請重新讀取收件；此回執不代表解析或比對已完成。</p>
  </div>;
  return <>
    <AssistedEntryForm busy={command.busy} canEdit native locked={command.hasPending}
      onSave={(input) => { void command.submit(() => {
        if (!input.reason) throw new Error("EXPLICIT_NATIVE_ENTRY_REASON_REQUIRED");
        return { key: `canonical-entry:${crypto.randomUUID()}`, intakeId: intake.intake_id, expectedVersion: intake.version,
          body: { reason: input.reason, risk_summary: input.riskSummary, risk_acknowledged: input.riskAcknowledged,
            fields: { ...input.fields, address: input.fields.address, rent: Number(input.fields.rent), areaPing: Number(input.fields.areaPing) } } };
      }); }} />
    {command.error ? <><ReadFailure error={command.error} /><p>結果尚未確認；重試會沿用同一份補錄內容與版本。</p>
      {command.hasPending ? <button className={styles.secondaryButton} type="button" disabled={command.busy}
        onClick={() => void command.submit(() => { throw new Error("CAPTURED_COMMAND_REQUIRED"); })}>重試同一補錄請求</button> : null}</> : null}
  </>;
}
