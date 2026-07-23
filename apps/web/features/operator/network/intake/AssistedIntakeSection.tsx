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
  CanonicalIntakeRuntimeDetail,
  IntakeCorrectableField,
  IntakeFieldValue,
  JobReceipt,
  MatchOutcome,
  OdpApiClient,
  PromotionDecisionReceipt,
  SlaReceipt,
} from "@oday-plus/openapi-client";
import type { OperatorRoleId } from "../../navigation";
import { getOperatorRole } from "../../navigation";
import styles from "./intake.module.css";
import { ListingInboxIntakeView } from "./ListingInboxIntakeView";
import { existingListingHref } from "./IntakeInboxMap";
import { IntakeDecisionDialog } from "./IntakeDecisionDialog";
import { IntakeDetailDialog } from "./IntakeDetailDialog";
import { IntakeDialogDismissBoundary } from "./IntakeDialogShell";
import { IntakeFieldFixDialog } from "./IntakeFieldFixDialog";
import {
  AssistedEntryForm,
  type AssistedEntrySubmission,
} from "./AssistedEntryForm";
import { IdentityDecisionPanel } from "./IdentityDecisionPanel";
import { ParsedDataReview, buildCanonicalFieldReview } from "./ParsedDataReview";
import { StructuredAuditTimeline } from "./StructuredAuditTimeline";
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
import { ReopenIntakeDialog } from "./ReopenIntakeDialog";
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
import type {
  InboxIntakeRecord,
  IntakeInboxBootstrapContext,
  IntakeInboxPageContract,
  IntakeInboxQueryContract,
  IntakeInboxSavedView,
} from "./inboxContracts";
import {
  canonicalAuditToStructured,
  canonicalBootstrapToInbox,
  canonicalCommandReceiptToIdentity,
  canonicalCorrectionsByField,
  canonicalDetailPresentationFacts,
  canonicalDetailToLifecycle,
  canonicalDetailToRecord,
  canonicalGraphPlan,
  canonicalHumanDecisionEvidence,
  canonicalIdentityReceipt,
  canonicalIdentityWorkflow,
  canonicalMatchToComparison,
  canonicalPageToInbox,
  canonicalSavedViewsToInbox,
  canonicalSensitiveEvidenceAccess,
  canonicalSourceEvidence,
  inboxContractToCanonicalQuery,
} from "./canonicalIntakeAdapters";
import {
  useIntakeLifecycle,
  type IntakeLifecycleSnapshot,
  type JobLifecycleReceipt,
} from "./useIntakeLifecycle";
import type {
  IdentityActor,
  IdentityDecisionCommand,
  IdentityDecisionReceipt,
} from "./identityTypes";
import type { AuthoritativeRecoveryContext } from "./evidenceContracts";

function restoreFocusAfterDialogClose(
  dialogTestId: string,
  triggerTestId: string,
) {
  let stableChecks = 0;
  const restore = () => {
    const dialog = document.querySelector(
      `[data-testid="${dialogTestId}"]`,
    );
    const trigger = Array.from(
      document.querySelectorAll<HTMLElement>(
        `[data-testid="${triggerTestId}"]`,
      ),
    ).find(
      (candidate) =>
        !candidate.hasAttribute("disabled") &&
        candidate.offsetParent !== null,
    );

    if (!dialog && trigger) {
      if (document.activeElement !== trigger) {
        trigger.focus({ preventScroll: true });
      }
      stableChecks =
        document.activeElement === trigger ? stableChecks + 1 : 0;
      return stableChecks >= 4;
    }
    stableChecks = 0;
    return false;
  };

  if (restore()) return;
  const interval = window.setInterval(() => {
    if (restore()) window.clearInterval(interval);
  }, 100);
  window.setTimeout(() => {
    window.clearInterval(interval);
  }, 12_000);
}

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

const CANONICAL_PERMISSION_ACTION: Record<IntakePermissionAction, string> = {
  view: "VIEW",
  submit: "SUBMIT_URL",
  correct: "CORRECT",
  decide: "DECIDE_MATCH",
  retry: "RETRY",
  promote: "REQUEST_PROMOTION",
  assign: "ASSIGN",
  viewEvidence: "VIEW",
  viewRestrictedEvidence: "VIEW",
  proposeIdentity: "DECIDE_MATCH",
  reviewIdentity: "DECIDE_MATCH",
  requestPromotion: "REQUEST_PROMOTION",
  reviewPromotion: "REVIEW_PROMOTION",
  executePromotion: "REVIEW_PROMOTION",
  replayScore: "REPLAY_JOB",
  reopenQuarantine: "REOPEN",
  exportEvidence: "EXPORT_EVIDENCE",
};

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

  const [records, setRecords] = useState<InboxIntakeRecord[]>([]);
  const [pageData, setPageData] = useState<IntakeInboxPageContract | undefined>();
  const [inboxQuery, setInboxQuery] = useState<IntakeInboxQueryContract>({
    page: 1,
    pageSize: 10,
    sortBy: "updatedAt",
    sortOrder: "desc",
  });
  const [bootstrapContext, setBootstrapContext] =
    useState<IntakeInboxBootstrapContext>();
  const [savedViews, setSavedViews] = useState<IntakeInboxSavedView[]>();
  const [canonicalDetails, setCanonicalDetails] = useState<
    Record<string, CanonicalIntakeRuntimeDetail>
  >({});
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [loadError, setLoadError] = useState<IntakeApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<IntakeApiError | null>(null);
  const [actionRecovery, setActionRecovery] =
    useState<AuthoritativeRecoveryContext | null>(null);
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
      const detail = record ? canonicalDetails[record.id] : null;
      const actorFacts = detail?.lifecycle.actor_facts;
      const canonicalAction = CANONICAL_PERMISSION_ACTION[action];
      const detailAllowed =
        actorFacts && canonicalAction
          ? actorFacts.allowed_actions.includes(canonicalAction)
          : undefined;
      const detailDenial =
        actorFacts && canonicalAction
          ? actorFacts.denied_action_reasons[canonicalAction]
          : undefined;
      const recordMasked = Object.values(record?.parsedFields ?? {}).some(
        (field) => field.masked === true,
      );
      const sourceIds = operatorSession?.scope?.sourceIds ?? [];
      const sourceInScope = record
        ? sourceIds.length > 0
          ? sourceIds.includes(record.sourceId)
          : operatorSession?.scope?.resourceInScope
        : operatorSession?.scope?.resourceInScope;

      return {
        resourceInScope: authoritative
          ? actorFacts?.scope.in_scope ??
            operatorSession?.scope?.resourceInScope
          : true,
        isOwner: record
          ? record.owner === subjectId || record.submitter === subjectId
          : authoritative
            ? undefined
            : true,
        isAssigned: record
          ? detail?.lifecycle.assignment?.owner_subject_id === subjectId ||
            record.owner === subjectId ||
            assignmentReceipts[record.id]?.owner_subject_id === subjectId
          : authoritative
            ? undefined
            : true,
        sourceInScope: authoritative ? sourceInScope : true,
        purposeDeclared: authoritative
          ? actorFacts?.purpose.bound ?? operatorSession?.purposeDeclared
          : true,
        fieldMasked:
          recordMasked ||
          Boolean(actorFacts?.masking.has_masked_fields) ||
          Boolean(operatorSession?.maskingReasonCode),
        fieldClassification: record ? "INTERNAL" : undefined,
        workflowState: record?.stage ?? null,
        proposerSubjectId:
          record && promotionReceipts[record.id]
            ? promotionReceipts[record.id].proposer_subject_id
            : actorFacts?.second_actor.proposer_subject_ids[0] ??
              record?.submitter,
        reviewerSubjectId: subjectId,
        serverAllowed: authoritative
          ? detailAllowed ??
            operatorSession?.allowedActions.includes(action)
          : undefined,
        serverReasonCode: authoritative
          ? (
              detailDenial ??
              actorFacts?.second_actor.reason_code ??
              operatorSession?.denialReasonByAction[action] ??
              operatorSession?.denialReasonCode ??
              ((detailAllowed ??
                operatorSession?.allowedActions.includes(action))
                ? null
                : "ROLE_DENIED")
            ) as IntakePermissionContext["serverReasonCode"]
          : undefined,
        maskingReasonCode:
          actorFacts?.masking.reason_codes[0] ??
          operatorSession?.maskingReasonCode,
        ...overrides,
      };
    },
    [
      assignmentReceipts,
      canonicalDetails,
      operatorSession,
      promotionReceipts,
      subjectId,
    ],
  );
  // Every submit attempt reuses one key so a network retry cannot double-create.
  const submitKeyRef = useRef<string | null>(null);
  const correctionKeyRef = useRef<string | null>(null);
  const reopenKeyRef = useRef<string | null>(null);
  const assistedEntryKeyRef = useRef<string | null>(null);
  const decisionKeyRef = useRef<string | null>(null);
  const savedViewKeyRef = useRef<string | null>(null);
  const assignmentTriggerRef = useRef<HTMLElement | null>(null);
  const assignmentDialogOpen =
    dialog === "assignmentSla" && asgKind !== null;

  useEffect(() => {
    if (assignmentDialogOpen || !assignmentTriggerRef.current) return;
    const trigger = assignmentTriggerRef.current;
    const frame = requestAnimationFrame(() => {
      const testId = trigger.dataset.testid;
      const currentTrigger = trigger.isConnected && trigger.offsetParent !== null
        ? trigger
        : testId
          ? Array.from(
              document.querySelectorAll<HTMLElement>(
                `[data-testid="${testId}"]`,
              ),
            ).find(
              (candidate) =>
                !candidate.hasAttribute("disabled") &&
                candidate.offsetParent !== null,
            ) ?? null
          : null;
      currentTrigger?.focus({ preventScroll: true });
      assignmentTriggerRef.current = null;
    });
    return () => cancelAnimationFrame(frame);
  }, [assignmentDialogOpen]);

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
    if (detailIntakeId) {
      const result = await intakeApi.get(client, detailIntakeId);
      if (result.ok) {
        const record = canonicalDetailToRecord(result.value);
        setCanonicalDetails((current) => ({
          ...current,
          [detailIntakeId]: result.value,
        }));
        setRecords([record]);
        setPageData(undefined);
        setLoadState("ready");
        setLoadError(null);
      } else {
        setLoadState("error");
        setLoadError(result.error);
      }
      return;
    }

    const [result, bootstrapResult, savedViewsResult] = await Promise.all([
      intakeApi.list(client, inboxContractToCanonicalQuery({
        ...inboxQuery,
        selectedHeatZoneId,
      })),
      intakeApi.bootstrap(client),
      intakeApi.savedViews(client),
    ]);
    if (result.ok) {
      const page = canonicalPageToInbox(result.value, inboxQuery.page);
      setRecords(page.items);
      setPageData(page);
      if (bootstrapResult.ok) {
        setBootstrapContext(canonicalBootstrapToInbox(bootstrapResult.value));
      }
      if (savedViewsResult.ok) {
        setSavedViews(canonicalSavedViewsToInbox(savedViewsResult.value));
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

  const handleCreateSavedView = useCallback(
    async (name: string, query: IntakeInboxQueryContract) => {
      if (!client) {
        return { ok: false as const, error: missingClientError() };
      }
      savedViewKeyRef.current ??= newIntakeActionIdempotencyKey(
        subjectId,
        "saved-view",
      );
      const result = await intakeApi.createSavedView(
        client,
        {
          name,
          query: inboxContractToCanonicalQuery({
            ...query,
            savedView: undefined,
          }),
          resource: "intake",
          visibility: "PRIVATE",
        },
        { idempotencyKey: savedViewKeyRef.current },
      );
      if (!result.ok) return result;

      savedViewKeyRef.current = null;
      const value = canonicalSavedViewsToInbox([result.value])[0];
      setSavedViews((current) => [
        ...(current ?? []).filter((view) => view.id !== value.id),
        value,
      ]);
      return { ok: true as const, value };
    },
    [client, subjectId],
  );

  useEffect(() => {
    correctionKeyRef.current = null;
    reopenKeyRef.current = null;
    assistedEntryKeyRef.current = null;
    decisionKeyRef.current = null;
    setPromotionError(null);
    setPromotionReplayed(false);
  }, [selectedId]);

  const selectedRecord = records.find((record) => record.id === selectedId) ?? null;
  const canonicalDetail = selectedId ? canonicalDetails[selectedId] ?? null : null;
  const lifecycleLoader = useCallback(
    async (): Promise<IntakeLifecycleSnapshot> => {
      if (!client || !detailIntakeId) {
        throw new Error("Canonical intake detail is unavailable.");
      }
      const result = await intakeApi.get(client, detailIntakeId);
      if (!result.ok) throw new Error(result.error.summary);
      const record = canonicalDetailToRecord(result.value);
      setCanonicalDetails((current) => ({
        ...current,
        [detailIntakeId]: result.value,
      }));
      setRecords([record]);
      if (result.value.lifecycle.promotion) {
        const promotionResult = await intakeApi.getPromotionForIntake(
          client,
          detailIntakeId,
        );
        if (promotionResult.ok) {
          setPromotionReceipts((current) => ({
            ...current,
            [detailIntakeId]: promotionResult.value,
          }));
          const scoreJobId = promotionResult.value.site_score_job_id;
          if (scoreJobId) {
            const jobResult = await intakeApi.getScoreJob(client, scoreJobId);
            if (jobResult.ok) {
              setScoreJobs((current) => ({
                ...current,
                [detailIntakeId]: jobResult.value,
              }));
            }
          }
        }
      }
      return canonicalDetailToLifecycle(result.value);
    },
    [client, detailIntakeId],
  );
  const lifecycleState = useIntakeLifecycle({
    intakeId: detailIntakeId ?? selectedId ?? "intake-not-selected",
    enabled: Boolean(isDurableDetailPage && client && detailIntakeId),
    initialSnapshot: canonicalDetail
      ? canonicalDetailToLifecycle(canonicalDetail)
      : null,
    loadSnapshot: lifecycleLoader,
  });
  const lifecycleSnapshot = lifecycleState.snapshot;
  const selected =
    lifecycleSnapshot && lifecycleSnapshot.record.id === selectedId
      ? lifecycleSnapshot.record
      : selectedRecord;

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

  function applyCanonicalDetail(
    detail: CanonicalIntakeRuntimeDetail,
  ): AssistedIntake {
    const record = canonicalDetailToRecord(detail);
    setCanonicalDetails((current) => ({
      ...current,
      [detail.intake_id]: detail,
    }));
    applyRecord(record);
    return record;
  }

  async function recordActionFailure(
    error: IntakeApiError,
    operation: string,
    preservedInput: Record<string, unknown>,
  ) {
    setActionError(error);
    let authoritative = selected ? canonicalDetails[selected.id] : undefined;
    if (client && selected) {
      const readback = await intakeApi.get(client, selected.id);
      if (readback.ok) {
        authoritative = readback.value;
        applyCanonicalDetail(readback.value);
      }
    }
    setActionRecovery({
      operation,
      current_state:
        error.currentState ?? authoritative?.state ?? selected?.stage ?? null,
      current_version:
        error.currentVersion ?? authoritative?.version ?? selected?.version ?? null,
      server_value: authoritative
        ? {
            intake_id: authoritative.intake_id,
            state: authoritative.state,
            version: authoritative.version,
            assignment: authoritative.lifecycle.assignment,
            sla: authoritative.lifecycle.sla,
          }
        : null,
      preserved_input: preservedInput,
    });
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
    const record = applyCanonicalDetail(result.value);
    const submissionListingId =
      record.submissionReceipt?.existingListingId ??
      (record.matchResult?.outcome === "EXACT_DUPLICATE"
        ? record.matchResult.targetListingId
        : null);
    setToast(
      submissionListingId
        ? `已於識別檢查攔截 — 此 URL 已存在（${submissionListingId}），未執行擷取`
        : `收件 ${record.id} 已建立 — ${record.policyLabel}`,
    );
    router.push(
      submissionListingId
        ? existingListingHref(submissionListingId)
        : intakeDetailHref(record.id, searchParams),
    );
    return record;
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
    applyCanonicalDetail(result.value);
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
    setActionRecovery(null);
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
    applyCanonicalDetail(result.value);
    setToast("補錄完成 — 已進入比對");
    void refresh();
  }

  async function handleAssistedEntryCommit(
    submission: AssistedEntrySubmission,
  ) {
    if (!client || !selected) {
      return {
        status: "FAILED" as const,
        failure: {
          code: "INTAKE_UNAVAILABLE",
          summary: "收件資料尚未載入。",
          occurredAt: new Date().toISOString(),
          retryable: true,
        },
      };
    }
    const result = await intakeApi.correct(
      client,
      selected.id,
      {
        fields: submission.fields as Partial<
          Record<IntakeCorrectableField, IntakeFieldValue>
        >,
        reason: submission.reason,
        riskSummary: "人工補錄會建立可覆核的 authoritative correction lineage。",
        riskAcknowledged: submission.riskAcknowledged,
        actorRoleId: activeRoleId,
        actorName: role.label,
      },
      {
        idempotencyKey: submission.operationId,
        ifMatch:
          submission.ifMatchVersion === null
            ? undefined
            : `W/"${submission.ifMatchVersion}"`,
      },
    );
    if (!result.ok) {
      return {
        status:
          result.error.status === 409 || result.error.status === 428
            ? ("CONFLICT" as const)
            : ("FAILED" as const),
        failure: {
          code: result.error.code,
          summary: result.error.summary,
          occurredAt: result.error.occurredAt,
          retryable: result.error.retryable,
          currentVersion: result.error.currentVersion,
          currentState: result.error.currentState,
          correlationId: result.error.correlationId,
        },
      };
    }
    applyCanonicalDetail(result.value);
    setToast("人工補錄已寫入，欄位 lineage 與覆核狀態已更新。");
    return {
      status: "COMMITTED" as const,
      authoritativeVersion: result.value.version,
      correctionIds: result.value.lifecycle.mutation_receipts
        .map((entry) => entry.receipt.receipt_id)
        .filter((value): value is string => Boolean(value)),
    };
  }

  async function handleIdentitySubmit(
    command: IdentityDecisionCommand,
  ): Promise<IdentityDecisionReceipt> {
    if (!client || !canonicalDetail || !canonicalDetail.match_case_id) {
      throw new Error("Canonical identity contract is unavailable.");
    }
    const options = {
      idempotencyKey: newIntakeActionIdempotencyKey(
        canonicalDetail.intake_id,
        `${command.phase}-${command.graphOperation ?? command.outcomeAction ?? "identity"}`,
      ),
      ifMatch: `W/"${
        command.phase === "REVIEW"
          ? canonicalDetail.lifecycle.latest_decision_receipt?.version ?? 1
          : command.matchCaseVersion
      }"`,
    };
    const result =
      command.phase === "REVIEW" && command.decisionId
        ? await intakeApi.reviewIdentityDecision(
            client,
            command.decisionId,
            {
              decision: command.reviewDisposition ?? "REJECT",
              reason: command.reason,
              risk_acknowledged: command.riskAcknowledged,
            },
            options,
          )
        : !command.graphOperation
          ? await intakeApi.proposeIdentityDecision(
              client,
              canonicalDetail.match_case_id,
              {
                decision_type: identityOutcomeCommand(command.outcomeAction),
                reason: command.reason,
                risk_acknowledged: command.riskAcknowledged,
                target_listing_id:
                  canonicalDetail.match_case?.target_listing_id ?? undefined,
              },
              options,
            )
          : await submitIdentityGraphCommand(
              client,
              canonicalDetail,
              command,
              options,
            );
    if (!result.ok) {
      throw {
        code: result.error.code,
        summary: result.error.summary,
        currentVersion: result.error.currentVersion ?? canonicalDetail.version,
        currentState: result.error.currentState ?? canonicalDetail.state,
        currentOwner: canonicalDetail.assigned_to,
        correlationId: result.error.correlationId ?? "",
        occurredAt: result.error.occurredAt,
        nextAction: result.error.nextAction,
      };
    }
    const receipt = canonicalCommandReceiptToIdentity(
      result.value,
      canonicalDetail.match_case_id,
    );
    const refreshed = await intakeApi.get(client, canonicalDetail.intake_id);
    if (refreshed.ok) applyCanonicalDetail(refreshed.value);
    return receipt;
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
    const updatedRecord = applyCanonicalDetail(result.value);
    updateUrlState({ dialog: isDurableDetailPage ? null : "detail", decisionKind: null });
    setToast(`決策已寫入 — ${updatedRecord.stage} · 已記錄於 Audit Trail`);
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
    applyCanonicalDetail(result.value);
    setToast("已重試擷取 — 先前送件內容與人工修正已保留");
    void refresh();
  }

  async function handleReopen({
    reason,
    riskAcknowledged,
  }: {
    reason: string;
    riskAcknowledged: true;
  }) {
    if (!client || !selected || busy) return;
    setBusy(true);
    setActionError(null);
    if (!reopenKeyRef.current) {
      reopenKeyRef.current = newIntakeActionIdempotencyKey(selected.id, "reopen");
    }
    const result = await intakeApi.reopen(
      client,
      selected.id,
      {
        reason,
        risk_acknowledged: riskAcknowledged,
      },
      {
        idempotencyKey: reopenKeyRef.current,
        ifMatch: `W/"${selected.version}"`,
      },
    );
    setBusy(false);
    if (!result.ok) {
      await recordActionFailure(result.error, "QUARANTINE_REOPEN", {
        reason,
        risk_acknowledged: riskAcknowledged,
      });
      return;
    }
    reopenKeyRef.current = null;
    const updated = applyCanonicalDetail(result.value);
    updateUrlState({
      dialog: isDurableDetailPage ? null : "detail",
      activeSection: "timeline",
    });
    setToast(
      updated.stage === "QUARANTINED"
        ? "解除隔離提案已記錄；等待另一位具權限人員覆核"
        : "隔離已由獨立覆核者解除，durable receipt 已寫入",
    );
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
      setActionRecovery({
        operation: "INTAKE_RETRY",
        current_state: result.error.currentState ?? null,
        current_version: result.error.currentVersion ?? null,
        preserved_input: {
          intake_id: intakeId,
          actor_role_id: activeRoleId,
        },
      });
      return;
    }
    applyCanonicalDetail(result.value);
    setToast(`收件 ${intakeId} 已直接重試；原始證據與修正紀錄保留`);
    void refresh();
  }

  async function handleInboxClaim(intakeId: string) {
    const record = records.find((item) => item.id === intakeId);
    if (!client || !record || busy) {
      return {
        ok: false as const,
        error: {
          code: "CLAIM_UNAVAILABLE",
          summary: "目前無法認領此收件。",
          occurredAt: new Date().toISOString(),
          nextAction: "請重新整理 Listing Inbox 後再試。",
          status: 409,
          retryable: true,
          correlationId: null,
        },
      };
    }
    const idempotencyKey = newIntakeActionIdempotencyKey(intakeId, "claim");
    const correlationId = newCorrelationId();
    const result = record.assignmentId
      ? await intakeApi.claimAssignment(
          client,
          record.assignmentId,
          { reason: "Operator claimed intake from Listing Inbox" },
          {
            idempotencyKey,
            correlationId,
            ifMatch: `W/"${record.assignmentVersion ?? record.version}"`,
          },
        )
      : await intakeApi.assign(
          client,
          intakeId,
          {
            owner_subject_id: subjectId,
            owner_role: activeRoleId,
            reason: "Operator claimed unassigned intake from Listing Inbox",
            due_at: new Date(Date.now() + 5 * 24 * 60 * 60 * 1000).toISOString(),
          },
          {
            idempotencyKey,
            correlationId,
            ifMatch: `W/"${record.version}"`,
          },
        );
    if (!result.ok) return result;
    setAssignmentReceipts((current) => ({
      ...current,
      [intakeId]: result.value,
    }));
    const detailResult = await intakeApi.get(client, intakeId);
    if (detailResult.ok) applyCanonicalDetail(detailResult.value);
    void refresh();
    return result;
  }

  async function handleClaim() {
    if (!client || !selected || busy) return;
    setBusy(true);
    setActionError(null);
    setActionRecovery(null);

    const key = newIntakeActionIdempotencyKey(selected.id, "claim-asg");
    const correlationId = newCorrelationId();

    const result = selected.assignmentId
      ? await intakeApi.claimAssignment(
          client,
            selected.assignmentId,
            { reason: "Claiming existing assignment" },
            {
              idempotencyKey: key,
              correlationId,
              ifMatch: `W/"${
                lifecycleSnapshot?.assignment?.version ??
                selected.assignmentVersion ??
                selected.version
              }"`,
            },
          )
      : await intakeApi.assign(
          client,
          selected.id,
          {
            owner_subject_id: subjectId,
            owner_role: activeRoleId,
            reason: "Claiming assignment for manual triage review",
            due_at: new Date(Date.now() + 5 * 24 * 60 * 60 * 1000).toISOString(),
          },
          {
            idempotencyKey: key,
            correlationId,
            ifMatch: `W/"${selected.version}"`,
          },
        );
    setBusy(false);
    if (!result.ok) {
      await recordActionFailure(result.error, "ASSIGNMENT_CLAIM", {
        assignment_id: selected.assignmentId,
        intake_id: selected.id,
        reason: selected.assignmentId
          ? "Claiming existing assignment"
          : "Claiming assignment for manual triage review",
      });
      return;
    }
    const receipt = result.value;
    setToast(`已成功認領收件！指派 ID: ${receipt.assignment_id}`);
    setAssignmentReceipts((prev) => ({ ...prev, [selected.id]: receipt }));
    const getResult = await intakeApi.get(client, selected.id);
    if (getResult.ok) applyCanonicalDetail(getResult.value);
    void refresh();
  }

  async function handleTransferSubmit(payload: {
    target_owner_subject_id: string;
    target_owner_role: string;
    handoff_note: string;
  }) {
    if (!client || !selected || busy) return;
    setBusy(true);
    setActionError(null);
    setActionRecovery(null);

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

    const result = await intakeApi.transferAssignment(
        client,
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
          ifMatch: `W/"${
            lifecycleSnapshot?.assignment?.version ??
            selected.assignmentVersion ??
            selected.version
          }"`,
        }
      );
    setBusy(false);
    if (!result.ok) {
      await recordActionFailure(result.error, "ASSIGNMENT_TRANSFER", payload);
      return;
    }
    const receipt = result.value;
    setToast("已成功轉交收件！");
    setAssignmentReceipts((prev) => ({ ...prev, [selected.id]: receipt }));
    const getResult = await intakeApi.get(client, selected.id);
    if (getResult.ok) applyCanonicalDetail(getResult.value);
    updateUrlState({ dialog: isDurableDetailPage ? null : "detail" });
    void refresh();
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

    const result = await intakeApi.pauseSla(
        client,
        slaId,
        {
          reason: payload.reason,
          expected_resume_at: payload.expected_resume_at,
        },
        {
          idempotencyKey: key,
          correlationId,
          ifMatch: `W/"${
            lifecycleSnapshot?.sla?.version ??
            selected.slaVersion ??
            selected.version
          }"`,
        }
      );
    setBusy(false);
    if (!result.ok) {
      setActionError(result.error);
      return;
    }
    const receipt = result.value;
    setToast("SLA 已暫停！");
    setSlaReceipts((prev) => ({ ...prev, [selected.id]: receipt }));
    const getResult = await intakeApi.get(client, selected.id);
    if (getResult.ok) applyCanonicalDetail(getResult.value);
    updateUrlState({ dialog: isDurableDetailPage ? null : "detail" });
    void refresh();
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

    const result = await intakeApi.resumeSla(
        client,
        slaId,
        { reason: "Manual resume SLA" },
        {
          idempotencyKey: key,
          correlationId,
          ifMatch: `W/"${
            lifecycleSnapshot?.sla?.version ??
            selected.slaVersion ??
            selected.version
          }"`,
        }
      );
    setBusy(false);
    if (!result.ok) {
      setActionError(result.error);
      return;
    }
    const receipt = result.value;
    setToast("SLA 已恢復計時！");
    setSlaReceipts((prev) => ({ ...prev, [selected.id]: receipt }));
    const getResult = await intakeApi.get(client, selected.id);
    if (getResult.ok) applyCanonicalDetail(getResult.value);
    void refresh();
  }

  async function handleEscalateAssignment() {
    if (!client || !selected?.assignmentId || busy) return;
    setBusy(true);
    setActionError(null);
    const result = await intakeApi.escalateAssignment(
        client,
        selected.assignmentId,
        { reason: "Operator escalated overdue intake review" },
        {
          idempotencyKey: newIntakeActionIdempotencyKey(selected.id, "escalate"),
          correlationId: newCorrelationId(),
          ifMatch: `W/"${
            lifecycleSnapshot?.assignment?.version ??
            selected.assignmentVersion ??
            selected.version
          }"`,
        },
      );
    setBusy(false);
    if (!result.ok) {
      setActionError(result.error);
      return;
    }
    const receipt = result.value;
    setAssignmentReceipts((current) => ({
      ...current,
      [selected.id]: receipt,
    }));
    await handleConflictRefresh();
    setToast(`收件已升級處理，Assignment ${receipt.assignment_id}`);
  }

  async function handleCompleteAssignment() {
    if (!client || !selected?.assignmentId || busy) return;
    setBusy(true);
    setActionError(null);
    const result = await intakeApi.completeAssignment(
      client,
      selected.assignmentId,
      { reason: "Operator completed intake assignment" },
      {
        idempotencyKey: newIntakeActionIdempotencyKey(selected.id, "complete"),
        correlationId: newCorrelationId(),
        ifMatch: `W/"${
          lifecycleSnapshot?.assignment?.version ??
          selected.assignmentVersion ??
          selected.version
        }"`,
      },
    );
    setBusy(false);
    if (!result.ok) {
      setActionError(result.error);
      return;
    }
    setAssignmentReceipts((current) => ({
      ...current,
      [selected.id]: result.value,
    }));
    await handleConflictRefresh();
    setToast(`Assignment ${result.value.assignment_id} 已完成。`);
  }

  async function handleCancelIntake() {
    if (!client || !selected || busy) return;
    setBusy(true);
    setActionError(null);
    const result = await intakeApi.cancel(
        client,
        selected.id,
        { reason: "Operator cancelled intake processing" },
        {
          idempotencyKey: newIntakeActionIdempotencyKey(selected.id, "cancel"),
          correlationId: newCorrelationId(),
          ifMatch: `W/"${selected.version}"`,
        },
      );
    setBusy(false);
    if (!result.ok) {
      setActionError(result.error);
      return;
    }
    await handleConflictRefresh();
    setToast("收件已取消，CANCELLED 為 terminal state。");
  }

  async function handleReplayDlq(jobId?: string) {
    if (!client || !selected || !jobId || busy) return;
    const job = canonicalDetail?.lifecycle.job;
    if (
      !job ||
      job.job_id !== jobId ||
      job.version === null ||
      !isRetryCheckpoint(job.checkpoint)
    ) {
      return;
    }
    setBusy(true);
    const result = await intakeApi.retryScoreJob(
      client,
      jobId,
      {
        checkpoint: job.checkpoint,
        reason: "Authorized operator replay from durable checkpoint",
        risk_acknowledged: true,
      },
      {
        idempotencyKey: newIntakeActionIdempotencyKey(selected.id, "replay-job", jobId),
        ifMatch: `W/"${job.version}"`,
      },
    );
    setBusy(false);
    if (!result.ok) {
      setActionError(result.error);
      return;
    }
    setScoreJobs((current) => ({
      ...current,
      [selected.id]: result.value.receipt,
    }));
    await handleConflictRefresh();
  }

  async function handleCancelJob(jobId: string) {
    if (!client || !selected || busy) return;
    const job =
      lifecycleState.snapshot?.jobs.find((entry) => entry.job_id === jobId) ??
      (canonicalDetail?.lifecycle.job?.job_id === jobId
        ? canonicalDetail.lifecycle.job
        : null);
    if (!job || job.version === null) return;

    setBusy(true);
    setActionError(null);
    const result = await intakeApi.cancelJob(
      client,
      jobId,
      { reason: "Operator cancelled active intake job" },
      {
        idempotencyKey: newIntakeActionIdempotencyKey(
          selected.id,
          "cancel-job",
          jobId,
        ),
        correlationId: newCorrelationId(),
        ifMatch: `W/"${job.version}"`,
      },
    );
    setBusy(false);
    if (!result.ok) {
      setActionError(result.error);
      return;
    }
    setScoreJobs((current) => ({
      ...current,
      [selected.id]: result.value.receipt,
    }));
    await handleConflictRefresh();
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
    if (getResult.ok) applyCanonicalDetail(getResult.value);
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
      applyCanonicalDetail(getResult.value);
    }
  }

  const fixField = selected && fixFieldKey ? selected.parsedFields?.[fixFieldKey] : undefined;

  const selectedPromotion = selected ? promotionReceipts[selected.id] ?? null : null;
  // Only an authoritative JobReceipt may enable replay. A promotion receipt
  // supplies the job ID, but never attempt/version/checkpoint values.
  const lifecycleScoreJob = selectedPromotion?.site_score_job_id
    ? lifecycleState.snapshot?.jobs.find(
        (job) => job.job_id === selectedPromotion.site_score_job_id,
      ) ?? null
    : null;
  const selectedScoreJob: JobReceipt | JobLifecycleReceipt | null = selected
    ? scoreJobs[selected.id] ?? lifecycleScoreJob
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
  const reopenDecision = evaluateIntakePermission(
    "reopenQuarantine",
    activeRoleId,
    permissionContextFor(selected, "reopenQuarantine", {
      fieldClassification: "INTERNAL",
      riskLevel: "CRITICAL",
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
  const persistedDetailError: IntakeApiError | null =
    selected?.failure
      ? {
          status: selected.stage === "FAILED" ? 500 : 409,
          code: selected.failure.code,
          summary: selected.failure.summary,
          nextAction: selected.failure.nextAction,
          correlationId: selected.correlationId,
          occurredAt:
            canonicalDetail?.updated_at ?? new Date(0).toISOString(),
          retryable: selected.failure.retryable,
          currentState: selected.stage,
          currentVersion: selected.version,
          reasonCode: selected.failure.code,
        }
      : null;
  const compareTargetId =
    searchParams.get("compareTarget") ?? selected?.matchResult?.targetListingId ?? null;
  const currentOperator = {
    id: operatorSubjectId(activeRoleId, activeSubjectId),
    name: role.label,
    role: activeRoleId,
  };
  const identityActor: IdentityActor = {
    subjectId: currentOperator.id,
    displayName: currentOperator.name,
    role: currentOperator.role,
  };
  const canonicalComparison = canonicalDetail
    ? canonicalMatchToComparison(canonicalDetail)
    : null;
  const canonicalGraph = canonicalDetail?.match_case
    ? canonicalGraphPlan(canonicalDetail.match_case.graph_plan, identityActor)
    : null;
  const canonicalCorrections = canonicalDetail
    ? canonicalCorrectionsByField(canonicalDetail)
    : {};
  const canonicalPresentationFacts = canonicalDetail
    ? canonicalDetailPresentationFacts(canonicalDetail)
    : null;
  const canonicalEvidence = canonicalDetail
    ? canonicalSourceEvidence(canonicalDetail)
    : null;
  const canonicalEvidenceAccess = canonicalDetail
    ? canonicalSensitiveEvidenceAccess(canonicalDetail)
    : null;
  const canonicalDecisionEvidence = canonicalDetail
    ? canonicalHumanDecisionEvidence(canonicalDetail)
    : null;
  const canonicalReviewSection =
    selected && canonicalDetail ? (
      selected.stage === "AWAITING_ASSISTED_ENTRY" ? (
        <AssistedEntryForm
          baseVersion={canonicalDetail.version}
          disabled={!correctionDecision.allowed || busy}
          draftIdentity={{
            tenantId:
              operatorSession?.tenantId ??
              String(canonicalDetail.scope.tenant_id ?? ""),
            intakeId: canonicalDetail.intake_id,
            actorSubjectId: currentOperator.id,
          }}
          initialValues={assistedInitialValues(canonicalDetail)}
          onCommit={handleAssistedEntryCommit}
          originalUrl={canonicalDetail.original_url ?? ""}
          policy={selected.policy}
          sourceId={canonicalDetail.source_id ?? ""}
        />
      ) : (
        <ParsedDataReview
          canCorrect={correctionDecision.allowed}
          fields={buildCanonicalFieldReview(canonicalDetail.fields, {
            sourceSnapshotId: canonicalDetail.source_snapshot_id,
            parserRunId: canonicalDetail.parser_run_id,
            correctionsByField: canonicalCorrections,
          })}
          onCorrect={(field) => openFix(field.fieldPath)}
        />
      )
    ) : undefined;
  const canonicalIdentitySection =
    selected && canonicalDetail && canonicalComparison ? (
      <IdentityDecisionPanel
        busy={busy}
        comparison={canonicalComparison}
        draftIdentity={{
          tenantId:
            operatorSession?.tenantId ??
            String(canonicalDetail.scope.tenant_id ?? ""),
          intakeId: canonicalDetail.intake_id,
          matchCaseId: canonicalComparison.matchCaseId,
          actorId: currentOperator.id,
        }}
        durableDesktopHref={intakeDetailHref(
          canonicalDetail.intake_id,
          "section=identity&compare=true",
        )}
        errorMessage={actionError?.summary ?? null}
        graphPlans={canonicalGraph ? [canonicalGraph] : []}
        onRefreshConflict={handleConflictRefresh}
        onSubmit={handleIdentitySubmit}
        receipt={canonicalIdentityReceipt(canonicalDetail)}
        record={selected}
        workflow={canonicalIdentityWorkflow(canonicalDetail, identityActor)}
      />
    ) : undefined;
  const canonicalAuditSection = canonicalDetail ? (
    <StructuredAuditTimeline
      events={canonicalAuditToStructured(canonicalDetail)}
    />
  ) : undefined;

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
    assignmentTriggerRef.current = document.activeElement as HTMLElement | null;
    setActionError(null);
    updateUrlState(
      { dialog: "assignmentSla", decisionKind: kind },
      isDurableDetailPage ? "push" : "replace",
    );
  }

  function openReopen() {
    if (!selected || selected.stage !== "QUARANTINED") return;
    reopenKeyRef.current = newIntakeActionIdempotencyKey(selected.id, "reopen");
    setActionError(null);
    updateUrlState(
      { dialog: "reopen" },
      isDurableDetailPage ? "push" : "replace",
    );
  }

  function closeChildDialog(kind: "fix" | "decision" | "assignment") {
    if (busy || promotionBusy) return;
    const assignmentKind = kind === "assignment" ? asgKind : null;
    updateUrlState({
      dialog: isDurableDetailPage ? null : "detail",
      fixFieldKey: kind === "fix" ? null : urlState.fixFieldKey,
      decisionKind: kind === "decision" || kind === "assignment" ? null : urlState.decisionKind,
    });
    if (assignmentKind === "transfer") {
      restoreFocusAfterDialogClose(
        "transfer-intake-dialog",
        "asg-btn-transfer",
      );
    } else if (assignmentKind === "pause") {
      restoreFocusAfterDialogClose("pause-sla-dialog", "asg-btn-pause");
    }
    if (kind === "fix") correctionKeyRef.current = null;
    if (kind === "decision") decisionKeyRef.current = null;
  }

  const handleInboxQueryChange = useCallback(
    (next: IntakeInboxQueryContract) => {
      setInboxQuery((current) =>
        JSON.stringify(current) === JSON.stringify(next) ? current : next,
      );
    },
    [],
  );

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

      {dialog === "reopen" && selected.stage === "QUARANTINED" ? (
        <ReopenIntakeDialog
          busy={busy}
          error={actionError}
          independentReviewRequired={Boolean(
            canonicalDetail?.lifecycle.actor_facts.second_actor.required,
          )}
          onClose={() => {
            if (busy) return;
            updateUrlState({
              dialog: isDurableDetailPage ? null : "detail",
            });
            setActionError(null);
            reopenKeyRef.current = null;
          }}
          onSubmit={handleReopen}
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
          auditReferences={canonicalDetail?.audit}
          auditSection={canonicalAuditSection}
          busy={busy}
          canCorrect={correctionDecision.allowed}
          canDecide={intakeDecision.allowed}
          canReplay={retryDecision.allowed}
          canCancelJob={Boolean(
            lifecycleState.snapshot?.allowed_actions.includes("CANCEL_JOB"),
          )}
          canReopen={reopenDecision.allowed}
          canReplayScore={replayScoreDecision.allowed}
          canRequestPromotion={requestPromotionDecision.allowed}
          canReviewPromotion={reviewPromotionDecision.allowed}
          canExecutePromotion={executePromotionDecision.allowed}
          canRetry={retryDecision.allowed}
          compareTargetId={compareTargetId}
          correctionsByField={canonicalCorrections}
          currentOperator={currentOperator}
          detailFacts={canonicalPresentationFacts}
          error={actionError ?? persistedDetailError}
          evidenceAccess={canonicalEvidenceAccess}
          evidenceVerification={null}
          exportReceipt={null}
          recovery={actionRecovery}
          fields={canonicalDetail?.fields}
          gateSnapshotSha256={promotionGateHash}
          history={canonicalDetail?.processing_history}
          humanDecisionEvidence={canonicalDecisionEvidence}
          identitySection={canonicalIdentitySection}
          jobs={
            lifecycleState.snapshot?.jobs ??
            (selectedScoreJob ? [selectedScoreJob] : [])
          }
          lifecycle={lifecycleState.snapshot}
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
          onCancel={handleCancelIntake}
          onCancelJob={handleCancelJob}
          onClose={() => router.push(intakeInboxHref(searchParams))}
          onCompleteAssignment={handleCompleteAssignment}
          onDecide={openDecision}
          onEscalateAssignment={handleEscalateAssignment}
          onLookupPromotionDecision={
            selectedPromotion ? handleLookupPromotionDecision : undefined
          }
          onOpenFix={openFix}
          onOpenPause={() => openAssignment("pause")}
          onOpenTransfer={() => openAssignment("transfer")}
          onRefresh={handleConflictRefresh}
          onReplayScore={handleReplayScore}
          onReplayDlq={handleReplayDlq}
          onReopen={openReopen}
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
          reopenDeniedReason={reopenDecision.reasonCode}
          record={selected}
          reviewSection={canonicalReviewSection}
          scoreJob={selectedScoreJob}
          sla={lifecycleState.snapshot?.sla ?? undefined}
          slaReceipt={slaReceipts[selected.id]}
          sourceEvidence={canonicalEvidence}
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
        bootstrapContext={bootstrapContext}
        busy={busy}
        loadError={loadError}
        loadState={loadState}
        onAddSubmit={handleSubmit}
        onOpenDetail={openDetail}
        onClaimIntake={handleInboxClaim}
        onCreateSavedView={handleCreateSavedView}
        onRetryLoad={() => void refresh()}
        onRetryIntake={(intakeId) => void handleInboxRetry(intakeId)}
        onQueryChange={handleInboxQueryChange}
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
              action === "reviewPromotion" ||
              action === "replayScore"
                ? "HIGH"
                : undefined,
          })
        }
        records={records}
        savedViews={savedViews}
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

function assistedInitialValues(
  detail: CanonicalIntakeRuntimeDetail,
): Record<string, string | number | boolean | null> {
  return Object.fromEntries(
    detail.fields.flatMap((field) => {
      const value = field.effective;
      return value === null ||
        typeof value === "string" ||
        typeof value === "number" ||
        typeof value === "boolean"
        ? [[field.field_path, value]]
        : [];
    }),
  );
}

function identityOutcomeCommand(
  action: IdentityDecisionCommand["outcomeAction"],
): "CREATE" | "REVISE" | "DUPLICATE" | "QUARANTINE" | "REJECT" {
  switch (action) {
    case "APPEND_REVISION":
      return "REVISE";
    case "MARK_DUPLICATE":
      return "DUPLICATE";
    case "QUARANTINE":
    case "SEND_TO_STEWARD":
      return "QUARANTINE";
    case "REJECT":
      return "REJECT";
    case "CREATE":
    default:
      return "CREATE";
  }
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function isRetryCheckpoint(
  value: string | null,
): value is
  | "RETRIEVING"
  | "PARSING"
  | "MATCHING"
  | "CANDIDATE_CREATING"
  | "SCORE_QUEUED" {
  return (
    value === "RETRIEVING" ||
    value === "PARSING" ||
    value === "MATCHING" ||
    value === "CANDIDATE_CREATING" ||
    value === "SCORE_QUEUED"
  );
}

function operationValue(
  operation: Record<string, unknown> | null,
  snake: string,
  camel: string,
): unknown {
  return operation?.[snake] ?? operation?.[camel];
}

async function submitIdentityGraphCommand(
  client: OdpApiClient,
  detail: CanonicalIntakeRuntimeDetail,
  command: IdentityDecisionCommand,
  options: {
    idempotencyKey: string;
    ifMatch: string;
    correlationId?: string;
  },
) {
  const plan = detail.match_case?.graph_plan;
  const operation = objectValue(plan?.operations[0]);
  if (!plan || !command.graphOperation) {
    throw new Error("Authoritative identity graph plan is unavailable.");
  }

  if (command.graphOperation === "REVERSAL") {
    const decisionId =
      command.decisionId ?? plan.original_decision?.decision_id ?? null;
    if (!decisionId) {
      throw new Error("Reversal requires an authoritative original decision ID.");
    }
    return intakeApi.reverseIdentityDecision(
      client,
      decisionId,
      {
        reason: command.reason,
        risk_acknowledged: command.riskAcknowledged,
      },
      options,
    );
  }

  if (command.graphOperation === "MERGE") {
    const propertyIds = plan.before_graph.nodes
      .filter((node) => node.node_type === "PROPERTY")
      .map((node) => node.node_id);
    const targetPropertyId =
      operationValue(operation, "target_property_id", "targetPropertyId");
    const sourcePropertyIds =
      operationValue(operation, "source_property_ids", "sourcePropertyIds");
    const target =
      typeof targetPropertyId === "string" ? targetPropertyId : propertyIds[0];
    const sources = stringArray(sourcePropertyIds).length
      ? stringArray(sourcePropertyIds)
      : propertyIds.filter((propertyId) => propertyId !== target);
    if (!target || sources.length === 0) {
      throw new Error("Merge plan does not contain source and target properties.");
    }
    return intakeApi.proposeIdentityMerge(
      client,
      {
        source_property_ids: sources,
        target_property_id: target,
        reason: command.reason,
        risk_acknowledged: true,
      },
      options,
    );
  }

  if (command.graphOperation === "SPLIT") {
    const sourcePropertyId = operationValue(
      operation,
      "source_property_id",
      "sourcePropertyId",
    );
    const partitions = operationValue(operation, "partitions", "partitions");
    if (typeof sourcePropertyId !== "string" || !Array.isArray(partitions)) {
      throw new Error("Split plan does not contain an authoritative partition.");
    }
    return intakeApi.proposeIdentitySplit(
      client,
      {
        source_property_id: sourcePropertyId,
        partitions: partitions.map((value) => {
          const partition = objectValue(value);
          return {
            target_property_id:
              typeof partition?.target_property_id === "string"
                ? partition.target_property_id
                : typeof partition?.targetPropertyId === "string"
                  ? partition.targetPropertyId
                  : null,
            source_identity_edge_ids: stringArray(
              partition?.source_identity_edge_ids ??
                partition?.sourceIdentityEdgeIds,
            ),
          };
        }),
        reason: command.reason,
        risk_acknowledged: true,
      },
      options,
    );
  }

  const originalDecisionId =
    operationValue(operation, "original_decision_id", "originalDecisionId") ??
    plan.original_decision?.decision_id;
  const replacementEdges = operationValue(
    operation,
    "replacement_edges",
    "replacementEdges",
  );
  if (typeof originalDecisionId !== "string" || !Array.isArray(replacementEdges)) {
    throw new Error("Unmerge plan does not contain authoritative replacement edges.");
  }
  return intakeApi.proposeIdentityUnmerge(
    client,
    {
      original_decision_id: originalDecisionId,
      replacement_edges: replacementEdges.map((value) => {
        const partition = objectValue(value);
        return {
          target_property_id:
            typeof partition?.target_property_id === "string"
              ? partition.target_property_id
              : typeof partition?.targetPropertyId === "string"
                ? partition.targetPropertyId
                : null,
          source_identity_edge_ids: stringArray(
            partition?.source_identity_edge_ids ??
              partition?.sourceIdentityEdgeIds,
          ),
        };
      }),
      reason: command.reason,
      risk_acknowledged: true,
    },
    options,
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
