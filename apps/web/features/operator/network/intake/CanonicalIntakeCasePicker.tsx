"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import type { CanonicalIntakeAssistedEntryRequestReceipt, CanonicalIntakePage, CanonicalIntakeReceipt, CanonicalIntakeSummary, NativeIntakeScope } from "@oday-plus/openapi-client";
import { IntakeDialogShell } from "./IntakeDialogShell";
import { IntakeProcessingDetail } from "./IntakeProcessingDetail";
import {
  CANONICAL_INTAKE_STAGE_LABEL, CanonicalIntakeReadError, readCanonicalIntakeCase, readCanonicalIntakePage, submitCanonicalAssistedEntry, submitCanonicalURL,
  readCanonicalAssistedEntryReceipt, readCanonicalURLReceipt,
  type CanonicalAssistedEntryCommand, type CanonicalIntakeReadContext, type CanonicalURLCommand,
} from "./canonicalIntakeReadClient";
import { CanonicalIdentityDecisionPanel, CanonicalIntakeFailure as ReadFailure } from "./CanonicalIdentityDecisionPanel";
import { AddListingFromUrlDialog } from "./AddListingFromUrlDialog";
import { AssistedEntryForm } from "./AssistedEntryForm";
import { useCanonicalIntakeCommand, useCanonicalIntakeCommandState } from "./useCanonicalIntakeCommand";
import { CanonicalMatchCaseCreation } from "./CanonicalMatchCaseCreation";
import { parseUrlState, intakePathSelection, buildDurableDetailHref, buildInboxReturnHref, intakeNavigationHash } from "./urlState";
import styles from "./intake.module.css";

type Load<T> = { status: "loading" } | { status: "ready"; value: T } | { status: "error"; error: unknown };

// Only the Console's account-bound transport is accepted. Replacing that
// transport or its context closes/unmounts the old dialog during render.
export function CanonicalIntakeCasePicker({ context }: { context: CanonicalIntakeReadContext }) {
  const { fetchImpl, contextKey, headers } = context;
  const binding = useMemo(() => ({ fetchImpl, contextKey, headers }), [fetchImpl, contextKey, headers]);
  const router = useRouter(), pathname = usePathname(), params = useSearchParams();
  const query = params.toString(), state = parseUrlState(params);
  const pathId = intakePathSelection(pathname);
  const selectedId = pathId !== undefined ? pathId : state.selectedId ?? null;
  // This attests navigation to the transport that observed it; identity changes
  // alone cannot adopt an earlier dialog. Selection itself lives only in URL.
  const navigation = useRef({ location: pathname + "?" + query, binding, reader: binding, version: 0 });
  const location = pathname + "?" + query;
  if (navigation.current.reader !== binding) {
    navigation.current.reader = binding; navigation.current.version += 1;
  }
  if (navigation.current.location !== location) {
    navigation.current.location = location; navigation.current.binding = binding;
  }
  const [opened, setOpened] = useState<CanonicalIntakeReadContext | null>(binding);
  const { busy } = useCanonicalIntakeCommandState();
  const navigate = (href: string) => router.push(href, { scroll: false });
  const detailLink = (id: string) => buildDurableDetailHref(id, { ...state, intakeKind: "canonical" }, params, typeof window === "undefined" ? "" : window.location.hash);
  const browse = (cursor: string | null = null) => {
    setOpened(binding);
    navigate(buildInboxReturnHref({ ...state, intakeKind: "canonical", canonicalCursor: cursor ?? "",
      selectedId: null, dialog: "detail", fixFieldKey: null, decisionKind: null, receiptId: null }, params, window.location.hash));
  };
  const select = (id: string) => navigate(detailLink(id));
  const close = () => {
    setOpened(null);
    navigate(buildInboxReturnHref({ ...state, intakeKind: null, canonicalCursor: null,
      selectedId: null, dialog: null, fixFieldKey: null, decisionKind: null, receiptId: null }, params, window.location.hash));
  };
  return (
    <div className={selectedId && state.intakeKind === "canonical" ? undefined : styles.timelineActions}>
      {!selectedId || state.intakeKind !== "canonical" ? <button className={styles.secondaryButton} disabled={busy} type="button" onClick={() => browse()}>選取收件／比對案件</button> : null}
      {state.intakeKind === "canonical" && (opened === binding || navigation.current.binding === binding)
        ? <SelectionDialog key={navigation.current.version} context={binding} selectedId={selectedId} cursor={state.canonicalCursor ?? undefined}
          onClose={close} onSelect={select} onPage={browse}
          detailHref={selectedId ? detailLink(selectedId) : ""}
          activeSection={state.activeSection} onSectionChange={(activeSection) => {
            const next = new URLSearchParams(params.toString()); next.set("section", activeSection);
            router.replace(pathname + "?" + next.toString() + intakeNavigationHash(window.location.hash), { scroll: false });
          }} /> : null}
    </div>
  );
}

function SelectionDialog({ context, selectedId, cursor, onClose, onSelect, onPage, detailHref, activeSection, onSectionChange }: {
  context: CanonicalIntakeReadContext; selectedId: string | null; cursor: string | undefined;
  onClose: () => void; onSelect: (id: string) => void; onPage: (cursor: string | null) => void;
  detailHref: string; activeSection?: string | null; onSectionChange: (section: string) => void;
}) {
  const [reload, setReload] = useState(0);
  const [loadedList, setList] = useState<{ cursor: string; reload: number; value: Load<CanonicalIntakePage> } | null>(null);
  const list: Load<CanonicalIntakePage> = loadedList?.cursor === cursor && loadedList?.reload === reload
    ? loadedList.value : { status: "loading" };
  const { busy: commandBusy } = useCanonicalIntakeCommandState();
  const [adding, setAdding] = useState(false);
  const [created, setCreated] = useState<CanonicalIntakeReceipt | null>(null);
  const [loadedDetail, setDetail] = useState<{ id: string; reload: number; value: Load<Awaited<ReturnType<typeof readCanonicalIntakeCase>>> } | null>(null);
  const detail: Load<Awaited<ReturnType<typeof readCanonicalIntakeCase>>> = loadedDetail?.id === selectedId && loadedDetail?.reload === reload
    ? loadedDetail.value : { status: "loading" };

  useEffect(() => {
    if (cursor === undefined || selectedId !== null) return;
    const controller = new AbortController();
    let current = true;
    setList({ cursor, reload, value: { status: "loading" } });
    void readCanonicalIntakePage(context, cursor || null, controller.signal).then(
      (value) => { if (current) setList({ cursor, reload, value: { status: "ready", value } }); },
      (error) => { if (current) setList({ cursor, reload, value: { status: "error", error } }); },
    );
    return () => { current = false; controller.abort(); };
  }, [context, cursor, reload, selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    let current = true;
    setDetail({ id: selectedId, reload, value: { status: "loading" } });
    void readCanonicalIntakeCase(context, selectedId, controller.signal).then(
      (value) => { if (current) setDetail({ id: selectedId, reload, value: { status: "ready", value } }); },
      (error) => { if (current) setDetail({ id: selectedId, reload, value: { status: "error", error } }); },
    );
    return () => { current = false; controller.abort(); };
  }, [context, selectedId, reload]);

  const select = (id: string) => { if (id !== selectedId) onSelect(id); };
  const changePage = onPage;
  const createdReceipt = (created ? <div role="status" className={styles.noteBox}>
          <p>正式收件 {created.intake_id} · 已送出 · 版本 {created.version}</p>
          <p>稽核紀錄 {created.audit_event_id} · 查詢編號 {created.correlation_id}</p>
          <p>提交回執僅表示已收件；後續處理狀態以重新讀取的結果為準。</p>
          <button className={styles.secondaryButton} disabled={commandBusy} type="button" onClick={() => select(created.intake_id)}>開啟已提交收件</button>
        </div> : null);
  const selectedContent = (selectedId ? <section data-intake-section="identity" aria-label="所選收件與比對案件" className={styles.sectionBox}>
          {detail.status === "loading" ? <p role="status">正在讀取所選收件與案件…</p> : detail.status === "error" ? <ReadFailure error={detail.error} /> : (
            <>
              {detail.value.intake.intake_method === "URL" && detail.value.intake.processing_state === "AWAITING_ASSISTED_ENTRY"
                ? <CanonicalAssistedEntry key={`${detail.value.intake.intake_id}:${detail.value.intake.version}`}
                  context={context} intake={detail.value.intake} /> : null}
              {detail.value.intake.processing_state === "AWAITING_SOURCE_AUTHORIZATION" ? <p>此收件等待來源授權；人工補錄須先由既定流程確認另有明確核准。</p> : null}
              {detail.value.matchCase === null ? (["MATCHING", "NEEDS_REVIEW"].includes(detail.value.intake.processing_state)
                ? <CanonicalMatchCaseCreation key={`${detail.value.intake.intake_id}:${detail.value.intake.version}`}
                  context={context} intake={detail.value.intake} Failure={ReadFailure} />
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
                    context={context} selected={detail.value.matchCase} intake={detail.value.intake} busy={commandBusy} />
                </>
              )}
            </>
          )}
        </section> : null);
  const submission = (adding && list.status === "ready" ? <CanonicalURLSubmission context={context}
        scopes={[{ label: "不指定範圍（依目前帳號權限核對）", scope: { brand_id: null, region_id: null, assigned_area_id: null, heat_zone_id: null } },
          ...list.value.items.map((row) => ({ label: `沿用收件 ${row.intake_id} 的範圍`, scope: row.scope }))]}
        onClose={() => setAdding(false)} onCreated={(value) => { setCreated(value); setAdding(false); onSelect(value.intake_id); }} /> : null);
  if (selectedId) return <IntakeProcessingDetail intakeId={selectedId}
    readContext={context}
    canonical={detail.status === "ready" ? detail.value.intake : null}
    matchCase={detail.status === "ready" ? detail.value.matchCase : null} detailHref={detailHref}
    activeSection={activeSection} onSectionChange={onSectionChange} busy={commandBusy}
    onClose={() => onPage(cursor ?? null)} onRefresh={() => setReload((n) => n + 1)} testId="canonical-intake-case-picker">
    {createdReceipt}{selectedContent}{submission}
  </IntakeProcessingDetail>;
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
        {cursor !== undefined && list.status === "ready" ? <button className={styles.secondaryButton} disabled={commandBusy} type="button"
          onClick={() => setAdding(true)}>從網址建立正式收件</button> : null}
        {createdReceipt}

        {cursor === undefined ? <button className={styles.secondaryButton} disabled={commandBusy} type="button" onClick={() => onPage(null)}>選取其他收件</button> : list.status === "loading" ? <p role="status">正在讀取收件…</p> : list.status === "error" ? <ReadFailure error={list.error} /> : (
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
              <button className={styles.secondaryButton} disabled={commandBusy || !cursor} onClick={() => changePage(null)} type="button">返回第一頁</button>
              <button className={styles.secondaryButton} disabled={commandBusy || !list.value.next_cursor} onClick={() => changePage(list.value.next_cursor)} type="button">下一頁</button>
            </div>
          </>
        )}
      </div>
      <footer className={styles.dialogFooter}>
        <button className={styles.secondaryButton} disabled={commandBusy} onClick={() => setReload((n) => n + 1)} type="button">重新讀取</button>
        <button className={styles.secondaryButton} disabled={commandBusy} onClick={onClose} type="button">關閉</button>
      </footer>
      {submission}
    </IntakeDialogShell>
  );
}

function CanonicalURLSubmission({ context, scopes, onClose, onCreated }: {
  context: CanonicalIntakeReadContext; scopes: ReadonlyArray<{ label: string; scope: NativeIntakeScope }>;
  onClose: () => void; onCreated: (receipt: CanonicalIntakeReceipt) => void;
}) {
  const command = useCanonicalIntakeCommand<CanonicalURLCommand, CanonicalIntakeReceipt>(context,
    (packet, signal) => submitCanonicalURL(context, packet, signal), (packet) => `網址送件：${packet.body.original_url}`,
    (receipt) => `收件 ${receipt.intake_id} · 版本 ${receipt.version} · ${receipt.processing_state}`,
    (packet, signal) => readCanonicalURLReceipt(context, packet, signal));
  if (command.receipt) return <div role="status" className={styles.noteBox}>
    <p>正式收件 {command.receipt.intake_id} · 已送出 · 版本 {command.receipt.version}</p>
    <p>稽核紀錄 {command.receipt.audit_event_id} · 查詢編號 {command.receipt.correlation_id}</p>
    <p>提交回執僅表示已收件；後續處理狀態以重新讀取的結果為準。</p>
    <button type="button" className={styles.secondaryButton} onClick={onClose}>返回收件</button>
  </div>;
  if (command.blocked) return <p role="status">先前收件操作尚待確認；可關閉此視窗，從原操作摘要重試。</p>;
  return <AddListingFromUrlDialog busy={command.busy} locked={command.hasPending} canonicalScopes={scopes}
    error={null} submitterLabel="目前登入身分" onClose={onClose}
    errorContent={command.error ? <><ReadFailure error={command.error} />{command.hasPending ? <p>結果尚未確認；重試會沿用同一請求。返回收件後仍可從原操作摘要重試。</p> : null}</> : null}
    onSubmit={async (input) => {
      const receipt = await command.submit(() => {
        if (!input.reason || !input.scope) throw new Error("EXPLICIT_NATIVE_SUBMISSION_REQUIRED");
        return { key: `canonical-url:${crypto.randomUUID()}`, body: { intake_method: "URL", original_url: input.url,
          scope: input.scope, reason: input.reason } };
      });
      if (receipt) onCreated(receipt);
    }} />;
}

function CanonicalAssistedEntry({ context, intake }: {
  context: CanonicalIntakeReadContext; intake: CanonicalIntakeSummary;
}) {
  const command = useCanonicalIntakeCommand<CanonicalAssistedEntryCommand, CanonicalIntakeAssistedEntryRequestReceipt>(context,
    (packet, signal) => submitCanonicalAssistedEntry(context, packet, signal), (packet) => `收件 ${packet.intakeId} 的人工補錄`,
    (receipt) => `收件 ${receipt.intake_id} · 版本 ${receipt.version} · 補錄提案 ${receipt.request_id} · ${receipt.status}`,
    (packet, signal) => readCanonicalAssistedEntryReceipt(context, packet, signal));
  if (command.receipt) return <div role="status" className={styles.noteBox}>
    <p>補錄提案 {command.receipt.request_id} · 已受理 · {command.receipt.correction_ids.length} 個欄位</p>
    <p>稽核紀錄 {command.receipt.audit_event_id} · 查詢編號 {command.receipt.correlation_id}</p>
    <p>補錄內容尚待工作處理，請重新讀取收件；此回執不代表解析或比對已完成。</p>
  </div>;
  return <>
    <AssistedEntryForm busy={command.busy} canEdit native locked={command.hasPending || command.blocked}
      onSave={(input) => { void command.submit(() => {
        if (!input.reason) throw new Error("EXPLICIT_NATIVE_ENTRY_REASON_REQUIRED");
        return { key: `canonical-entry:${crypto.randomUUID()}`, intakeId: intake.intake_id, expectedVersion: intake.version,
          body: { reason: input.reason, risk_summary: input.riskSummary, risk_acknowledged: input.riskAcknowledged,
            fields: { ...input.fields, address: input.fields.address, rent: Number(input.fields.rent), areaPing: Number(input.fields.areaPing) } } };
      }); }} />
    {command.error ? <><ReadFailure error={command.error} />{command.hasPending ? <p>結果尚未確認；重試會沿用同一份補錄內容與版本。</p> : null}
      {command.hasPending ? <button className={styles.secondaryButton} type="button" disabled={command.busy}
        onClick={() => void command.submit(() => { throw new Error("CAPTURED_COMMAND_REQUIRED"); })}>重試同一補錄請求</button> : null}</> : null}
  </>;
}

/** The same workspace owner remains visible when a form or radar tab closes. */
export function CanonicalIntakeCommandStatus() {
  const owner = useCanonicalIntakeCommandState();
  const command = owner.command;
  if (!command) return null;
  return <section aria-label="先前收件操作" className={styles.noteBox}>
    <p>{command.description}</p>
    {command.receipt ? <>
      <p role="status">原操作已收到回執 · 稽核紀錄 {command.receipt.audit_event_id} · 查詢編號 {command.receipt.correlation_id}</p>
      <p>{command.receiptDescription}</p>
      <p>回執僅表示這次請求已受理；後續處理結果請重新讀取原收件或案件。</p>
      <button type="button" className={styles.secondaryButton} onClick={owner.dismissReceipt}>收起原操作回執</button>
    </> : <>
      <p role="status">{owner.probing ? "正在查詢原操作回執，請稍候。" : owner.busy ? "正在傳送原操作，請稍候。" : "原操作結果尚未確認。關閉或切換畫面不會取消已送出的請求；重試沿用原內容與版本。"}</p>
      {command.notObserved ? <p>這次查詢尚未觀察到原回執，仍不能確定請求是否已執行。原內容與請求識別保留，可稍後再查詢或重試原操作。</p> : null}
      {owner.error ? <p>上次嘗試未取得可驗證回執{owner.error.value instanceof CanonicalIntakeReadError ? `（HTTP ${owner.error.value.status}）` : ""}；請確認目前連線與登入權限，再重試原操作。</p> : null}
      <button type="button" className={styles.secondaryButton} disabled={owner.busy} onClick={() => void owner.retry()}>重試原操作</button>
      {command.probe ? <button type="button" className={styles.secondaryButton} disabled={owner.busy}
        onClick={() => void owner.probeReceipt()}>查詢原操作回執</button> : null}
    </>}
  </section>;
}
