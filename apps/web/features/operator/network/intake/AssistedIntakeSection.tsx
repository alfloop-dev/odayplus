"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import {
  intakeDetailHref,
  intakeInboxHref,
  normalizeIntakeDetailSection,
  parseUrlState,
  serializeUrlState,
} from "./urlState";
import type {
  AssistedIntake,
  AssignmentReceipt,
  IntakeCorrectableField,
  IntakeFieldValue,
  IntakeInboxPage,
  IntakeInboxQuery,
  JobReceipt,
  PromotionDecisionReceipt,
  SlaReceipt,
} from "@oday-plus/openapi-client";
import type { OperatorRoleId } from "../../navigation";
import { getOperatorRole } from "../../navigation";
import styles from "./intake.module.css";
import { ListingInboxIntakeView } from "./ListingInboxIntakeView";
import { IntakeDecisionDialog } from "./IntakeDecisionDialog";
import { IntakeDetailDialog } from "./IntakeDetailDialog";
import { IntakeDialogDismissBoundary } from "./IntakeDialogShell";
import { IntakeFieldFixDialog } from "./IntakeFieldFixDialog";
import {
  IntakeProcessingDetail,
  type IntakeDetailTab,
} from "./IntakeProcessingDetail";
import type {
  PromotionRequestInput,
  PromotionReviewInput,
} from "./PromotionReviewPanel";
import type { ScoreReplayInput } from "./SiteScoreJobStatus";
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
import {
  evaluateIntakePermission,
  type IntakePermissionAction,
  type IntakePermissionContext,
} from "./intakePermissions";
import { operatorSubjectId } from "../../operatorSecurityHeaders";
import { DECISION_API_ACTION, type IntakeDecisionKind } from "./intakeTypes";
import type { IntakeOperatorSession } from "./intakeOperatorSession";

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

export function AssistedIntakeDetailPage({
  operatorSession,
  activeRoleId,
  activeSubjectId,
  intakeId,
}: {
  operatorSession?: IntakeOperatorSession;
  activeRoleId?: OperatorRoleId;
  activeSubjectId?: string;
  intakeId: string;
}) {
  return (
    <AssistedIntakeSection
      operatorSession={operatorSession}
      activeRoleId={activeRoleId}
      activeSubjectId={activeSubjectId}
      detailIntakeId={intakeId}
    />
  );
}

export function AssistedIntakeSection({
  operatorSession,
  activeRoleId,
  activeSubjectId,
  detailIntakeId,
  selectedHeatZoneId,
}: {
  operatorSession?: IntakeOperatorSession;
  activeRoleId?: OperatorRoleId;
  activeSubjectId?: string;
  detailIntakeId?: string;
  selectedHeatZoneId?: string;
}) {
  if (operatorSession && operatorSession.status !== "ready") {
    const reason =
      operatorSession.denialReasonCode ?? "AUTHORIZATION_CONTEXT_UNAVAILABLE";
    return (
      <section
        aria-label="Assisted Listing Intake 權限狀態"
        className={styles.queue}
        data-testid="intake-authoritative-session-denied"
      >
        <div className={styles.warnNote} role="status">
          無法取得可驗證的操作權限，收件功能已切換為唯讀並停止載入。
          <br />
          後端拒絕代碼：<code>{reason}</code>
          {operatorSession.maskingReasonCode ? (
            <>
              <br />
              欄位遮罩代碼：<code>{operatorSession.maskingReasonCode}</code>
            </>
          ) : null}
        </div>
      </section>
    );
  }

  const resolvedRoleId = operatorSession?.roleId ?? activeRoleId ?? null;
  const resolvedSubjectId = operatorSession?.subjectId ?? activeSubjectId ?? null;
  if (!resolvedRoleId || !resolvedSubjectId) {
    return (
      <section
        aria-label="Assisted Listing Intake 權限狀態"
        className={styles.queue}
        data-testid="intake-authoritative-session-unavailable"
      >
        <div className={styles.warnNote} role="status">
          尚未載入 authoritative operator session；所有 Assisted Listing Intake
          寫入動作均已停用。
          <br />
          後端拒絕代碼：<code>AUTHORIZATION_CONTEXT_UNAVAILABLE</code>
        </div>
      </section>
    );
  }

  return (
    <AuthorizedAssistedIntakeSection
      activeRoleId={resolvedRoleId}
      activeSubjectId={resolvedSubjectId}
      detailIntakeId={detailIntakeId}
      operatorSession={operatorSession}
      selectedHeatZoneId={selectedHeatZoneId}
    />
  );
}

function AuthorizedAssistedIntakeSection({
  activeRoleId,
  activeSubjectId,
  detailIntakeId,
  operatorSession,
  selectedHeatZoneId,
}: {
  activeRoleId: OperatorRoleId;
  activeSubjectId: string;
  detailIntakeId?: string;
  operatorSession?: IntakeOperatorSession;
  selectedHeatZoneId?: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const urlState = useMemo(() => parseUrlState(searchParams), [searchParams]);

  const isDurableDetailPage = Boolean(detailIntakeId);
  const selectedId = detailIntakeId ?? urlState.selectedId;
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

  // ---- Candidate promotion saga (ODP-INTAKE-UX-PROMOTION-001) -------------
  // Receipts are keyed by intake id and only ever set from server responses:
  // the saga's state lives on the server, never optimistically here.
  const [promotionReceipts, setPromotionReceipts] = useState<Record<string, PromotionDecisionReceipt>>({});
  const [scoreJobs, setScoreJobs] = useState<Record<string, JobReceipt>>({});
  const [promotionBusy, setPromotionBusy] = useState(false);
  const [promotionError, setPromotionError] = useState<IntakeApiError | null>(null);
  const [promotionReplayed, setPromotionReplayed] = useState(false);
  const [gateSnapshots, setGateSnapshots] = useState<Record<string, string>>({});
  const [promotionHydration, setPromotionHydration] = useState<{
    intakeId: string | null;
    state: "idle" | "loading" | "ready";
  }>({ intakeId: null, state: "idle" });

  const updateUrlState = useCallback((
    updates: Partial<typeof urlState>,
    historyMode: "replace" | "push" = "replace",
  ) => {
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
    const query = newParams.toString();
    const destination = `${pathname}${query ? `?${query}` : ""}`;
    if (historyMode === "push") router.push(destination);
    else router.replace(destination);
  }, [urlState, searchParams, pathname, router]);

  const role = getOperatorRole(activeRoleId);
  const client = useMemo(
    () =>
      buildIntakeClient(activeRoleId, activeSubjectId, {
        authoritative: Boolean(operatorSession),
        tenantId: operatorSession?.tenantId,
        systemRoles: operatorSession?.systemRoles,
      }),
    [
      activeRoleId,
      activeSubjectId,
      operatorSession,
    ],
  );
  const subjectId = operatorSubjectId(activeRoleId, activeSubjectId);
  const rootViewPermissionContext = useMemo<IntakePermissionContext>(
    () => ({
      resourceInScope: operatorSession
        ? operatorSession.scope?.resourceInScope
        : true,
      serverAllowed: operatorSession
        ? operatorSession.allowedActions.includes("view")
        : undefined,
      serverReasonCode: operatorSession
        ? (
            operatorSession.denialReasonByAction.view ??
            operatorSession.denialReasonCode ??
            (operatorSession.allowedActions.includes("view")
              ? null
              : "ROLE_DENIED")
          ) as IntakePermissionContext["serverReasonCode"]
        : undefined,
    }),
    [operatorSession],
  );

  const permissionContextFor = useCallback(
    (
      record: AssistedIntake | null | undefined,
      action: IntakePermissionAction,
      overrides: Partial<IntakePermissionContext> = {},
    ): IntakePermissionContext => {
      const authoritative = Boolean(operatorSession);
      const recordMasked = Object.values(record?.parsedFields ?? {}).some(
        (field) => field.masked === true,
      );
      const sourceIds = operatorSession?.scope?.sourceIds ?? [];
      const sourceInScope = record
        ? sourceIds.length > 0
          ? sourceIds.includes(record.sourceId)
          : operatorSession?.scope?.ownershipMode !== "SOURCE_DATA"
            ? operatorSession?.scope?.resourceInScope
            : undefined
        : operatorSession?.scope?.resourceInScope;

      return {
        resourceInScope: authoritative
          ? operatorSession?.scope?.resourceInScope
          : true,
        isOwner: record
          ? record.owner === subjectId || record.submitter === subjectId
          : authoritative
            ? undefined
            : true,
        isAssigned: record
          ? assignmentReceipts[record.id]?.owner_subject_id === subjectId
          : authoritative
            ? undefined
            : true,
        sourceInScope: authoritative ? sourceInScope : true,
        purposeDeclared: authoritative
          ? operatorSession?.purposeDeclared
          : true,
        fieldMasked: recordMasked || Boolean(operatorSession?.maskingReasonCode),
        fieldClassification: record ? "INTERNAL" : undefined,
        workflowState: record?.stage ?? null,
        proposerSubjectId:
          record && promotionReceipts[record.id]
            ? promotionReceipts[record.id].proposer_subject_id
            : record?.submitter,
        reviewerSubjectId: subjectId,
        serverAllowed: authoritative
          ? operatorSession?.allowedActions.includes(action)
          : undefined,
        serverReasonCode: authoritative
          ? (
              operatorSession?.denialReasonByAction[action] ??
              operatorSession?.denialReasonCode ??
              (operatorSession?.allowedActions.includes(action)
                ? null
                : "ROLE_DENIED")
            ) as IntakePermissionContext["serverReasonCode"]
          : undefined,
        maskingReasonCode: operatorSession?.maskingReasonCode,
        ...overrides,
      };
    },
    [
      activeRoleId,
      assignmentReceipts,
      operatorSession,
      promotionReceipts,
      subjectId,
    ],
  );
  // Every submit attempt reuses one key so a network retry cannot double-create.
  const submitKeyRef = useRef<string | null>(null);
  const correctionKeyRef = useRef<string | null>(null);
  const assistedEntryKeyRef = useRef<string | null>(null);
  const decisionKeyRef = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    // A role without listing:VIEW would get a guaranteed 403. That is a
    // permission state, not a failure, so don't issue the request at all.
    if (
      !evaluateIntakePermission(
        "view",
        activeRoleId,
        rootViewPermissionContext,
      ).allowed
    ) {
      return;
    }
    if (!client) {
      setLoadState("error");
      setLoadError(missingClientError());
      return;
    }
    setLoadState("loading");
    const result = detailIntakeId
      ? await intakeApi.get(client, detailIntakeId)
      : await intakeApi.list(client, { ...inboxQuery, selectedHeatZoneId });
    if (result.ok) {
      if (detailIntakeId) {
        setRecords([result.value as AssistedIntake]);
        setPageData(undefined);
      } else {
        const page = result.value as IntakeInboxPage;
        setRecords(page.items);
        setPageData(page);
      }
      setLoadState("ready");
      setLoadError(null);
    } else {
      setLoadState("error");
      setLoadError(result.error);
    }
  }, [
    activeRoleId,
    client,
    detailIntakeId,
    inboxQuery,
    rootViewPermissionContext,
    selectedHeatZoneId,
  ]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Legacy hash compatibility remains on the Inbox. The canonical durable
  // deep link is the App Router page and does not use this dialog state.
  useEffect(() => {
    if (isDurableDetailPage) return undefined;
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
  }, [isDurableDetailPage, updateUrlState]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    correctionKeyRef.current = null;
    assistedEntryKeyRef.current = null;
    decisionKeyRef.current = null;
    setPromotionError(null);
    setPromotionReplayed(false);
  }, [selectedId]);

  const selected = records.find((record) => record.id === selectedId) ?? null;

  // Restore the durable promotion saga whenever a deep link or reloaded inbox
  // opens an intake. Until this lookup finishes the request form stays closed,
  // preventing a stale page from offering a second promotion request.
  useEffect(() => {
    if (!client || !selected?.id) {
      setPromotionHydration({ intakeId: null, state: "idle" });
      return undefined;
    }
    const intakeId = selected.id;
    let cancelled = false;
    setPromotionHydration({ intakeId, state: "loading" });

    void (async () => {
      const result = await intakeApi.getPromotionForIntake(client, intakeId);
      if (cancelled) return;
      if (result.ok) {
        setPromotionReceipts((current) => ({ ...current, [intakeId]: result.value }));
        if (result.value.site_score_job_id) {
          const jobResult = await intakeApi.getScoreJob(client, result.value.site_score_job_id);
          if (cancelled) return;
          if (jobResult.ok) {
            setScoreJobs((current) => ({ ...current, [intakeId]: jobResult.value }));
          } else {
            setPromotionError(jobResult.error);
          }
        }
      } else if (result.error.status !== 404) {
        setPromotionError(result.error);
      }
      if (!cancelled) setPromotionHydration({ intakeId, state: "ready" });
    })();

    return () => {
      cancelled = true;
    };
  }, [client, selected?.id]);

  // Bind the request to the exact version and gate facts displayed by this UI.
  // The backend retains this digest for lineage; it remains responsible for
  // re-validating the authoritative promotion gates before execution.
  const gateKey = selected ? `${selected.id}:v${selected.version}` : null;
  useEffect(() => {
    if (!selected || selected.stage !== "READY" || !gateKey) return undefined;
    if (gateSnapshots[gateKey]) return undefined;
    let cancelled = false;
    const canonical = JSON.stringify({
      intakeId: selected.id,
      version: selected.version,
      stage: selected.stage,
      policy: selected.policy,
      matchOutcome: selected.matchResult?.outcome ?? null,
    });
    void crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical)).then((digest) => {
      if (cancelled) return;
      const hex = Array.from(new Uint8Array(digest))
        .map((byte) => byte.toString(16).padStart(2, "0"))
        .join("");
      setGateSnapshots((prev) => ({ ...prev, [gateKey]: hex }));
    });
    return () => {
      cancelled = true;
    };
  }, [selected, gateKey, gateSnapshots]);

  function closeDialog() {
    updateUrlState({
      dialog: null,
      selectedId: isDurableDetailPage ? urlState.selectedId : null,
      fixFieldKey: null,
      decisionKind: null,
      receiptId: null,
    });
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

  function openFullDetail(intakeId: string) {
    router.push(intakeDetailHref(intakeId, searchParams));
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
    if (!isDurableDetailPage) updateUrlState({ selectedId: record.id });
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
    setToast(
      result.value.matchResult?.outcome === "EXACT_DUPLICATE"
        ? `已於識別檢查攔截 — 此 URL 已存在（${result.value.matchResult.targetListingId ?? result.value.id}），未執行擷取`
        : `收件 ${result.value.id} 已建立 — ${result.value.policyLabel}`,
    );
    router.push(intakeDetailHref(result.value.id, searchParams));
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
    updateUrlState({ dialog: isDurableDetailPage ? null : "detail", fixFieldKey: null });
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
    updateUrlState({ dialog: isDurableDetailPage ? null : "detail", decisionKind: null });
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
      updateUrlState({ dialog: isDurableDetailPage ? null : "detail" });
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
      updateUrlState({ dialog: isDurableDetailPage ? null : "detail" });
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

  // ---- Promotion saga handlers (all four v1 calls go through intakeApi) ---

  async function handleRequestPromotion(input: PromotionRequestInput) {
    if (!client || !selected || promotionBusy) return;
    setPromotionBusy(true);
    setPromotionError(null);
    const result = await intakeApi.requestPromotion(
      client,
      selected.id,
      {
        target_format_code: input.targetFormatCode,
        reason: input.reason,
        gate_snapshot_sha256: input.gateSnapshotSha256,
        risk_acknowledged: input.riskAcknowledged,
        requested_reviewer_id: input.requestedReviewerId ?? null,
      },
      { idempotencyKey: input.idempotencyKey, ifMatch: input.ifMatch },
    );
    setPromotionBusy(false);
    if (!result.ok) {
      setPromotionError(result.error);
      return;
    }
    setPromotionReplayed(result.value.idempotencyReplayed);
    setPromotionReceipts((prev) => ({ ...prev, [selected.id]: result.value.receipt }));
    setToast(
      result.value.idempotencyReplayed
        ? "伺服器以原持久化收據回應（Idempotency-Replayed）— 未重複建立申請"
        : `晉升申請已送出（${result.value.receipt.status}）— 等待第二人審查`,
    );
    // The request consumed the intake's If-Match; refetch for the new version.
    const getResult = await intakeApi.get(client, selected.id);
    if (getResult.ok) applyRecord(getResult.value);
  }

  async function handleReviewPromotion(input: PromotionReviewInput) {
    if (!client || !selected || promotionBusy) return;
    const current = promotionReceipts[selected.id];
    if (!current) return;
    setPromotionBusy(true);
    setPromotionError(null);
    const result = await intakeApi.reviewPromotion(
      client,
      current.promotion_decision_id,
      {
        decision: input.decision,
        reason: input.reason,
        risk_acknowledged: input.riskAcknowledged,
        requested_changes: input.requestedChanges,
      },
      { idempotencyKey: input.idempotencyKey, ifMatch: input.ifMatch },
    );
    setPromotionBusy(false);
    if (!result.ok) {
      setPromotionError(result.error);
      return;
    }
    setPromotionReplayed(result.value.idempotencyReplayed);
    setPromotionReceipts((prev) => ({ ...prev, [selected.id]: result.value.receipt }));
    if (result.value.receipt.site_score_job_id) {
      const jobResult = await intakeApi.getScoreJob(client, result.value.receipt.site_score_job_id);
      if (jobResult.ok) {
        setScoreJobs((prev) => ({ ...prev, [selected.id]: jobResult.value }));
      } else {
        setPromotionError(jobResult.error);
      }
    }
    setToast(`審查已寫入 — 決策狀態 ${result.value.receipt.status}`);
  }

  async function handleLookupPromotionDecision() {
    if (!client || !selected) return;
    const current = promotionReceipts[selected.id];
    if (!current) return;
    setPromotionBusy(true);
    const result = await intakeApi.getPromotionDecision(client, current.promotion_decision_id);
    setPromotionBusy(false);
    if (!result.ok) {
      setPromotionError(result.error);
      return;
    }
    // A successful lookup resolves the lost-response state without resending.
    setPromotionError(null);
    setPromotionReplayed(false);
    setPromotionReceipts((prev) => ({ ...prev, [selected.id]: result.value }));
    if (result.value.site_score_job_id) {
      const jobResult = await intakeApi.getScoreJob(client, result.value.site_score_job_id);
      if (jobResult.ok) {
        setScoreJobs((prev) => ({ ...prev, [selected.id]: jobResult.value }));
      } else {
        setPromotionError(jobResult.error);
      }
    }
    setToast(`已查得決策狀態 ${result.value.status}（未重送任何寫入）`);
  }

  async function handleReplayScore(input: ScoreReplayInput) {
    if (!client || !selected || promotionBusy) return;
    setPromotionBusy(true);
    setPromotionError(null);
    const result = await intakeApi.retryScoreJob(
      client,
      input.jobId,
      {
        checkpoint: input.checkpoint,
        reason: input.reason,
        risk_acknowledged: input.riskAcknowledged,
      },
      { idempotencyKey: input.idempotencyKey, ifMatch: input.ifMatch },
    );
    setPromotionBusy(false);
    if (!result.ok) {
      setPromotionError(result.error);
      return;
    }
    setPromotionReplayed(result.value.idempotencyReplayed);
    setScoreJobs((prev) => ({ ...prev, [selected.id]: result.value.receipt }));
    setToast(`評分工作已重新排入（attempt ${result.value.receipt.attempt}）`);
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

  const selectedPromotion = selected ? promotionReceipts[selected.id] ?? null : null;
  // Only an authoritative JobReceipt may enable replay. A promotion receipt
  // supplies the job ID, but never attempt/version/checkpoint values.
  const selectedScoreJob: JobReceipt | null = selected
    ? scoreJobs[selected.id] ?? null
    : null;

  const promotionGateHash = gateKey ? gateSnapshots[gateKey] : undefined;
  const requestPromotionDecision = evaluateIntakePermission(
    "requestPromotion",
    activeRoleId,
    permissionContextFor(selected, "requestPromotion", {
      riskLevel: "HIGH",
    }),
  );
  const reviewPromotionDecision = evaluateIntakePermission(
    "reviewPromotion",
    activeRoleId,
    {
      ...permissionContextFor(selected, "reviewPromotion", {
        riskLevel: "CRITICAL",
      }),
      riskLevel: "CRITICAL",
      proposerSubjectId:
        selectedPromotion?.proposer_subject_id ?? selected?.submitter ?? null,
      reviewerSubjectId: subjectId,
    },
  );
  const replayScoreDecision = evaluateIntakePermission(
    "replayScore",
    activeRoleId,
    {
      ...permissionContextFor(selected, "replayScore", {
        riskLevel: "HIGH",
      }),
    },
  );
  const executePromotionDecision = evaluateIntakePermission(
    "executePromotion",
    activeRoleId,
    permissionContextFor(selected, "executePromotion", {
      riskLevel: "CRITICAL",
      proposerSubjectId:
        selectedPromotion?.proposer_subject_id ?? selected?.submitter ?? null,
      reviewerSubjectId: subjectId,
    }),
  );
  const correctionDecision = evaluateIntakePermission(
    "correct",
    activeRoleId,
    permissionContextFor(selected, "correct", {
      fieldClassification: "INTERNAL",
    }),
  );
  const intakeDecision = evaluateIntakePermission(
    "decide",
    activeRoleId,
    permissionContextFor(selected, "decide", {
      fieldClassification: "INTERNAL",
      riskLevel: "HIGH",
    }),
  );
  const retryDecision = evaluateIntakePermission(
    "retry",
    activeRoleId,
    permissionContextFor(selected, "retry", {
      fieldClassification: "INTERNAL",
    }),
  );
  // The promotion section renders on the READY branch of the real detail
  // (UX-SCR-EXP-003F) — once a decision receipt exists it stays visible on
  // every later saga state so the receipt and score job remain reachable.
  const promotionIsHydrating =
    selected &&
    promotionHydration.intakeId === selected.id &&
    promotionHydration.state === "loading";
  const promotionIsHydrated =
    selected &&
    promotionHydration.intakeId === selected.id &&
    promotionHydration.state === "ready";
  const detailSection = normalizeIntakeDetailSection(
    urlState.activeSection,
    actionError ? "error" : "timeline",
  );
  const compareTargetId =
    searchParams.get("compareTarget") ?? selected?.matchResult?.targetListingId ?? null;
  const currentOperator = {
    id: operatorSubjectId(activeRoleId, activeSubjectId),
    name: role.label,
    role: activeRoleId,
  };

  function openDecision(kind: IntakeDecisionKind) {
    decisionKeyRef.current = selected
      ? newIntakeActionIdempotencyKey(selected.id, `decide-${kind}`)
      : null;
    setActionError(null);
    updateUrlState(
      { dialog: "decide", decisionKind: kind },
      isDurableDetailPage ? "push" : "replace",
    );
  }

  function openFix(fieldKey: string) {
    correctionKeyRef.current = selected
      ? newIntakeActionIdempotencyKey(selected.id, "correct", fieldKey)
      : null;
    setActionError(null);
    updateUrlState(
      { dialog: "fix", fixFieldKey: fieldKey },
      isDurableDetailPage ? "push" : "replace",
    );
  }

  function openAssignment(kind: "transfer" | "pause") {
    setActionError(null);
    updateUrlState(
      { dialog: "assignmentSla", decisionKind: kind },
      isDurableDetailPage ? "push" : "replace",
    );
  }

  function closeChildDialog(kind: "fix" | "decision" | "assignment") {
    if (busy || promotionBusy) return;
    updateUrlState({
      dialog: isDurableDetailPage ? null : "detail",
      fixFieldKey: kind === "fix" ? null : urlState.fixFieldKey,
      decisionKind: kind === "decision" || kind === "assignment" ? null : urlState.decisionKind,
    });
    setActionError(null);
    if (kind === "fix") correctionKeyRef.current = null;
    if (kind === "decision") decisionKeyRef.current = null;
  }

  const actionDialogs = selected ? (
    <>
      {dialog === "fix" && fixField ? (
        <IntakeFieldFixDialog
          busy={busy}
          error={actionError}
          field={fixField}
          onClose={() => closeChildDialog("fix")}
          onSubmit={handleFix}
        />
      ) : null}

      {dialog === "decide" && decisionKind ? (
        <IntakeDecisionDialog
          busy={busy}
          error={actionError}
          kind={decisionKind}
          onClose={() => closeChildDialog("decision")}
          onSubmit={handleDecide}
          record={selected}
        />
      ) : null}

      {dialog === "assignmentSla" && asgKind === "transfer" ? (
        <TransferIntakeDialog
          busy={busy}
          error={actionError}
          onClose={() => closeChildDialog("assignment")}
          onConflictRefresh={handleConflictRefresh}
          onSubmit={handleTransferSubmit}
          record={selected}
        />
      ) : null}

      {dialog === "assignmentSla" && asgKind === "pause" ? (
        <PauseSlaDialog
          busy={busy}
          error={actionError}
          onClose={() => closeChildDialog("assignment")}
          onConflictRefresh={handleConflictRefresh}
          onSubmit={handlePauseSubmit}
          record={selected}
        />
      ) : null}
    </>
  ) : null;

  if (isDurableDetailPage) {
    if (
      !evaluateIntakePermission(
        "view",
        activeRoleId,
        permissionContextFor(selected, "view"),
      ).allowed
    ) {
      return (
        <DurableRouteState
          code="ODP-INTAKE-FORBIDDEN"
          kind="denied"
          message="目前角色沒有查看 Assisted Listing Intake 的權限。"
          onBack={() => router.push(intakeInboxHref(searchParams))}
          title="無法開啟收件"
        />
      );
    }
    if (loadState === "loading") {
      return (
        <DurableRouteState
          code="LOADING"
          kind="loading"
          message={`正在載入收件 ${detailIntakeId} 的持久化狀態…`}
          title="載入收件"
        />
      );
    }
    if (loadState === "error" || !selected) {
      const status = loadError?.status;
      const isMissing = status === 404 || (loadState === "ready" && !selected);
      const isDenied = status === 403;
      return (
        <DurableRouteState
          code={loadError?.code ?? (isMissing ? "ODP-INTAKE-NOT-FOUND" : "ODP-INTAKE-LOAD-FAILED")}
          correlationId={loadError?.correlationId}
          kind={isMissing ? "missing" : isDenied ? "denied" : "error"}
          message={
            loadError?.summary ??
            (isMissing
              ? `找不到收件 ${detailIntakeId}，它可能已刪除或不在目前租戶範圍。`
              : "無法載入收件。")
          }
          onBack={() => router.push(intakeInboxHref(searchParams))}
          onRetry={!isMissing && !isDenied ? () => void refresh() : undefined}
          title={isMissing ? "找不到收件" : isDenied ? "存取遭拒" : "載入失敗"}
        />
      );
    }

    return (
      <IntakeDialogDismissBoundary dismissible={!busy && !promotionBusy}>
        <IntakeProcessingDetail
          activeTab={detailSection as IntakeDetailTab}
          assignmentReceipt={assignmentReceipts[selected.id]}
          busy={busy}
          canCorrect={correctionDecision.allowed}
          canDecide={intakeDecision.allowed}
          canReplay={retryDecision.allowed}
          canReplayScore={replayScoreDecision.allowed}
          canRequestPromotion={requestPromotionDecision.allowed}
          canReviewPromotion={reviewPromotionDecision.allowed}
          canExecutePromotion={executePromotionDecision.allowed}
          canRetry={retryDecision.allowed}
          compareTargetId={compareTargetId}
          currentOperator={currentOperator}
          error={actionError}
          gateSnapshotSha256={promotionGateHash}
          jobs={selectedScoreJob ? [selectedScoreJob] : []}
          onActiveTabChange={(tab) => {
            updateUrlState(
              {
                activeSection: tab,
                compareTask: tab === "identity" ? true : urlState.compareTask,
              },
              "push",
            );
          }}
          onClaimAssignment={handleClaim}
          onClose={() => router.push(intakeInboxHref(searchParams))}
          onDecide={openDecision}
          onLookupPromotionDecision={
            selectedPromotion ? handleLookupPromotionDecision : undefined
          }
          onOpenFix={openFix}
          onOpenPause={() => openAssignment("pause")}
          onOpenTransfer={() => openAssignment("transfer")}
          onRefresh={handleConflictRefresh}
          onReplayScore={handleReplayScore}
          onRequestPromotion={handleRequestPromotion}
          onResumeSla={handleResumeSla}
          onRetry={handleRetry}
          onReviewPromotion={handleReviewPromotion}
          presentation="page"
          promotion={selectedPromotion}
          promotionBusy={promotionBusy}
          promotionError={promotionError}
          promotionExecuteDeniedReason={executePromotionDecision.reasonCode}
          promotionHydrated={Boolean(promotionIsHydrated && !promotionIsHydrating)}
          promotionIdempotencyReplayed={promotionReplayed}
          promotionReplayDeniedReason={replayScoreDecision.reasonCode}
          promotionRequestDeniedReason={requestPromotionDecision.reasonCode}
          promotionReviewDeniedReason={reviewPromotionDecision.reasonCode}
          record={selected}
          scoreJob={selectedScoreJob}
          slaReceipt={slaReceipts[selected.id]}
          testId="intake-processing-page"
        />
        {toast ? (
          <div className={styles.noteBox} data-testid="intake-toast" role="status">
            {toast}
          </div>
        ) : null}
        {actionDialogs}
      </IntakeDialogDismissBoundary>
    );
  }

  return (
    <IntakeDialogDismissBoundary dismissible={!busy && !promotionBusy}>
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
        permissionContext={permissionContextFor(null, "view", {
          fieldClassification: "INTERNAL",
          workflowState: "SUBMITTED",
        })}
        submitPermissionContext={permissionContextFor(null, "submit", {
          fieldClassification: "INTERNAL",
          workflowState: "SUBMITTED",
        })}
        permissionContextForRecord={(record, action) =>
          permissionContextFor(record, action, {
            fieldClassification: "INTERNAL",
            riskLevel:
              action === "decide" ||
              action === "reviewIdentity" ||
              action === "reviewPromotion"
                ? "HIGH"
                : undefined,
          })
        }
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
          canCorrect={correctionDecision.allowed}
          canDecide={intakeDecision.allowed}
          canRetry={retryDecision.allowed}
          error={actionError}
          onAssistedEntrySave={handleAssistedEntry}
          onClose={closeDialog}
          onDecide={openDecision}
          onOpenFix={openFix}
          onOpenFullPage={() => openFullDetail(selected.id)}
          onRetry={handleRetry}
          previewOnly
          permissionDenials={{
            correct: correctionDecision.reasonCode,
            decide: intakeDecision.reasonCode,
            retry: retryDecision.reasonCode,
          }}
          record={selected}
          assignmentReceipt={assignmentReceipts[selected.id]}
          slaReceipt={slaReceipts[selected.id]}
          onClaimAssignment={handleClaim}
          onOpenTransfer={() => openAssignment("transfer")}
          onOpenPause={() => openAssignment("pause")}
          onResumeSla={handleResumeSla}
        />
      ) : null}

      {actionDialogs}
    </IntakeDialogDismissBoundary>
  );
}

function DurableRouteState({
  code,
  correlationId,
  kind,
  message,
  onBack,
  onRetry,
  title,
}: {
  code: string;
  correlationId?: string | null;
  kind: "loading" | "missing" | "denied" | "error";
  message: string;
  onBack?: () => void;
  onRetry?: () => void;
  title: string;
}) {
  return (
    <main
      aria-live={kind === "loading" ? "polite" : undefined}
      className="odp-content"
      data-state={kind}
      data-testid={`intake-route-state-${kind}`}
      role={kind === "loading" ? "status" : "main"}
    >
      <section
        style={{
          background: "#ffffff",
          border: "1px solid #dfe4ee",
          borderRadius: "8px",
          padding: "24px",
        }}
      >
        <h1 style={{ fontSize: "20px", margin: "0 0 8px" }}>{title}</h1>
        <p>{message}</p>
        <dl>
          <dt>狀態碼</dt>
          <dd>
            <code>{code}</code>
          </dd>
          {correlationId ? (
            <>
              <dt>Correlation ID</dt>
              <dd>
                <code>{correlationId}</code>
              </dd>
            </>
          ) : null}
        </dl>
        <div style={{ display: "flex", gap: "8px" }}>
          {onRetry ? (
            <button
              className={styles.primaryButton}
              data-testid="intake-route-retry"
              onClick={onRetry}
              type="button"
            >
              重新載入
            </button>
          ) : null}
          {onBack ? (
            <button
              className={styles.secondaryButton}
              data-testid="intake-route-back"
              onClick={onBack}
              type="button"
            >
              返回 Listing 收件匣
            </button>
          ) : null}
        </div>
      </section>
    </main>
  );
}
