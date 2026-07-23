"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  AssistedIntake,
  JobReceipt,
  PromotionDecisionReceipt,
  SlaReceipt,
  TransitionReceipt,
} from "@oday-plus/openapi-client";

export type LifecycleStream =
  | "INTAKE"
  | "ASSIGNMENT"
  | "SLA"
  | "DECISION"
  | "PROMOTION"
  | "JOB";

export type LifecycleRefreshReason =
  | "INITIAL"
  | "POLL"
  | "VISIBLE"
  | "MANUAL"
  | "SUBSCRIPTION_RECOVERY";

export type PersistedLifecycleTransition = TransitionReceipt & {
  /** Optional on stream-specific endpoints where the collection names it. */
  stream?: LifecycleStream;
  actor_role?: string | null;
  reason?: string | null;
  attempt?: number | null;
  checkpoint?: string | null;
  timeout_at?: string | null;
  next_retry_at?: string | null;
  queue_name?: string | null;
  owner_subject_id?: string | null;
  correlation_id?: string | null;
};

export type AssignmentLifecycleReceipt = {
  assignment_id: string;
  status: "UNASSIGNED" | "ASSIGNED" | "CLAIMED" | "TRANSFERRED" | "ESCALATED" | "COMPLETED";
  owner_subject_id: string | null;
  owner_display_name?: string | null;
  owner_role?: string | null;
  queue_name: string | null;
  assigned_at?: string | null;
  claimed_at?: string | null;
  transferred_at?: string | null;
  escalated_at?: string | null;
  completed_at?: string | null;
  due_at: string | null;
  version: number;
  audit_event_id: string;
};

export type SlaLifecycleReceipt = SlaReceipt & {
  expected_resume_at?: string | null;
  paused_at?: string | null;
  resumed_at?: string | null;
  escalation_level?: number | null;
};

export type JobLifecycleReceipt = JobReceipt & {
  queue_name?: string | null;
  queued_at?: string | null;
  started_at?: string | null;
  timeout_at?: string | null;
  next_retry_at?: string | null;
  cancelled_at?: string | null;
  dead_lettered_at?: string | null;
  completed_at?: string | null;
  max_attempts?: number | null;
  retryable?: boolean;
};

export type DecisionLifecycleReceipt = {
  decision_id: string;
  decision_type: string;
  status:
    | "DRAFT"
    | "PENDING_REVIEW"
    | "APPROVED"
    | "REJECTED"
    | "EXECUTING"
    | "EXECUTED"
    | "FAILED"
    | "REVERSAL_PENDING"
    | "REVERSED"
    | "SUPERSEDED";
  proposer_subject_id?: string | null;
  reviewer_subject_id?: string | null;
  version: number;
  occurred_at: string;
  audit_event_id: string;
  correlation_id: string;
};

export type IntakeLifecycleAction =
  | "CANCEL_INTAKE"
  | "RETRY_INTAKE"
  | "REOPEN_INTAKE"
  | "CLAIM_ASSIGNMENT"
  | "TRANSFER_ASSIGNMENT"
  | "PAUSE_SLA"
  | "RESUME_SLA"
  | "ESCALATE_ASSIGNMENT"
  | "COMPLETE_ASSIGNMENT"
  | "CANCEL_JOB"
  | "REPLAY_JOB";

export type IntakeLifecycleSnapshot = {
  record: AssistedIntake;
  intake_history: PersistedLifecycleTransition[];
  assignment: AssignmentLifecycleReceipt | null;
  assignment_history: PersistedLifecycleTransition[];
  sla: SlaLifecycleReceipt | null;
  sla_history: PersistedLifecycleTransition[];
  decisions: DecisionLifecycleReceipt[];
  decision_history: PersistedLifecycleTransition[];
  promotion: PromotionDecisionReceipt | null;
  promotion_history: PersistedLifecycleTransition[];
  jobs: JobLifecycleReceipt[];
  job_history: PersistedLifecycleTransition[];
  allowed_actions: IntakeLifecycleAction[];
  refreshed_at: string;
  /** Monotonic aggregate sequence when the server exposes one. */
  sequence?: number;
  /** Authoritative aggregate update time, not the browser refresh time. */
  updated_at?: string;
  version: number;
};

export type LifecycleLoadContext = {
  signal: AbortSignal;
  reason: LifecycleRefreshReason;
};

export type LifecycleSubscription = (
  handlers: {
    onSnapshot: (snapshot: IntakeLifecycleSnapshot) => void;
    onError: (error: unknown) => void;
  },
) => () => void;

export type UseIntakeLifecycleOptions = {
  intakeId: string;
  enabled?: boolean;
  initialSnapshot?: IntakeLifecycleSnapshot | null;
  loadSnapshot: (context: LifecycleLoadContext) => Promise<IntakeLifecycleSnapshot>;
  subscribe?: LifecycleSubscription;
  activeIntervalMs?: number;
  maxBackoffMs?: number;
};

export type LifecycleRefreshState = {
  snapshot: IntakeLifecycleSnapshot | null;
  error: Error | null;
  loading: boolean;
  refreshing: boolean;
  consecutiveFailures: number;
  lastRefreshedAt: string | null;
  nextRefreshAt: string | null;
  mode: "IDLE" | "POLLING" | "SUBSCRIBED" | "HIDDEN" | "STOPPED";
  refresh: (reason?: LifecycleRefreshReason) => Promise<void>;
};

const DEFAULT_INTERVAL_MS = 2_500;
const DEFAULT_MAX_BACKOFF_MS = 30_000;

export function lifecycleBackoffDelay(
  consecutiveFailures: number,
  baseMs = DEFAULT_INTERVAL_MS,
  maxMs = DEFAULT_MAX_BACKOFF_MS,
): number {
  if (consecutiveFailures <= 0) return baseMs;
  return Math.min(maxMs, baseMs * 2 ** Math.min(consecutiveFailures, 8));
}

function asError(error: unknown): Error {
  return error instanceof Error ? error : new Error("Lifecycle refresh failed");
}

function parsedTime(value?: string): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Reject snapshots that cannot belong to the active record or are older than
 * any monotonic server coordinate already rendered.
 */
export function isLifecycleSnapshotCurrent(
  candidate: IntakeLifecycleSnapshot,
  current: IntakeLifecycleSnapshot | null,
  intakeId: string,
): boolean {
  if (candidate.record.id !== intakeId) return false;
  if (!current || current.record.id !== intakeId) return true;

  if (candidate.version < current.version) return false;
  if (
    candidate.sequence !== undefined &&
    current.sequence !== undefined &&
    candidate.sequence < current.sequence
  ) {
    return false;
  }

  const candidateUpdatedAt = parsedTime(candidate.updated_at);
  const currentUpdatedAt = parsedTime(current.updated_at);
  if (
    candidateUpdatedAt !== null &&
    currentUpdatedAt !== null &&
    candidateUpdatedAt < currentUpdatedAt
  ) {
    return false;
  }

  return true;
}

/**
 * Canonical lifecycle read boundary.
 *
 * The hook never mutates a transition locally. A visible state change appears
 * only after the loader/subscription returns a persisted server snapshot.
 */
export function useIntakeLifecycle({
  intakeId,
  enabled = true,
  initialSnapshot = null,
  loadSnapshot,
  subscribe,
  activeIntervalMs = DEFAULT_INTERVAL_MS,
  maxBackoffMs = DEFAULT_MAX_BACKOFF_MS,
}: UseIntakeLifecycleOptions): LifecycleRefreshState {
  const [snapshot, setSnapshot] = useState<IntakeLifecycleSnapshot | null>(initialSnapshot);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(enabled && initialSnapshot === null);
  const [refreshing, setRefreshing] = useState(false);
  const [consecutiveFailures, setConsecutiveFailures] = useState(0);
  const [lastRefreshedAt, setLastRefreshedAt] = useState<string | null>(
    initialSnapshot?.refreshed_at ?? null,
  );
  const [nextRefreshAt, setNextRefreshAt] = useState<string | null>(null);
  const [mode, setMode] = useState<LifecycleRefreshState["mode"]>(
    enabled ? (subscribe ? "SUBSCRIBED" : "POLLING") : "STOPPED",
  );

  const mountedRef = useRef(false);
  const snapshotRef = useRef<IntakeLifecycleSnapshot | null>(initialSnapshot);
  const loadSnapshotRef = useRef(loadSnapshot);
  const subscribeRef = useRef(subscribe);
  const visibleRef = useRef(true);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const requestRef = useRef<AbortController | null>(null);
  const refreshRef = useRef<(reason?: LifecycleRefreshReason) => Promise<void>>(
    async () => undefined,
  );
  const failureRef = useRef(0);
  const activeIntakeIdRef = useRef(intakeId);
  const subscriptionGenerationRef = useRef(0);

  const applySnapshot = useCallback(
    (next: IntakeLifecycleSnapshot, expectedIntakeId: string): boolean => {
      if (
        !mountedRef.current ||
        activeIntakeIdRef.current !== expectedIntakeId ||
        !isLifecycleSnapshotCurrent(next, snapshotRef.current, expectedIntakeId)
      ) {
        return false;
      }

      snapshotRef.current = next;
      setSnapshot(next);
      setError(null);
      failureRef.current = 0;
      setConsecutiveFailures(0);
      setLastRefreshedAt(next.refreshed_at);
      setLoading(false);
      setRefreshing(false);
      return true;
    },
    [],
  );

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setNextRefreshAt(null);
  }, []);

  const schedule = useCallback(
    (failures: number) => {
      clearTimer();
      if (!enabled || !visibleRef.current) return;
      const delay = lifecycleBackoffDelay(failures, activeIntervalMs, maxBackoffMs);
      setNextRefreshAt(new Date(Date.now() + delay).toISOString());
      timerRef.current = setTimeout(() => {
        void refreshRef.current(failures > 0 ? "SUBSCRIPTION_RECOVERY" : "POLL");
      }, delay);
    },
    [activeIntervalMs, clearTimer, enabled, maxBackoffMs],
  );

  const refresh = useCallback(
    async (reason: LifecycleRefreshReason = "MANUAL") => {
      if (!enabled || !visibleRef.current) return;
      const expectedIntakeId = intakeId;
      requestRef.current?.abort();
      const controller = new AbortController();
      requestRef.current = controller;
      setRefreshing(true);
      if (!snapshotRef.current) setLoading(true);

      try {
        const next = await loadSnapshotRef.current({ signal: controller.signal, reason });
        if (!mountedRef.current || controller.signal.aborted) return;
        applySnapshot(next, expectedIntakeId);
        setMode(subscribeRef.current ? "SUBSCRIBED" : "POLLING");
        schedule(0);
      } catch (loadError: unknown) {
        if (!mountedRef.current || controller.signal.aborted) return;
        const failures = failureRef.current + 1;
        failureRef.current = failures;
        setError(asError(loadError));
        setConsecutiveFailures(failures);
        setMode(subscribeRef.current ? "SUBSCRIBED" : "POLLING");
        schedule(failures);
      } finally {
        if (mountedRef.current && !controller.signal.aborted) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [applySnapshot, enabled, intakeId, schedule],
  );

  refreshRef.current = refresh;
  loadSnapshotRef.current = loadSnapshot;
  subscribeRef.current = subscribe;

  useEffect(() => {
    mountedRef.current = true;
    activeIntakeIdRef.current = intakeId;
    visibleRef.current = typeof document === "undefined" || document.visibilityState !== "hidden";
    const initialForIntake = initialSnapshot?.record.id === intakeId ? initialSnapshot : null;
    snapshotRef.current = initialForIntake;
    setSnapshot(initialForIntake);
    setLastRefreshedAt(initialForIntake?.refreshed_at ?? null);
    setError(null);
    failureRef.current = 0;
    setConsecutiveFailures(0);
    setLoading(enabled && initialForIntake === null);

    function handleVisibility() {
      const visible = document.visibilityState !== "hidden";
      visibleRef.current = visible;
      if (!visible) {
        clearTimer();
        requestRef.current?.abort();
        setRefreshing(false);
        setMode("HIDDEN");
        return;
      }
      setMode(subscribeRef.current ? "SUBSCRIBED" : "POLLING");
      void refreshRef.current("VISIBLE");
    }

    document.addEventListener("visibilitychange", handleVisibility);
    if (enabled && visibleRef.current) void refreshRef.current("INITIAL");

    const activeSubscribe = subscribe;
    const subscribedIntakeId = intakeId;
    const subscriptionGeneration = subscriptionGenerationRef.current + 1;
    subscriptionGenerationRef.current = subscriptionGeneration;
    const unsubscribe = enabled && activeSubscribe
      ? activeSubscribe({
          onSnapshot: (next) => {
            if (subscriptionGenerationRef.current !== subscriptionGeneration) return;
            if (!applySnapshot(next, subscribedIntakeId)) return;
            schedule(0);
          },
          onError: (subscriptionError) => {
            if (
              !mountedRef.current ||
              subscriptionGenerationRef.current !== subscriptionGeneration ||
              activeIntakeIdRef.current !== subscribedIntakeId
            ) {
              return;
            }
            const failures = failureRef.current + 1;
            failureRef.current = failures;
            setError(asError(subscriptionError));
            setConsecutiveFailures(failures);
            schedule(failures);
          },
        })
      : undefined;

    return () => {
      mountedRef.current = false;
      document.removeEventListener("visibilitychange", handleVisibility);
      unsubscribe?.();
      clearTimer();
      requestRef.current?.abort();
    };
  }, [
    applySnapshot,
    clearTimer,
    enabled,
    intakeId,
    schedule,
    subscribe,
  ]);

  useEffect(() => {
    if (!enabled) {
      clearTimer();
      requestRef.current?.abort();
      setMode("STOPPED");
      setLoading(false);
      setRefreshing(false);
    }
  }, [clearTimer, enabled]);

  return {
    snapshot,
    error,
    loading,
    refreshing,
    consecutiveFailures,
    lastRefreshedAt,
    nextRefreshAt,
    mode,
    refresh,
  };
}
