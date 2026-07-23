"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { AssistedIntake } from "@oday-plus/openapi-client";
import styles from "./identity.module.css";
import { IdentityDecisionReceiptView } from "./IdentityDecisionReceipt";
import { IdentityGraphPlanView } from "./IdentityGraphPlan";
import { ListingCompareTable } from "./ListingCompareTable";
import { MatchEvidencePanel } from "./MatchEvidencePanel";
import {
  IDENTITY_ACTION_LABEL,
  IDENTITY_OUTCOME_ACTIONS,
  commandRequiresIndependentReview,
  defaultOutcomeAction,
  type IdentityConflict,
  type IdentityDecisionCommand,
  type IdentityDecisionDraft,
  type IdentityDraftPersistenceReceipt,
  type IdentityDraftScope,
  type IdentityDecisionReceipt,
  type IdentityGraphOperation,
  type IdentityGraphPlan,
  type IdentityOutcomeAction,
  type IdentityReviewWorkflow,
  type IdentityComparisonContract,
} from "./identityTypes";

type IdentityPanelTab = "evidence" | "compare" | "graph";
type DraftPersistenceState =
  | { status: "IDLE" }
  | { status: "LOADED" }
  | { status: "PENDING" }
  | { status: "SAVED"; draftVersion: number; persistedAt: string }
  | { status: "CLEARED"; draftVersion: number; persistedAt: string }
  | { status: "FAILED"; message: string };

const GRAPH_OPERATIONS: readonly IdentityGraphOperation[] = [
  "MERGE",
  "SPLIT",
  "UNMERGE",
  "REVERSAL",
];

function createInitialDraft(
  comparison: IdentityComparisonContract,
  workflow: IdentityReviewWorkflow,
): IdentityDecisionDraft {
  if (workflow.proposal) {
    return {
      commandType: workflow.proposal.graphOperation ? "GRAPH" : "OUTCOME",
      outcomeAction: workflow.proposal.outcomeAction,
      graphOperation: workflow.proposal.graphOperation,
      graphPlanId: workflow.proposal.graphPlanId,
      reason: "",
      riskAcknowledged: false,
    };
  }
  return {
    commandType: "OUTCOME",
    outcomeAction: defaultOutcomeAction(comparison.outcome),
    graphOperation: null,
    graphPlanId: null,
    reason: "",
    riskAcknowledged: false,
  };
}

function isStoredDraft(value: unknown): value is IdentityDecisionDraft {
  if (!value || typeof value !== "object") return false;
  const draft = value as Partial<IdentityDecisionDraft>;
  return (
    (draft.commandType === "OUTCOME" || draft.commandType === "GRAPH") &&
    typeof draft.reason === "string" &&
    typeof draft.riskAcknowledged === "boolean"
  );
}

function loadDraft(storageKey: string, fallback: IdentityDecisionDraft): IdentityDecisionDraft {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.sessionStorage.getItem(storageKey);
    if (!raw) return fallback;
    const parsed: unknown = JSON.parse(raw);
    return isStoredDraft(parsed) ? parsed : fallback;
  } catch {
    return fallback;
  }
}

function buildDraftStorageKey(scope: IdentityDraftScope): string {
  return [
    "odp",
    "intake",
    "identity-draft",
    "v2",
    scope.tenantId,
    scope.intakeId,
    scope.matchCaseId,
    scope.actorId,
  ]
    .map(encodeURIComponent)
    .join(":");
}

function clearStoredDraft(storageKey: string) {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(storageKey);
  } catch {
    // Storage can be unavailable in restricted browser contexts.
  }
}

function isIdentityConflict(error: unknown): error is IdentityConflict {
  if (!error || typeof error !== "object") return false;
  const candidate = error as Partial<IdentityConflict>;
  return (
    typeof candidate.code === "string" &&
    typeof candidate.summary === "string" &&
    typeof candidate.currentVersion === "number" &&
    typeof candidate.currentState === "string" &&
    typeof candidate.correlationId === "string" &&
    typeof candidate.occurredAt === "string" &&
    typeof candidate.nextAction === "string"
  );
}

type IdentityDecisionPanelCommonProps = {
  record: Pick<
    AssistedIntake,
    "id" | "correlationId" | "snapshotId" | "parserVersion"
  >;
  comparison: IdentityComparisonContract;
  graphPlans: IdentityGraphPlan[];
  workflow: IdentityReviewWorkflow;
  draftIdentity: IdentityDraftScope;
  durableDesktopHref: string;
  busy?: boolean;
  conflict?: IdentityConflict | null;
  receipt?: IdentityDecisionReceipt | null;
  errorMessage?: string | null;
  onSubmit: (command: IdentityDecisionCommand) => Promise<IdentityDecisionReceipt>;
  onRefreshConflict?: () => Promise<void> | void;
  className?: string;
};

type IdentityDecisionPanelProps =
  | (IdentityDecisionPanelCommonProps & {
      draftPersistence?: "SESSION";
      persistedDraft?: never;
      onDraftSave?: never;
    })
  | (IdentityDecisionPanelCommonProps & {
      draftPersistence: "SERVER";
      persistedDraft: IdentityDecisionDraft | null;
      onDraftSave: (
        draft: IdentityDecisionDraft,
        scope: IdentityDraftScope,
      ) => Promise<IdentityDraftPersistenceReceipt>;
    });

export function IdentityDecisionPanel(props: IdentityDecisionPanelProps) {
  const {
    record,
    comparison,
    graphPlans,
    workflow,
    draftIdentity,
    durableDesktopHref,
    busy = false,
    conflict = null,
    receipt = null,
    errorMessage = null,
    onSubmit,
    onRefreshConflict,
    className,
  } = props;
  const draftPersistence = props.draftPersistence ?? "SESSION";
  const persistedDraft =
    props.draftPersistence === "SERVER" ? props.persistedDraft : undefined;
  const onDraftSave =
    props.draftPersistence === "SERVER" ? props.onDraftSave : undefined;
  if (
    draftIdentity.intakeId !== record.id ||
    draftIdentity.matchCaseId !== comparison.matchCaseId ||
    draftIdentity.actorId !== workflow.currentActor.subjectId
  ) {
    throw new Error("Identity draft scope does not match the authoritative intake workflow.");
  }
  const storageKey = buildDraftStorageKey(draftIdentity);
  const initialDraftRef = useRef({
    storageKey,
    draft: createInitialDraft(comparison, workflow),
  });
  if (initialDraftRef.current.storageKey !== storageKey) {
    initialDraftRef.current = {
      storageKey,
      draft: createInitialDraft(comparison, workflow),
    };
  }
  const initialDraft = initialDraftRef.current.draft;
  const [draft, setDraft] = useState<IdentityDecisionDraft>(() => {
    if (draftPersistence === "SERVER") return persistedDraft ?? initialDraft;
    return workflow.proposal ? initialDraft : loadDraft(storageKey, initialDraft);
  });
  const [draftPersistenceState, setDraftPersistenceState] =
    useState<DraftPersistenceState>(() =>
      draftPersistence === "SERVER" && persistedDraft ? { status: "LOADED" } : { status: "IDLE" },
    );
  const lastHydratedDraftSignature = useRef<string | undefined>(undefined);
  const draftSaveSequence = useRef(0);
  const [activeTab, setActiveTab] = useState<IdentityPanelTab>("evidence");
  const [localError, setLocalError] = useState<string | null>(null);
  const [caughtConflict, setCaughtConflict] = useState<IdentityConflict | null>(null);
  const [serverReceipt, setServerReceipt] = useState<IdentityDecisionReceipt | null>(null);

  useEffect(() => {
    if (draftPersistence !== "SERVER") return;
    const authoritativeSignature =
      persistedDraft === null ? "null" : JSON.stringify(persistedDraft);
    if (lastHydratedDraftSignature.current === authoritativeSignature) return;
    lastHydratedDraftSignature.current = authoritativeSignature;
    draftSaveSequence.current += 1;
    clearStoredDraft(storageKey);
    if (persistedDraft) {
      setDraft(persistedDraft);
      setDraftPersistenceState({ status: "LOADED" });
      return;
    }
    setDraft(initialDraft);
    setDraftPersistenceState({ status: "IDLE" });
  }, [draftPersistence, initialDraft, persistedDraft, storageKey]);

  useEffect(() => {
    if (draftPersistence !== "SESSION") return;
    try {
      window.sessionStorage.setItem(storageKey, JSON.stringify(draft));
    } catch {
      // Storage can be unavailable in restricted browser contexts. The
      // controlled command boundary still preserves the in-memory draft.
    }
  }, [draft, draftPersistence, storageKey]);

  const shownConflict = conflict ?? caughtConflict;
  const shownReceipt = receipt ?? serverReceipt;
  const availableActions = IDENTITY_OUTCOME_ACTIONS[comparison.outcome];
  const activePlan = useMemo(
    () =>
      draft.graphPlanId
        ? graphPlans.find((plan) => plan.planId === draft.graphPlanId) ?? null
        : draft.graphOperation
          ? graphPlans.find((plan) => plan.operation === draft.graphOperation) ?? null
          : null,
    [draft.graphOperation, draft.graphPlanId, graphPlans],
  );
  const reviewPhase =
    workflow.status === "PENDING_REVIEW" || workflow.status === "REVERSAL_PENDING";
  const selfReviewDenied =
    reviewPhase &&
    workflow.requiresIndependentReview &&
    workflow.currentActor.subjectId === workflow.proposer.subjectId;
  const commandNeedsIndependentReview =
    workflow.requiresIndependentReview ||
    commandRequiresIndependentReview(comparison.outcome, draft);
  const riskRequired =
    draft.commandType === "GRAPH" ||
    comparison.outcome === "POSSIBLE_MATCH" ||
    draft.outcomeAction === "QUARANTINE";
  const reasonValid = draft.reason.trim().length >= 3;
  const riskValid = !riskRequired || draft.riskAcknowledged;
  const canPropose =
    !reviewPhase &&
    workflow.canPropose &&
    reasonValid &&
    riskValid &&
    !busy &&
    !shownConflict;
  const canReview =
    reviewPhase &&
    workflow.canReview &&
    !selfReviewDenied &&
    reasonValid &&
    !busy &&
    !shownConflict;

  async function persistServerDraft(updated: IdentityDecisionDraft) {
    if (!onDraftSave) return;
    const requestSequence = draftSaveSequence.current + 1;
    draftSaveSequence.current = requestSequence;
    setDraftPersistenceState({ status: "PENDING" });
    try {
      const result = await onDraftSave(updated, draftIdentity);
      if (requestSequence !== draftSaveSequence.current) return;
      clearStoredDraft(storageKey);
      if (result.draft === null) {
        setDraft(initialDraft);
        setDraftPersistenceState({
          status: "CLEARED",
          draftVersion: result.draftVersion,
          persistedAt: result.persistedAt,
        });
        return;
      }
      setDraft(result.draft);
      setDraftPersistenceState({
        status: "SAVED",
        draftVersion: result.draftVersion,
        persistedAt: result.persistedAt,
      });
    } catch (error: unknown) {
      if (requestSequence !== draftSaveSequence.current) return;
      setDraftPersistenceState({
        status: "FAILED",
        message: error instanceof Error ? error.message : "Server draft save failed.",
      });
    }
  }

  function updateDraft(next: Partial<IdentityDecisionDraft>) {
    const updated = { ...draft, ...next };
    setDraft(updated);
    if (draftPersistence === "SERVER") {
      void persistServerDraft(updated);
    }
    setLocalError(null);
  }

  function selectOutcomeAction(action: IdentityOutcomeAction) {
    updateDraft({
      commandType: "OUTCOME",
      outcomeAction: action,
      graphOperation: null,
      graphPlanId: null,
      riskAcknowledged: false,
    });
  }

  function selectGraphOperation(operation: IdentityGraphOperation) {
    const plan = graphPlans.find((candidate) => candidate.operation === operation);
    if (!plan) return;
    updateDraft({
      commandType: "GRAPH",
      outcomeAction: null,
      graphOperation: operation,
      graphPlanId: plan.planId,
      riskAcknowledged: false,
    });
    setActiveTab("graph");
  }

  function createCommand(
    phase: IdentityDecisionCommand["phase"],
    reviewDisposition: IdentityDecisionCommand["reviewDisposition"],
  ): IdentityDecisionCommand {
    return {
      phase,
      reviewDisposition,
      matchCaseId: comparison.matchCaseId,
      matchCaseVersion: comparison.matchCaseVersion,
      decisionId: workflow.decisionId,
      outcomeAction: draft.commandType === "OUTCOME" ? draft.outcomeAction : null,
      graphOperation: draft.commandType === "GRAPH" ? draft.graphOperation : null,
      graphPlanId: draft.commandType === "GRAPH" ? draft.graphPlanId : null,
      expectedGraphVersion:
        draft.commandType === "GRAPH" ? activePlan?.expectedGraphVersion ?? null : null,
      reason: draft.reason.trim(),
      riskAcknowledged: draft.riskAcknowledged,
      proposerId: workflow.proposer.subjectId,
      reviewerId:
        phase === "REVIEW"
          ? workflow.currentActor.subjectId
          : workflow.reviewer?.subjectId ?? null,
      requiresIndependentReview: commandNeedsIndependentReview,
    };
  }

  async function submit(
    phase: IdentityDecisionCommand["phase"],
    reviewDisposition: IdentityDecisionCommand["reviewDisposition"],
  ) {
    if (!reasonValid) {
      setLocalError("請輸入至少 3 個字的決策原因。");
      return;
    }
    if (riskRequired && !draft.riskAcknowledged && reviewDisposition !== "REJECT") {
      setLocalError("此決策必須完成風險確認。");
      return;
    }
    if (phase === "REVIEW" && selfReviewDenied) {
      setLocalError("SELF_REVIEW_DENIED：提案者不可審查自己的 identity 決策。");
      return;
    }

    setLocalError(null);
    try {
      const result = await onSubmit(createCommand(phase, reviewDisposition));
      setServerReceipt(result);
    } catch (error: unknown) {
      if (isIdentityConflict(error)) {
        setCaughtConflict(error);
        return;
      }
      setLocalError(error instanceof Error ? error.message : "Identity command failed.");
    }
  }

  async function refreshConflict() {
    await onRefreshConflict?.();
    setCaughtConflict(null);
    setLocalError(null);
  }

  return (
    <section
      aria-labelledby="identity-decision-title"
      className={`${styles.boundary} ${className ?? ""}`}
      data-outcome={comparison.outcome}
      data-testid="identity-decision-panel"
    >
      <div className={styles.headingRow}>
        <div>
          <h2 className={styles.title} id="identity-decision-title">
            Identity comparison and reversible decision
          </h2>
          <p className={styles.subtitle}>
            Intake <code>{record.id}</code> · Match case <code>{comparison.matchCaseId}</code>
          </p>
        </div>
        <span
          className={styles.badge}
          data-outcome={comparison.outcome}
          data-testid="identity-match-badge"
        >
          {comparison.outcome}
        </span>
      </div>

      <div className={styles.desktopRequired} data-testid="identity-desktop-required">
        <strong>此 identity 比對與可逆圖譜決策需要桌面版。</strong>
        {draftPersistence === "SERVER" ? (
          <div
            aria-live="polite"
            data-status={draftPersistenceState.status}
            data-testid="identity-server-draft-status"
          >
            {draftPersistenceState.status === "IDLE"
              ? "尚未取得 server 保存回執；目前內容不可視為已保存。"
              : null}
            {draftPersistenceState.status === "LOADED"
              ? "已載入 authoritative server draft；後續修改尚未保存。"
              : null}
            {draftPersistenceState.status === "PENDING" ? "正在等待 server 保存草稿。" : null}
            {draftPersistenceState.status === "SAVED"
              ? `Server 已確認保存草稿，version ${draftPersistenceState.draftVersion}，時間 ${draftPersistenceState.persistedAt}。`
              : null}
            {draftPersistenceState.status === "CLEARED"
              ? `Server 已確認清除草稿，version ${draftPersistenceState.draftVersion}，時間 ${draftPersistenceState.persistedAt}。`
              : null}
            {draftPersistenceState.status === "FAILED"
              ? `草稿保存失敗：${draftPersistenceState.message}。目前內容仍在畫面中，但不可視為已保存。`
              : null}
          </div>
        ) : (
          <p>草稿已保留在目前瀏覽器工作階段；請以同一瀏覽器的較寬視窗繼續。</p>
        )}
        <a data-testid="identity-desktop-link" href={durableDesktopHref}>
          開啟桌面版 identity review
        </a>
      </div>

      <div className={styles.desktopWorkflow} data-testid="identity-desktop-workflow">
        <div className={styles.metaRow} data-testid="identity-actors">
          <span className={styles.meta}>
            提案者：{workflow.proposer.displayName} ({workflow.proposer.subjectId})
          </span>
          <span className={styles.meta}>
            審查者：
            {workflow.reviewer
              ? `${workflow.reviewer.displayName} (${workflow.reviewer.subjectId})`
              : "尚未指定"}
          </span>
          <span className={styles.meta}>
            Current actor: {workflow.currentActor.displayName} ({workflow.currentActor.subjectId})
          </span>
          <span className={styles.badge}>{workflow.status}</span>
        </div>

        {comparison.outcome === "POSSIBLE_MATCH" ? (
          <p className={styles.notice} data-testid="identity-no-auto-merge-note">
            <code>POSSIBLE_MATCH</code> 不會自動合併。每個處置都必須提交明確原因，並依
            workflow 進入獨立審查。
          </p>
        ) : null}

        {selfReviewDenied ? (
          <p className={styles.error} data-testid="self-review-denied" role="alert">
            <strong>SELF_REVIEW_DENIED</strong>：提案者與審查者是同一 subject，不能核准或拒絕此案。
          </p>
        ) : null}

        {workflow.denialReasonCode ? (
          <p className={styles.error} data-testid="identity-denial-reason" role="alert">
            {workflow.denialReasonCode}
          </p>
        ) : null}

        <div aria-label="Identity review sections" className={styles.tabs} role="tablist">
          {(
            [
              ["evidence", "比對證據"],
              ["compare", "欄位並列比對"],
              ["graph", "可逆圖譜計畫"],
            ] as const
          ).map(([tab, label]) => (
            <button
              aria-selected={activeTab === tab}
              className={styles.tab}
              data-testid={`identity-tab-${tab}`}
              key={tab}
              onClick={() => setActiveTab(tab)}
              role="tab"
              type="button"
            >
              {label}
            </button>
          ))}
        </div>

        {activeTab === "evidence" ? (
          <MatchEvidencePanel comparison={comparison} correlationId={record.correlationId} />
        ) : null}
        {activeTab === "compare" ? <ListingCompareTable comparison={comparison} /> : null}
        {activeTab === "graph" ? (
          activePlan ? (
            <IdentityGraphPlanView plan={activePlan} />
          ) : (
            <p className={styles.notice} data-testid="identity-graph-plan-empty">
              請先選擇一個有 authoritative plan 的 graph operation。
            </p>
          )
        ) : null}

        {!reviewPhase ? (
          <section aria-labelledby="identity-action-title" className={styles.section}>
            <h3 className={styles.title} id="identity-action-title">
              Outcome decision
            </h3>
            <div className={styles.actions} data-testid="identity-outcome-actions">
              {availableActions.map((action) => (
                <button
                  aria-pressed={
                    draft.commandType === "OUTCOME" && draft.outcomeAction === action
                  }
                  className={
                    action === "REJECT" || action === "QUARANTINE"
                      ? styles.buttonDanger
                      : draft.commandType === "OUTCOME" && draft.outcomeAction === action
                        ? styles.buttonPrimary
                        : styles.button
                  }
                  data-action={action}
                  data-testid={`identity-action-${action}`}
                  disabled={busy || !workflow.canPropose}
                  key={action}
                  onClick={() => selectOutcomeAction(action)}
                  type="button"
                >
                  {IDENTITY_ACTION_LABEL[action]}
                </button>
              ))}
            </div>

            <h3 className={styles.title}>Reversible graph operation</h3>
            <div className={styles.actions} data-testid="identity-graph-actions">
              {GRAPH_OPERATIONS.map((operation) => {
                const plan = graphPlans.find((candidate) => candidate.operation === operation);
                return (
                  <button
                    aria-pressed={
                      draft.commandType === "GRAPH" && draft.graphOperation === operation
                    }
                    className={
                      draft.commandType === "GRAPH" && draft.graphOperation === operation
                        ? styles.buttonPrimary
                        : styles.button
                    }
                    data-testid={`identity-graph-${operation}`}
                    disabled={busy || !workflow.canPropose || !plan}
                    key={operation}
                    onClick={() => selectGraphOperation(operation)}
                    title={plan ? `${operation} plan ${plan.planId}` : `${operation} plan unavailable`}
                    type="button"
                  >
                    {operation}
                  </button>
                );
              })}
            </div>
          </section>
        ) : null}

        <section aria-labelledby="identity-reason-title" className={styles.section}>
          <h3 className={styles.title} id="identity-reason-title">
            {reviewPhase ? "Independent review" : "Decision proposal"}
          </h3>
          <p className={styles.subtitle}>
            {commandNeedsIndependentReview
              ? "此命令需要獨立審查；proposal receipt 不代表 graph 已執行。"
              : "此命令依目前 workflow 可直接提交。"}
          </p>

          {reviewPhase && workflow.proposal ? (
            <div className={styles.notice} data-testid="identity-authoritative-proposal">
              <strong>待審 proposal：</strong>
              {workflow.proposal.graphOperation ??
                (workflow.proposal.outcomeAction
                  ? IDENTITY_ACTION_LABEL[workflow.proposal.outcomeAction]
                  : "未提供 action")}
              {" · "}提案原因：{workflow.proposal.reason}
              {" · "}提案者風險確認：
              {workflow.proposal.riskAcknowledged ? "已確認" : "未確認"}
            </div>
          ) : null}

          <label className={styles.label} htmlFor="identity-decision-reason">
            {reviewPhase ? "審查原因" : "決策原因"}
          </label>
          <textarea
            className={styles.textarea}
            data-testid="identity-decision-reason"
            disabled={busy}
            id="identity-decision-reason"
            onChange={(event) => updateDraft({ reason: event.target.value })}
            value={draft.reason}
          />

          <label className={styles.checkbox} htmlFor="identity-risk-ack">
            <input
              checked={draft.riskAcknowledged}
              data-testid="identity-risk-ack"
              disabled={busy}
              id="identity-risk-ack"
              onChange={(event) => updateDraft({ riskAcknowledged: event.target.checked })}
              type="checkbox"
            />
            <span>
              我已閱讀 current／submitted 差異、graph before／after、redirect、Candidate 與
              lineage impact。
            </span>
          </label>

          {shownConflict ? (
            <div className={styles.error} data-testid="identity-conflict-banner" role="alert">
              <strong>{shownConflict.code}</strong>：{shownConflict.summary}
              <div>
                Current state {shownConflict.currentState} · version {shownConflict.currentVersion}
                {shownConflict.currentOwner ? ` · owner ${shownConflict.currentOwner}` : ""}
              </div>
              <div>
                Correlation {shownConflict.correlationId} · {shownConflict.occurredAt}
              </div>
              <div>{shownConflict.nextAction}</div>
              {onRefreshConflict ? (
                <button
                  className={styles.button}
                  data-testid="identity-conflict-refresh"
                  onClick={refreshConflict}
                  type="button"
                >
                  重新讀取 authoritative state
                </button>
              ) : null}
            </div>
          ) : null}

          {localError || errorMessage ? (
            <p className={styles.error} data-testid="identity-decision-error" role="alert">
              {localError ?? errorMessage}
            </p>
          ) : null}

          <div className={styles.actions}>
            {reviewPhase ? (
              <>
                <button
                  className={styles.buttonPrimary}
                  data-testid="identity-review-approve"
                  disabled={!canReview || !riskValid}
                  onClick={() => submit("REVIEW", "APPROVE")}
                  type="button"
                >
                  核准 identity 決策
                </button>
                <button
                  className={styles.buttonDanger}
                  data-testid="identity-review-reject"
                  disabled={!canReview}
                  onClick={() => submit("REVIEW", "REJECT")}
                  type="button"
                >
                  拒絕 identity 決策
                </button>
              </>
            ) : (
              <button
                className={styles.buttonPrimary}
                data-testid="identity-submit-proposal"
                disabled={!canPropose}
                onClick={() => submit("PROPOSE", null)}
                type="button"
              >
                {commandNeedsIndependentReview ? "提交獨立審查" : "提交 identity 決策"}
              </button>
            )}
          </div>
        </section>

        {shownReceipt ? <IdentityDecisionReceiptView receipt={shownReceipt} /> : null}
      </div>
    </section>
  );
}
