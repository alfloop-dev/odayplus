"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AssistedIntake, IntakeCorrectableField, IntakeFieldValue } from "@oday-plus/openapi-client";
import type { OperatorRoleId } from "../../navigation";
import { getOperatorRole } from "../../navigation";
import styles from "./intake.module.css";
import { ListingInboxIntakeView } from "./ListingInboxIntakeView";
import { IntakeDecisionDialog } from "./IntakeDecisionDialog";
import { IntakeDetailDialog } from "./IntakeDetailDialog";
import { IntakeFieldFixDialog } from "./IntakeFieldFixDialog";
import {
  buildIntakeClient,
  intakeApi,
  missingClientError,
  newIdempotencyKey,
  newIntakeActionIdempotencyKey,
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
  const [records, setRecords] = useState<AssistedIntake[]>([]);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [loadError, setLoadError] = useState<IntakeApiError | null>(null);
  const [dialog, setDialog] = useState<"add" | "detail" | "fix" | "decide" | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [fixFieldKey, setFixFieldKey] = useState<string | null>(null);
  const [decisionKind, setDecisionKind] = useState<IntakeDecisionKind | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<IntakeApiError | null>(null);
  const [toast, setToast] = useState<string | null>(null);

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
    const result = await intakeApi.list(client, selectedHeatZoneId);
    if (result.ok) {
      setRecords(result.value);
      setLoadState("ready");
      setLoadError(null);
    } else {
      setLoadState("error");
      setLoadError(result.error);
    }
  }, [activeRoleId, client, selectedHeatZoneId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Durable deep link: #intake/<id> opens the detail dialog on load, so an
  // operator can leave and come back to the record (design §4 requirement).
  useEffect(() => {
    function openFromHash() {
      const match = /^#intake\/(.+)$/.exec(window.location.hash);
      if (match) {
        setSelectedId(match[1]);
        setDialog("detail");
      }
    }
    openFromHash();
    window.addEventListener("hashchange", openFromHash);
    return () => window.removeEventListener("hashchange", openFromHash);
  }, []);

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
    setDialog(null);
    setActionError(null);
    submitKeyRef.current = null;
    correctionKeyRef.current = null;
    assistedEntryKeyRef.current = null;
    decisionKeyRef.current = null;
    if (window.location.hash.startsWith("#intake/")) {
      history.replaceState(null, "", window.location.pathname + window.location.search);
    }
  }

  function openDetail(intakeId: string) {
    setSelectedId(intakeId);
    setDialog("detail");
    setActionError(null);
    history.replaceState(null, "", `#intake/${intakeId}`);
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
    setSelectedId(record.id);
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
    setDialog("detail");
    history.replaceState(null, "", `#intake/${result.value.id}`);
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
    setDialog("detail");
    setFixFieldKey(null);
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
        action: DECISION_API_ACTION[decisionKind],
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
    setDialog("detail");
    setDecisionKind(null);
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
            setDecisionKind(kind);
            setActionError(null);
            setDialog("decide");
          }}
          onOpenFix={(fieldKey) => {
            correctionKeyRef.current = selected
              ? newIntakeActionIdempotencyKey(selected.id, "correct", fieldKey)
              : null;
            setFixFieldKey(fieldKey);
            setActionError(null);
            setDialog("fix");
          }}
          onRetry={handleRetry}
          record={selected}
        />
      ) : null}

      {dialog === "fix" && selected && fixField ? (
        <IntakeFieldFixDialog
          busy={busy}
          error={actionError}
          field={fixField}
          onClose={() => {
            setDialog("detail");
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
            setDialog("detail");
            setActionError(null);
            decisionKeyRef.current = null;
          }}
          onSubmit={handleDecide}
          record={selected}
        />
      ) : null}
    </>
  );
}
