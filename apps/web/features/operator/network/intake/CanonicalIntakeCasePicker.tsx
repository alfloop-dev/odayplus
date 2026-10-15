"use client";

import { useEffect, useMemo, useState } from "react";
import type { CanonicalIntakePage } from "@oday-plus/openapi-client";
import { IntakeDialogShell } from "./IntakeDialogShell";
import {
  CANONICAL_INTAKE_STAGE_LABEL, CanonicalIntakeReadError, readCanonicalIntakeCase, readCanonicalIntakePage,
  type CanonicalIntakeReadContext,
} from "./canonicalIntakeReadClient";
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
    <IntakeDialogShell ariaLabel="選取收件與比對案件" className={styles.panelWide} onClose={onClose}
      screenLabel="Dialog 選取正式收件與比對案件" testId="canonical-intake-case-picker">
      <header className={styles.dialogHead}>
        <h2 className={styles.dialogTitle}>選取收件與比對案件</h2>
        <button aria-label="關閉" className={styles.dialogClose} onClick={onClose} type="button">×</button>
      </header>
      <div className={styles.dialogBody}>
        <p>選取收件以查看目前的比對案件與候選主體。</p>
        {list.status === "loading" ? <p role="status">正在讀取收件…</p> : list.status === "error" ? <ReadFailure error={list.error} /> : (
          <>
            {list.value.items.length === 0 ? <p>目前可讀範圍沒有收件。</p> : (
              <ul aria-label="可選取的收件">
                {list.value.items.map((row) => <li key={row.intake_id}>
                  <button aria-pressed={row.intake_id === selectedId} className={styles.secondaryButton} onClick={() => select(row.intake_id)} type="button">
                    {row.intake_id} · {CANONICAL_INTAKE_STAGE_LABEL[row.processing_state]} · {row.intake_method} · {new Date(row.submitted_at).toLocaleString("zh-TW")}
                  </button>
                </li>)}
              </ul>
            )}
            <div className={styles.timelineActions}>
              <button className={styles.secondaryButton} disabled={cursor === null} onClick={() => changePage(null)} type="button">返回第一頁</button>
              <button className={styles.secondaryButton} disabled={!list.value.next_cursor} onClick={() => changePage(list.value.next_cursor)} type="button">下一頁</button>
            </div>
          </>
        )}
        {selectedId ? <section aria-label="所選收件與比對案件" className={styles.sectionBox}>
          {detail.status === "loading" ? <p role="status">正在讀取所選收件與案件…</p> : detail.status === "error" ? <ReadFailure error={detail.error} /> : (
            <>
              <p>收件 {detail.value.intake.intake_id} · 版本 {detail.value.intake.version} · {CANONICAL_INTAKE_STAGE_LABEL[detail.value.intake.processing_state]}</p>
              {detail.value.matchCase === null ? <p>這筆收件目前沒有比對案件。</p> : (
                <>
                  <p>比對案件 {detail.value.matchCase.match_case_id} · 版本 {detail.value.matchCase.version} · {detail.value.matchCase.status} · {detail.value.matchCase.outcome}</p>
                  <ul aria-label="案件候選主體">
                    {detail.value.matchCase.candidates.map((candidate) => <li key={candidate.match_candidate_id}>
                      主體 {candidate.property_id ?? "未提供"} · 物件 {candidate.listing_id ?? "未提供"} · 信心值 {candidate.confidence}
                      <p>觀察主體版本 {candidate.observed_property_version ?? "未提供"} · 觀察物件版本 {candidate.observed_listing_version ?? "未提供"}</p>
                    </li>)}
                  </ul>
                </>
              )}
            </>
          )}
        </section> : null}
      </div>
      <footer className={styles.dialogFooter}>
        <button className={styles.secondaryButton} onClick={() => { setList({ status: "loading" }); setSelectedId(null); setReload((n) => n + 1); }} type="button">重新讀取</button>
        <button className={styles.secondaryButton} onClick={onClose} type="button">關閉</button>
      </footer>
    </IntakeDialogShell>
  );
}

function ReadFailure({ error }: { error: unknown }) {
  let message = "無法完成讀取，請重新讀取後再試。";
  if (error instanceof CanonicalIntakeReadError) {
    if (error.status === 401) message = "登入狀態已失效，請重新登入。";
    else if (error.status === 403) message = "目前身分沒有讀取這些資料的權限。";
    else if (error.status === 404) message = "無法取得所選收件或案件，請重新讀取。";
    else if (error.status === 409) message = "資料版本已更新，請重新讀取後再選取。";
    else if (error.code === "CANONICAL_RESPONSE_INVALID") message = "回傳資料無法辨識，請重新讀取後再試。";
  }
  return <div role="alert" className={styles.errorPanel}><p>{message}</p>{error instanceof CanonicalIntakeReadError && error.correlationId ? <small>查詢編號 {error.correlationId}</small> : null}</div>;
}
