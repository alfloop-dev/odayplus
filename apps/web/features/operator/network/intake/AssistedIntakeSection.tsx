"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import { parseUrlState, serializeUrlState } from "./urlState";
import type {
  AssistedIntake,
  AssignmentReceipt,
  IntakeCorrectableField,
  IntakeFieldValue,
  IntakeInboxPage,
  IntakeInboxQuery,
  SlaReceipt,
} from "@oday-plus/openapi-client";
import type { OperatorRoleId } from "../../navigation";
import { getOperatorRole } from "../../navigation";
import styles from "./intake.module.css";
import { ListingInboxIntakeView } from "./ListingInboxIntakeView";
import { IntakeDecisionDialog } from "./IntakeDecisionDialog";
import { IntakeDetailDialog } from "./IntakeDetailDialog";
import { IntakeFieldFixDialog } from "./IntakeFieldFixDialog";
import { TransferIntakeDialog } from "./TransferIntakeDialog";
import { PauseSlaDialog } from "./PauseSlaDialog";
import {
  buildIntakeClient,
  intakeApi,
  missingClientError,
  newIdempotencyKey,
  newIntakeActionIdempotencyKey,
  newCorrelationId,
  type IntakeApiError,
} from "./intakeClient";
import { canPerform, canView } from "./intakePermissions";
import { DECISION_API_ACTION, type IntakeDecisionKind } from "./intakeTypes";

// Container for the assisted listing intake slice (ODP-OC-R5-011).
//
// Owned layer  : intake queue state, dialog routing (incl. the #intake/<id>
//                deep link), and every write through the typed client.
// Not changing : the surrounding Listing Radar panel's own data flow.
//
// There is deliberately NO fixture fallback here. The other network panels
// fall back to bundled fixtures when the API is down, which is right for
// read-only analytics — but an intake queue is a record of real human
// submissions and real governance decisions. Showing synthetic rows in its
// place would present fabricated evidence, so an unreachable backend renders
// an explicit error state instead.

export function AssistedIntakeSection({
  activeRoleId,
  selectedHeatZoneId,
}: {
  activeRoleId: OperatorRoleId;
  selectedHeatZoneId?: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const urlState = useMemo(() => parseUrlState(searchParams), [searchParams]);

  const selectedId = urlState.selectedId;
  const dialog = urlState.dialog;
  const fixFieldKey = urlState.fixFieldKey;
  const decisionKind = urlState.decisionKind as any;
  const asgKind = urlState.decisionKind === "transfer" ? "transfer" : urlState.decisionKind === "pause" ? "pause" : null;

  const [records, setRecords] = useState<AssistedIntake[]>([]);
  const [pageData, setPageData] = useState<IntakeInboxPage | undefined>();
  const [inboxQuery, setInboxQuery] = useState<IntakeInboxQuery>({ page: 1, pageSize: 10, sortBy: "updatedAt", sortOrder: "desc" });
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [loadError, setLoadError] = useState<IntakeApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<IntakeApiError | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [assignmentReceipts, setAssignmentReceipts] = useState<Record<string, AssignmentReceipt>>({});
  const [slaReceipts, setSlaReceipts] = useState<Record<string, SlaReceipt>>({});

  const updateUrlState = useCallback((updates: Partial<typeof urlState>) => {
    const nextState = {
      filters: urlState.filters,
      sort: urlState.sort,
      view: urlState.view,
      selectedId: urlState.selectedId,
      dialog: urlState.dialog,
      activeSection: urlState.activeSection,
      fixFieldKey: urlState.fixFieldKey,
      decisionKind: urlState.decisionKind,
      receiptId: urlState.receiptId,
      compareTask: urlState.compareTask,
      ...updates,
    };
    const newParams = serializeUrlState(nextState, searchParams);
    router.replace(`${pathname}?${newParams.toString()}`);
  }, [urlState, searchParams, pathname, router]);

  const role = getOperatorRole(activeRoleId);
  const client = useMemo(() => buildIntakeClient(activeRoleId), [activeRoleId]);
  // Every submit attempt reuses one key so a network retry cannot double-create.
  const submitKeyRef = useRef<string | null>(null);
  const correctionKeyRef = useRef<string | null>(null);
  const assistedEntryKeyRef = useRef<string | null>(null);
  const decisionKeyRef = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    // A role without listing:VIEW would get a guaranteed 403. That is a
    // permission state, not a failure, so don't issue the request at all.
    if (!canView(activeRoleId)) return;
    if (!client) {
      setLoadState("error");
      setLoadError(missingClientError());
      return;
    }
    setLoadState("loading");
    const result = await intakeApi.list(client, { ...inboxQuery, selectedHeatZoneId });
    if (result.ok) {
      setRecords(result.value.items);
      setPageData(result.value);
      setLoadState("ready");
      setLoadError(null);
    } else {
      setLoadState("error");
      setLoadError(result.error);
    }
  }, [activeRoleId, client, inboxQuery, selectedHeatZoneId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Durable deep link: #intake/<id> opens the detail dialog on load, so an
  // operator can leave and come back to the record (design §4 requirement).
  useEffect(() => {
    function openFromHash() {
      const match = /^#intake\/(.+)$/.exec(window.location.hash);
      if (match) {
        updateUrlState({ dialog: "detail", selectedId: match[1] });
        window.history.replaceState(null, "", window.location.pathname + window.location.search);
      }
    }
    openFromHash();
    window.addEventListener("hashchange", openFromHash);
    return () => window.removeEventListener("hashchange", openFromHash);
  }, [updateUrlState]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    correctionKeyRef.current = null;
    assistedEntryKeyRef.current = null;
    decisionKeyRef.current = null;
  }, [selectedId]);

  const selected = records.find((record) => record.id === selectedId) ?? null;

  function closeDialog() {
    updateUrlState({ dialog: null, selectedId: null, fixFieldKey: null, decisionKind: null, receiptId: null });
    setActionError(null);
    submitKeyRef.current = null;
    correctionKeyRef.current = null;
    assistedEntryKeyRef.current = null;
    decisionKeyRef.current = null;
  }

  function openDetail(intakeId: string) {
    updateUrlState({ dialog: "detail", selectedId: intakeId });
    setActionError(null);
  }

  /** Merge a server response back into the queue; the server is authoritative. */
  function applyRecord(record: AssistedIntake) {
    setRecords((current) => {
      const index = current.findIndex((item) => item.id === record.id);
      if (index === -1) return [record, ...current];
      const next = [...current];
      next[index] = record;
      return next;
    });
    updateUrlState({ selectedId: record.id });
  }

  async function handleSubmit({ url, heatZoneId }: { url: string; heatZoneId: string }) {
    if (!client || busy) return;
    setBusy(true);
    setActionError(null);
    if (!submitKeyRef.current) submitKeyRef.current = newIdempotencyKey(url);

    const result = await intakeApi.submit(
      client,
      {
        url,
        heatZoneId: heatZoneId || null,
        actorRoleId: activeRoleId,
        actorName: role.label,
      },
      { idempotencyKey: submitKeyRef.current },
    );
    setBusy(false);

    if (!result.ok) {
      setActionError(result.error);
      return;
    }
    submitKeyRef.current = null;
    applyRecord(result.value);
    updateUrlState({ dialog: "detail", selectedId: result.value.id });
    setToast(
      result.value.matchResult?.outcome === "EXACT_DUPLICATE"
        ? `已於識別檢查攔截 — 此 URL 已存在（${result.value.matchResult.targetListingId ?? result.value.id}），未執行擷取`
        : `收件 ${result.value.id} 已建立 — ${result.value.policyLabel}`,
    );
    void refresh();
  }

  async function handleFix({
    value,
    reason,
    riskSummary,
    riskAcknowledged,
  }: {
    value: string;
    reason: string;
    riskSummary: string;
    riskAcknowledged: boolean;
  }) {
    if (!client || !selected || !fixFieldKey || busy) return;
    setBusy(true);
    setActionError(null);
    if (!correctionKeyRef.current) {
      correctionKeyRef.current = newIntakeActionIdempotencyKey(selected.id, "correct", fixFieldKey);
    }
    const result = await intakeApi.correct(
      client,
      selected.id,
      {
        fields: { [fixFieldKey as IntakeCorrectableField]: value as IntakeFieldValue },
        reason: reason || null,
        riskSummary,
        riskAcknowledged,
        actorRoleId: activeRoleId,
        actorName: role.label,
      },
      { idempotencyKey: correctionKeyRef.current },
    );
    setBusy(false);

    if (!result.ok) {
      setActionError(result.error);
      return;
    }
    correctionKeyRef.current = null;
    applyRecord(result.value);
    updateUrlState({ dialog: "detail", fixFieldKey: null });
    setToast("已記錄人工修正（前後值已寫入 Audit）");
  }

  async function handleAssistedEntry({
    fields,
    riskSummary,
    riskAcknowledged,
  }: {
    fields: Record<string, string>;
    riskSummary: string;
    riskAcknowledged: boolean;
  }) {
    if (!client || !selected || busy) return;
    setBusy(true);
    setActionError(null);
    if (!assistedEntryKeyRef.current) {
      assistedEntryKeyRef.current = newIntakeActionIdempotencyKey(selected.id, "assisted-entry");
    }
    const result = await intakeApi.correct(
      client,
      selected.id,
      {
        fields: fields as Partial<Record<IntakeCorrectableField, IntakeFieldValue>>,
        reason: "人工補錄：此來源未經核准擷取，依來源頁內容補錄必要欄位",
        riskSummary,
        riskAcknowledged,
        actorRoleId: activeRoleId,
        actorName: role.label,
      },
      { idempotencyKey: assistedEntryKeyRef.current },
    );
    setBusy(false);

    if (!result.ok) {
      setActionError(result.error);
      return;
    }
    assistedEntryKeyRef.current = null;
    applyRecord(result.value);
    setToast("補錄完成 — 已進入比對");
    void refresh();
  }

  async function handleDecide({
    reason,
    riskSummary,
    riskAcknowledged,
  }: {
    reason: string;
    riskSummary: string;
    riskAcknowledged: boolean;
  }) {
    if (!client || !selected || !decisionKind || busy) return;
    setBusy(true);
    setActionError(null);
    if (!decisionKeyRef.current) {
      decisionKeyRef.current = newIntakeActionIdempotencyKey(selected.id, `decide-${decisionKind}`);
    }
    const result = await intakeApi.decide(
      client,
      selected.id,
      {
        action: DECISION_API_ACTION[decisionKind as IntakeDecisionKind],
        reason,
        riskSummary,
        riskAcknowledged,
        actorRoleId: activeRoleId,
        actorName: role.label,
      },
      { idempotencyKey: decisionKeyRef.current },
    );
    setBusy(false);

    if (!result.ok) {
      // Keep the dialog open and the reason typed — no optimistic close.
      setActionError(result.error);
      return;
    }
    decisionKeyRef.current = null;
    applyRecord(result.value);
    updateUrlState({ dialog: "detail", decisionKind: null });
    setToast(`決策已寫入 — ${result.value.stage} · 已記錄於 Audit Trail`);
    void refresh();
  }

  async function handleRetry() {
    if (!client || !selected || busy) return;
    setBusy(true);
    setActionError(null);
    const result = await intakeApi.retry(client, selected.id, activeRoleId);
    setBusy(false);

    if (!result.ok) {
      setActionError(result.error);
      return;
    }
    applyRecord(result.value);
    setToast("已重試擷取 — 先前送件內容與人工修正已保留");
    void refresh();
  }

  async function handleInboxRetry(intakeId: string) {
    if (!client || busy) return;
    setBusy(true);
    setActionError(null);
    const result = await intakeApi.retry(client, intakeId, activeRoleId);
    setBusy(false);
    if (!result.ok) {
      setActionError(result.error);
      return;
    }
    applyRecord(result.value);
    setToast(`收件 ${intakeId} 已直接重試；原始證據與修正紀錄保留`);
    void refresh();
  }

  async function handleClaim() {
    if (!client || !selected || busy) return;
    setBusy(true);
    setActionError(null);

    const key = newIntakeActionIdempotencyKey(selected.id, "claim-asg");
    const correlationId = newCorrelationId();

    try {
      let receipt;
      if (selected.assignmentId) {
        receipt = await client.claimAssignment(
          selected.assignmentId,
          { reason: "Claiming existing assignment" },
          { idempotencyKey: key, correlationId }
        );
      } else {
        receipt = await client.assignIntake(
          selected.id,
          {
            owner_subject_id: activeRoleId,
            owner_role: "reviewer",
            reason: "Claiming assignment for manual triage review",
            due_at: new Date(Date.now() + 5 * 24 * 60 * 60 * 1000).toISOString(),
          },
          { idempotencyKey: key, correlationId }
        );
      }

      setToast(`已成功認領收件！指派 ID: ${receipt.assignment_id}`);
      if (receipt) {
        setAssignmentReceipts((prev) => ({ ...prev, [selected.id]: receipt }));
      }

      const getResult = await intakeApi.get(client, selected.id);
      if (getResult.ok) {
        applyRecord(getResult.value);
      }
      void refresh();
    } catch (err: any) {
      setActionError({
        code: err.code || "CLAIM_ERROR",
        summary: err.message || "認領收件時發生錯誤",
        occurredAt: new Date().toISOString(),
        nextAction: "請稍後再試",
        status: err.status,
        retryable: false,
        correlationId: err.correlationId,
      });
    } finally {
      setBusy(false);
    }
  }

  async function handleTransferSubmit(payload: {
    target_owner_subject_id: string;
    target_owner_role: string;
    handoff_note: string;
  }) {
    if (!client || !selected || busy) return;
    setBusy(true);
    setActionError(null);

    const asgId = selected.assignmentId;
    if (!asgId) {
      setActionError({
        code: "NO_ASSIGNMENT",
        summary: "無法轉交：找不到此收件的指派記錄",
        occurredAt: new Date().toISOString(),
        nextAction: "請先認領或指派收件",
        retryable: false,
        correlationId: null,
        status: 400,
      });
      setBusy(false);
      return;
    }

    const key = newIntakeActionIdempotencyKey(selected.id, "transfer-asg");
    const correlationId = newCorrelationId();

    try {
      const receipt = await client.transferAssignment(
        asgId,
        {
          target_owner_subject_id: payload.target_owner_subject_id,
          target_owner_role: payload.target_owner_role,
          handoff_note: payload.handoff_note,
          reason: payload.handoff_note,
        },
        {
          idempotencyKey: key,
          correlationId,
          ifMatch: `W/"${selected.version}"`,
        }
      );

      setToast(`已成功轉交收件！`);
      if (receipt) {
        setAssignmentReceipts((prev) => ({ ...prev, [selected.id]: receipt }));
      }

      const getResult = await intakeApi.get(client, selected.id);
      if (getResult.ok) {
        applyRecord(getResult.value);
      }
      updateUrlState({ dialog: "detail" });
      void refresh();
    } catch (err: any) {
      setActionError({
        code: err.code || "TRANSFER_ERROR",
        summary: err.message || "轉交收件時發生錯誤",
        occurredAt: new Date().toISOString(),
        nextAction: "請檢查對象角色或狀態",
        status: err.status,
        retryable: false,
        correlationId: err.correlationId,
      });
    } finally {
      setBusy(false);
    }
  }

  async function handlePauseSubmit(payload: {
    expected_resume_at: string;
    reason: string;
  }) {
    if (!client || !selected || busy) return;
    setBusy(true);
    setActionError(null);

    const slaId = selected.slaInstanceId;
    if (!slaId) {
      setActionError({
        code: "NO_SLA_INSTANCE",
        summary: "無法暫停 SLA：找不到此收件的 SLA 實例",
        occurredAt: new Date().toISOString(),
        nextAction: "請重新整理收件",
        retryable: false,
        correlationId: null,
        status: 400,
      });
      setBusy(false);
      return;
    }

    const key = newIntakeActionIdempotencyKey(selected.id, "pause-sla");
    const correlationId = newCorrelationId();

    try {
      const receipt = await client.pauseSla(
        slaId,
        {
          reason: payload.reason,
          expected_resume_at: payload.expected_resume_at,
        },
        {
          idempotencyKey: key,
          correlationId,
          ifMatch: `W/"${selected.version}"`,
        }
      );

      setToast(`SLA 已暫停！`);
      if (receipt) {
        setSlaReceipts((prev) => ({ ...prev, [selected.id]: receipt }));
      }

      const getResult = await intakeApi.get(client, selected.id);
      if (getResult.ok) {
        applyRecord(getResult.value);
      }
      updateUrlState({ dialog: "detail" });
      void refresh();
    } catch (err: any) {
      setActionError({
        code: err.code || "PAUSE_ERROR",
        summary: err.message || "暫停 SLA 時發生錯誤",
        occurredAt: new Date().toISOString(),
        nextAction: "請稍後再試",
        status: err.status,
        retryable: false,
        correlationId: err.correlationId,
      });
    } finally {
      setBusy(false);
    }
  }

  async function handleResumeSla() {
    if (!client || !selected || busy) return;
    setBusy(true);
    setActionError(null);

    const slaId = selected.slaInstanceId;
    if (!slaId) {
      setActionError({
        code: "NO_SLA_INSTANCE",
        summary: "無法恢復 SLA：找不到此收件的 SLA 實例",
        occurredAt: new Date().toISOString(),
        nextAction: "請重新整理收件",
        retryable: false,
        correlationId: null,
        status: 400,
      });
      setBusy(false);
      return;
    }

    const key = newIntakeActionIdempotencyKey(selected.id, "resume-sla");
    const correlationId = newCorrelationId();

    try {
      const receipt = await client.resumeSla(
        slaId,
        { reason: "Manual resume SLA" },
        {
          idempotencyKey: key,
          correlationId,
          ifMatch: `W/"${selected.version}"`,
        }
      );

      setToast(`SLA 已恢復計時！`);
      if (receipt) {
        setSlaReceipts((prev) => ({ ...prev, [selected.id]: receipt }));
      }

      const getResult = await intakeApi.get(client, selected.id);
      if (getResult.ok) {
        applyRecord(getResult.value);
      }
      void refresh();
    } catch (err: any) {
      setActionError({
        code: err.code || "RESUME_ERROR",
        summary: err.message || "恢復 SLA 時發生錯誤",
        occurredAt: new Date().toISOString(),
        nextAction: "請稍後再試",
        status: err.status,
        retryable: false,
        correlationId: err.correlationId,
      });
    } finally {
      setBusy(false);
    }
  }

  async function handleConflictRefresh() {
    if (!client || !selected) return;
    setActionError(null);
    const getResult = await intakeApi.get(client, selected.id);
    if (getResult.ok) {
      applyRecord(getResult.value);
    }
  }

  const fixField = selected && fixFieldKey ? selected.parsedFields?.[fixFieldKey] : undefined;

  return (
    <>
      <ListingInboxIntakeView
        activeRoleId={activeRoleId}
        actionError={actionError}
        busy={busy}
        loadError={loadError}
        loadState={loadState}
        onAddSubmit={handleSubmit}
        onOpenDetail={openDetail}
        onRetryLoad={() => void refresh()}
        onRetryIntake={(intakeId) => void handleInboxRetry(intakeId)}
        onQueryChange={setInboxQuery}
        pageData={pageData}
        records={records}
        selectedHeatZoneId={selectedHeatZoneId}
      />

      {toast ? (
        <div className={styles.noteBox} data-testid="intake-toast" role="status">
          {toast}
        </div>
      ) : null}

      {dialog === "detail" && selected ? (
        <IntakeDetailDialog
          busy={busy}
          canCorrect={canPerform("correct", activeRoleId)}
          canDecide={canPerform("decide", activeRoleId)}
          canRetry={canPerform("retry", activeRoleId)}
          error={actionError}
          onAssistedEntrySave={handleAssistedEntry}
          onClose={closeDialog}
          onDecide={(kind) => {
            decisionKeyRef.current = selected
              ? newIntakeActionIdempotencyKey(selected.id, `decide-${kind}`)
              : null;
            setActionError(null);
            updateUrlState({ dialog: "decide", decisionKind: kind });
          }}
          onOpenFix={(fieldKey) => {
            correctionKeyRef.current = selected
              ? newIntakeActionIdempotencyKey(selected.id, "correct", fieldKey)
              : null;
            setActionError(null);
            updateUrlState({ dialog: "fix", fixFieldKey: fieldKey });
          }}
          onRetry={handleRetry}
          record={selected}
          assignmentReceipt={assignmentReceipts[selected.id]}
          slaReceipt={slaReceipts[selected.id]}
          onClaimAssignment={handleClaim}
          onOpenTransfer={() => {
            setActionError(null);
            updateUrlState({ dialog: "assignmentSla", decisionKind: "transfer" });
          }}
          onOpenPause={() => {
            setActionError(null);
            updateUrlState({ dialog: "assignmentSla", decisionKind: "pause" });
          }}
          onResumeSla={handleResumeSla}
        />
      ) : null}

      {dialog === "fix" && selected && fixField ? (
        <IntakeFieldFixDialog
          busy={busy}
          error={actionError}
          field={fixField}
          onClose={() => {
            updateUrlState({ dialog: "detail", fixFieldKey: null });
            setActionError(null);
            correctionKeyRef.current = null;
          }}
          onSubmit={handleFix}
        />
      ) : null}

      {dialog === "decide" && selected && decisionKind ? (
        <IntakeDecisionDialog
          busy={busy}
          error={actionError}
          kind={decisionKind}
          onClose={() => {
            updateUrlState({ dialog: "detail", decisionKind: null });
            setActionError(null);
            decisionKeyRef.current = null;
          }}
          onSubmit={handleDecide}
          record={selected}
        />
      ) : null}

      {dialog === "assignmentSla" && selected && asgKind === "transfer" ? (
        <TransferIntakeDialog
          busy={busy}
          error={actionError}
          onClose={() => {
            updateUrlState({ dialog: "detail", decisionKind: null });
            setActionError(null);
          }}
          onSubmit={handleTransferSubmit}
          record={selected}
          onConflictRefresh={handleConflictRefresh}
        />
      ) : null}

      {dialog === "assignmentSla" && selected && asgKind === "pause" ? (
        <PauseSlaDialog
          busy={busy}
          error={actionError}
          onClose={() => {
            updateUrlState({ dialog: "detail", decisionKind: null });
            setActionError(null);
          }}
          onSubmit={handlePauseSubmit}
          record={selected}
          onConflictRefresh={handleConflictRefresh}
        />
      ) : null}
    </>
  );
}
