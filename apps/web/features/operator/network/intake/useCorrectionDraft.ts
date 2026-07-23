"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const DRAFT_SCHEMA_VERSION = 1;
const DRAFT_NAMESPACE = "oday-plus:intake-draft";

export type DraftValue = string | number | boolean | null;
export type CorrectionDraftFields = Record<string, DraftValue>;
export type CorrectionDraftStatus =
  | "CLEAN"
  | "DIRTY"
  | "SUBMITTING"
  | "FAILED"
  | "CONFLICT";

export type CorrectionDraftIdentity = {
  tenantId: string;
  intakeId: string;
  actorSubjectId: string;
  purpose: "assisted-entry" | "correction";
  fieldPath?: string;
};

export type CorrectionDraftFailure = {
  code: string;
  summary: string;
  occurredAt: string;
  retryable: boolean;
  currentVersion?: number | null;
  currentState?: string | null;
  correlationId?: string | null;
};

export type CorrectionDraftRecord<TFields extends CorrectionDraftFields> = {
  schemaVersion: typeof DRAFT_SCHEMA_VERSION;
  operationId: string;
  fields: TFields;
  reason: string;
  riskAcknowledged: boolean;
  baseVersion: number | null;
  status: CorrectionDraftStatus;
  dirty: boolean;
  updatedAt: string;
  lastFailure: CorrectionDraftFailure | null;
};

export interface DraftStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export type UseCorrectionDraftOptions<TFields extends CorrectionDraftFields> = {
  identity: CorrectionDraftIdentity | null;
  initialFields: TFields;
  baseVersion?: number | null;
  enabled?: boolean;
  storage?: DraftStorage;
};

export type CorrectionDraftController<TFields extends CorrectionDraftFields> = {
  draft: CorrectionDraftRecord<TFields>;
  storageKey: string | null;
  persistenceAvailable: boolean;
  setField: <TKey extends keyof TFields>(key: TKey, value: TFields[TKey]) => void;
  replaceFields: (fields: TFields) => void;
  setReason: (reason: string) => void;
  setRiskAcknowledged: (acknowledged: boolean) => void;
  markSubmitting: () => void;
  markFailure: (failure: CorrectionDraftFailure, conflict?: boolean) => void;
  rebase: (baseVersion: number) => void;
  clearAfterCommit: () => void;
  discard: () => void;
};

export function buildCorrectionDraftStorageKey(identity: CorrectionDraftIdentity): string {
  const parts = [
    identity.tenantId,
    identity.intakeId,
    identity.actorSubjectId,
    identity.purpose,
    identity.fieldPath ?? "all-fields",
  ].map((part) => encodeURIComponent(part));
  return `${DRAFT_NAMESPACE}:v${DRAFT_SCHEMA_VERSION}:${parts.join(":")}`;
}

export function useCorrectionDraft<TFields extends CorrectionDraftFields>({
  identity,
  initialFields,
  baseVersion = null,
  enabled = true,
  storage,
}: UseCorrectionDraftOptions<TFields>): CorrectionDraftController<TFields> {
  const storageKey = useMemo(
    () => (enabled && identity ? buildCorrectionDraftStorageKey(identity) : null),
    [enabled, identity],
  );
  const resolvedStorage = resolveStorage(enabled, storage);
  const initialRef = useRef(initialFields);
  const skipNextSaveRef = useRef(false);
  const [draft, setDraft] = useState<CorrectionDraftRecord<TFields>>(() =>
    loadDraft(resolvedStorage, storageKey, initialFields, baseVersion),
  );

  useEffect(() => {
    initialRef.current = initialFields;
  }, [initialFields]);

  useEffect(() => {
    skipNextSaveRef.current = true;
    setDraft(loadDraft(resolvedStorage, storageKey, initialFields, baseVersion));
  }, [baseVersion, initialFields, resolvedStorage, storageKey]);

  useEffect(() => {
    if (!resolvedStorage || !storageKey) return;
    if (skipNextSaveRef.current) {
      skipNextSaveRef.current = false;
      return;
    }
    try {
      if (!draft.dirty && draft.status === "CLEAN") {
        resolvedStorage.removeItem(storageKey);
      } else {
        resolvedStorage.setItem(storageKey, JSON.stringify(draft));
      }
    } catch {
      // The form remains usable when storage is denied or full. The caller can
      // expose persistenceAvailable=false, but draft input must not be dropped.
    }
  }, [draft, resolvedStorage, storageKey]);

  useEffect(() => {
    if (!storageKey || typeof window === "undefined" || resolvedStorage !== window.localStorage) {
      return;
    }
    const onStorage = (event: StorageEvent) => {
      if (event.key !== storageKey || !event.newValue) return;
      const parsed = parseDraft<TFields>(event.newValue);
      if (parsed) setDraft(normalizeInterruptedSubmission(parsed));
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [resolvedStorage, storageKey]);

  const update = useCallback(
    (apply: (current: CorrectionDraftRecord<TFields>) => CorrectionDraftRecord<TFields>) => {
      setDraft((current) => ({
        ...apply(current),
        dirty: true,
        updatedAt: new Date().toISOString(),
      }));
    },
    [],
  );

  const setField = useCallback(
    <TKey extends keyof TFields>(key: TKey, value: TFields[TKey]) => {
      update((current) => ({
        ...current,
        fields: { ...current.fields, [key]: value },
        status: "DIRTY",
        lastFailure: null,
      }));
    },
    [update],
  );

  const replaceFields = useCallback(
    (fields: TFields) => {
      update((current) => ({
        ...current,
        fields,
        status: "DIRTY",
        lastFailure: null,
      }));
    },
    [update],
  );

  const setReason = useCallback(
    (reason: string) => {
      update((current) => ({ ...current, reason, status: "DIRTY", lastFailure: null }));
    },
    [update],
  );

  const setRiskAcknowledged = useCallback(
    (riskAcknowledged: boolean) => {
      update((current) => ({
        ...current,
        riskAcknowledged,
        status: "DIRTY",
        lastFailure: null,
      }));
    },
    [update],
  );

  const markSubmitting = useCallback(() => {
    update((current) => ({ ...current, status: "SUBMITTING", lastFailure: null }));
  }, [update]);

  const markFailure = useCallback(
    (failure: CorrectionDraftFailure, conflict = false) => {
      update((current) => ({
        ...current,
        status: conflict ? "CONFLICT" : "FAILED",
        lastFailure: failure,
      }));
    },
    [update],
  );

  const rebase = useCallback(
    (nextBaseVersion: number) => {
      update((current) => ({
        ...current,
        baseVersion: nextBaseVersion,
        status: "DIRTY",
        lastFailure: null,
      }));
    },
    [update],
  );

  const clear = useCallback(() => {
    if (resolvedStorage && storageKey) {
      try {
        resolvedStorage.removeItem(storageKey);
      } catch {
        // The in-memory reset below is still authoritative for this component.
      }
    }
    setDraft(createDraft(initialRef.current, baseVersion));
  }, [baseVersion, resolvedStorage, storageKey]);

  return {
    draft,
    storageKey,
    persistenceAvailable: Boolean(resolvedStorage && storageKey),
    setField,
    replaceFields,
    setReason,
    setRiskAcknowledged,
    markSubmitting,
    markFailure,
    rebase,
    clearAfterCommit: clear,
    discard: clear,
  };
}

function resolveStorage(enabled: boolean, injected?: DraftStorage): DraftStorage | null {
  if (!enabled) return null;
  if (injected) return injected;
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function loadDraft<TFields extends CorrectionDraftFields>(
  storage: DraftStorage | null,
  storageKey: string | null,
  initialFields: TFields,
  baseVersion: number | null,
): CorrectionDraftRecord<TFields> {
  if (storage && storageKey) {
    try {
      const raw = storage.getItem(storageKey);
      const parsed = raw ? parseDraft<TFields>(raw) : null;
      if (parsed) return normalizeInterruptedSubmission(parsed);
    } catch {
      // Fall through to a clean in-memory draft.
    }
  }
  return createDraft(initialFields, baseVersion);
}

function parseDraft<TFields extends CorrectionDraftFields>(
  raw: string,
): CorrectionDraftRecord<TFields> | null {
  try {
    const value = JSON.parse(raw) as Partial<CorrectionDraftRecord<TFields>>;
    if (
      value.schemaVersion !== DRAFT_SCHEMA_VERSION ||
      typeof value.operationId !== "string" ||
      !value.fields ||
      typeof value.fields !== "object" ||
      typeof value.reason !== "string" ||
      typeof value.riskAcknowledged !== "boolean" ||
      typeof value.updatedAt !== "string" ||
      typeof value.dirty !== "boolean" ||
      !isDraftStatus(value.status)
    ) {
      return null;
    }
    return value as CorrectionDraftRecord<TFields>;
  } catch {
    return null;
  }
}

function normalizeInterruptedSubmission<TFields extends CorrectionDraftFields>(
  draft: CorrectionDraftRecord<TFields>,
): CorrectionDraftRecord<TFields> {
  if (draft.status !== "SUBMITTING") return draft;
  return {
    ...draft,
    status: "FAILED",
    lastFailure: {
      code: "SUBMISSION_RESULT_UNKNOWN",
      summary: "上次送出結果尚未確認；草稿與 operation ID 已保留。",
      occurredAt: new Date().toISOString(),
      retryable: true,
    },
  };
}

function createDraft<TFields extends CorrectionDraftFields>(
  initialFields: TFields,
  baseVersion: number | null,
): CorrectionDraftRecord<TFields> {
  return {
    schemaVersion: DRAFT_SCHEMA_VERSION,
    operationId: createOperationId(),
    fields: { ...initialFields },
    reason: "",
    riskAcknowledged: false,
    baseVersion,
    status: "CLEAN",
    dirty: false,
    updatedAt: new Date().toISOString(),
    lastFailure: null,
  };
}

function createOperationId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `draft-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function isDraftStatus(value: unknown): value is CorrectionDraftStatus {
  return (
    value === "CLEAN" ||
    value === "DIRTY" ||
    value === "SUBMITTING" ||
    value === "FAILED" ||
    value === "CONFLICT"
  );
}
