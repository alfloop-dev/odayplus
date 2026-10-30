"use client";

import { createContext, createElement, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { bindCanonicalIntakeFetch, CanonicalIntakeReadError, type CanonicalIntakeReadContext } from "./canonicalIntakeTransport";
import { readOperatorBrowserHints } from "../../operatorSecurityHeaders";
import { parseCanonicalCommandReceiptReference, readCanonicalCommandReceipt,
  type CanonicalCommandReceiptReference, type CanonicalCommandReceipt } from "./canonicalIntakeReadClient";

type Receipt = CanonicalCommandReceipt;
type Captured = { source: symbol; description: string;
  send?: (signal: AbortSignal) => Promise<{ receipt: Receipt; description: string }>;
  reference?: CanonicalCommandReceiptReference;
  notObserved: boolean;
  uncertain: boolean; receipt: Receipt | null; receiptDescription: string | null };
type Lifetime = { context: CanonicalIntakeReadContext; controller: AbortController; command: Captured | null;
  referenceKey: string | null; referenceBlocked: boolean; inFlight: boolean; probing: boolean; error: { source: symbol; value: unknown } | null };

function receiptDescription(reference: CanonicalCommandReceiptReference, receipt: Receipt): string {
  if (reference.kind === "url" && "processing_state" in receipt)
    return `收件 ${receipt.intake_id} · 版本 ${receipt.version} · ${receipt.processing_state}`;
  if (reference.kind === "case" && "candidate_ids" in receipt)
    return `收件 ${receipt.intake_id} · 版本 ${receipt.intake_version} · 比對案件 ${receipt.match_case_id} · 版本 ${receipt.version}`;
  if (reference.kind === "identity" && "decision_id" in receipt)
    return `比對案件 ${receipt.match_case_id} · 版本 ${receipt.match_case_version} · 決策 ${receipt.decision_id} · 版本 ${receipt.version} · ${receipt.status}`;
  if (reference.kind === "manual" && "request_id" in receipt)
    return `收件 ${receipt.intake_id} · 版本 ${receipt.version} · 補錄提案 ${receipt.request_id} · ${receipt.status}`;
  throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
}

function referenceKey(context: CanonicalIntakeReadContext): string | null {
  const scope = context.receiptOwner;
  if (!scope) return null;
  if (!scope.subject.trim() || scope.subject.length > 512 || !scope.roleId.trim() || scope.roleId.length > 128
    || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(scope.tenantId))
    throw new Error("CANONICAL_RECEIPT_SCOPE_INVALID");
  return "oday.canonicalIntake.receipt.v1:" + JSON.stringify([scope.subject, scope.tenantId, scope.roleId]);
}

function clearReference(owner: Lifetime, command: Captured) {
  if (!owner.referenceKey || !command.reference) return;
  try {
    // A late response/dismissal cannot clear a newer reference or another
    // account's entry. A failed cleanup retains metadata, never a raw packet.
    if (window.sessionStorage.getItem(owner.referenceKey) === JSON.stringify(command.reference))
      window.sessionStorage.removeItem(owner.referenceKey);
  } catch { /* Preserve the original result if browser storage is unavailable. */ }
}

function useCommandOwner(context: CanonicalIntakeReadContext | null, requireReference: boolean, scopeHintsUnavailable: boolean) {
  const [, render] = useState(0);
  const latestContext = useRef(context); latestContext.current = context;
  const lifetime = useRef<Lifetime | null>(null);
  const initialBinding = useRef<CanonicalIntakeReadContext | null>(null);
  const publish = () => render((value) => value + 1);
  useEffect(() => {
    const active: Lifetime | null = context ? { context, controller: new AbortController(), command: null, referenceKey: null, referenceBlocked: false,
      inFlight: false, probing: false, error: null } : null;
    lifetime.current = active;
    if (active) {
      try {
        if (scopeHintsUnavailable) throw new Error("CANONICAL_RECEIPT_SCOPE_UNAVAILABLE");
        active.referenceKey = referenceKey(active.context);
        if (requireReference && !active.referenceKey) throw new Error("CANONICAL_RECEIPT_SCOPE_REQUIRED");
        const saved = active.referenceKey ? window.sessionStorage.getItem(active.referenceKey) : null;
        if (saved !== null) {
          const reference = saved.length <= 2048 ? parseCanonicalCommandReceiptReference(JSON.parse(saved)) : null;
          if (!reference) throw new Error("CANONICAL_RECEIPT_REFERENCE_INVALID");
          if (initialBinding.current && initialBinding.current !== active.context) active.referenceBlocked = true;
          else active.command = { source: Symbol("restored canonical receipt"), reference,
            description: reference.kind === "url" ? "先前網址送件" : reference.kind === "identity"
              ? `案件 ${reference.matchCaseId} 的先前操作` : `收件 ${reference.intakeId} 的先前操作`,
            notObserved: false, uncertain: true, receipt: null, receiptDescription: null };
        }
        // StrictMode can repeat setup for this same binding. A later account,
        // persona or hint lease must not resurrect the previous command. Its
        // stored reference still blocks replacement until a fresh owner reads it.
        initialBinding.current ??= active.context;
      } catch (value) {
        const source = Symbol("unavailable canonical receipt reference");
        active.command = { source, description: "先前操作識別目前無法核對，請重新讀取登入狀態。",
          notObserved: false, uncertain: true, receipt: null, receiptDescription: null };
        active.error = { source, value };
      }
    }
    publish();
    if (active?.command?.reference) void probeReceipt();
    return () => { active?.controller.abort(); };
  }, [context, requireReference, scopeHintsUnavailable]);
  const current = (active: Lifetime) => !active.controller.signal.aborted
    && latestContext.current === active.context && lifetime.current === active;
  const matches = (candidate: CanonicalIntakeReadContext) => {
    const owner = lifetime.current;
    return Boolean(owner && current(owner) && !owner.referenceBlocked && candidate.fetchImpl === owner.context.fetchImpl
      && candidate.contextKey === owner.context.contextKey && candidate.headers === owner.context.headers
      && candidate.receiptOwner === owner.context.receiptOwner);
  };
  const active = lifetime.current?.context === context ? lifetime.current : null;

  async function execute(owner: Lifetime, captured: Captured) {
    if (!current(owner) || owner.inFlight || captured.receipt || !captured.send) return null;
    owner.inFlight = true; owner.probing = false; owner.error = null; captured.notObserved = false; publish();
    try {
      const value = await captured.send(owner.controller.signal);
      if (!current(owner)) return null;
      captured.receipt = value.receipt; captured.receiptDescription = value.description;
      return value.receipt;
    } catch (failure) {
      if (current(owner)) {
        owner.error = { source: captured.source, value: failure };
        // Initial explicit HTTP rejections leave the transactional API without
        // a committed command. After an uncertain attempt, even a 403 may only
        // hide its historical receipt during current reauthorization.
        if (!captured.uncertain && failure instanceof CanonicalIntakeReadError
          && [400, 401, 403, 404, 409, 412, 422, 428].includes(failure.status)) {
          clearReference(owner, captured); owner.command = null;
        }
        else captured.uncertain = true;
      }
      return null;
    } finally { if (current(owner)) { owner.inFlight = false; publish(); } }
  }

  async function submit<P extends object, R extends Receipt>(source: symbol, candidate: CanonicalIntakeReadContext,
    prepare: () => P, send: (packet: P, signal: AbortSignal) => Promise<R>, describe: (packet: P) => string,
    referenceFor: (packet: P) => CanonicalCommandReceiptReference, onCapture?: () => void) {
    const owner = lifetime.current;
    if (!owner || !matches(candidate) || !current(owner) || owner.inFlight) return null;
    if (owner.command?.receipt) {
      if (owner.command.source === source) return null;
      owner.command = null;
    }
    if (owner.command && owner.command.source !== source) return null;
    if (!owner.command) {
      try {
        const packet = structuredClone(prepare());
        const reference = parseCanonicalCommandReceiptReference(referenceFor(packet));
        if (!reference) throw new Error("CANONICAL_RECEIPT_REFERENCE_INVALID");
        const description = describe(packet);
        // Save only the closed reference before the POST can leave. Storage
        // failure must not send an operation whose key cannot be recovered.
        if (owner.referenceKey) window.sessionStorage.setItem(owner.referenceKey, JSON.stringify(reference));
        owner.command = { source, description, reference,
          send: async (signal) => { const receipt = await send(packet, signal);
            return { receipt, description: receiptDescription(reference, receipt) }; },
          notObserved: false, uncertain: false, receipt: null, receiptDescription: null };
      } catch (failure) { owner.error = { source, value: failure }; publish(); return null; }
      onCapture?.();
    }
    // A source token can only read the result of the sender captured above.
    return await execute(owner, owner.command) as R | null;
  }

  async function probeReceipt() {
    const owner = lifetime.current, captured = owner?.command;
    if (!owner || !current(owner) || owner.inFlight || !captured?.reference || captured.receipt) return;
    owner.inFlight = true; owner.probing = true; owner.error = null; captured.notObserved = false; publish();
    try {
      const observed = await readCanonicalCommandReceipt(owner.context, captured.reference, owner.controller.signal);
      if (!current(owner) || owner.command !== captured) return;
      if (observed.observation === "RECEIPT_COMMITTED") {
        captured.receipt = observed.receipt; captured.receiptDescription = receiptDescription(captured.reference, observed.receipt);
      } else {
        // Absence is one authorized observation, not proof of nonexecution or
        // permission to discard the frozen packet or issue a fresh key.
        captured.notObserved = true;
      }
    } catch (failure) {
      if (current(owner) && owner.command === captured) owner.error = { source: captured.source, value: failure };
    } finally { if (current(owner)) { owner.inFlight = false; owner.probing = false; publish(); } }
  }
  return { matches, submit, busy: Boolean(active?.inFlight), command: active?.command ?? null,
    referenceBlocked: Boolean(active?.referenceBlocked),
    probing: Boolean(active?.probing),
    error: active?.error ?? null,
    probeReceipt,
    retry: async () => { const owner = lifetime.current; if (owner?.command) await execute(owner, owner.command); },
    dismissReceipt: () => {
      const owner = lifetime.current;
      if (owner && current(owner) && !owner.inFlight && owner.command?.receipt) {
        clearReference(owner, owner.command); owner.command = null; owner.error = null; publish();
      }
    } };
}

const CommandOwner = createContext<ReturnType<typeof useCommandOwner> | null>(null);

/** One Console account-bound owner survives workspace, dialog and tab presentation. */
export function CanonicalIntakeCommandProvider({ context, createAuthenticatedReadGuard, children }: {
  context: CanonicalIntakeReadContext | null;
  createAuthenticatedReadGuard?: (current: () => boolean) => () => Promise<boolean>;
  children: ReactNode | ((binding: CanonicalIntakeReadContext | null) => ReactNode);
}) {
  // Browser hints can invalidate the production binding, never grant access.
  let contextKey = context?.contextKey ?? "";
  let scopeHintsUnavailable = false;
  if (context && createAuthenticatedReadGuard) {
    try {
      const hints = readOperatorBrowserHints();
      contextKey = JSON.stringify([context.contextKey, hints.subject, hints.tenant]);
    } catch {
      scopeHintsUnavailable = true;
      contextKey = JSON.stringify([context.contextKey, "browser scope hints unavailable"]);
    }
  }
  const raw = useMemo(() => context ? { fetchImpl: context.fetchImpl, contextKey, headers: context.headers,
    receiptOwner: context.receiptOwner } : null,
    [context?.fetchImpl, contextKey, context?.headers, context?.receiptOwner]);
  const lease = useRef({ raw, factory: createAuthenticatedReadGuard, mounted: true });
  lease.current.raw = raw; lease.current.factory = createAuthenticatedReadGuard;
  useEffect(() => {
    lease.current.mounted = true;
    return () => { lease.current.mounted = false; };
  }, []);
  const binding = useMemo(() => {
    if (!raw) return null;
    const current = createAuthenticatedReadGuard?.(() => lease.current.mounted && lease.current.raw === raw
      && lease.current.factory === createAuthenticatedReadGuard);
    return current ? { ...raw, fetchImpl: bindCanonicalIntakeFetch(raw.fetchImpl, current) } : raw;
  }, [raw, createAuthenticatedReadGuard]);
  const owner = useCommandOwner(binding, Boolean(createAuthenticatedReadGuard), scopeHintsUnavailable);
  return createElement(CommandOwner.Provider, { value: owner }, typeof children === "function" ? children(binding) : children);
}

export function useCanonicalIntakeCommandState() {
  const owner = useContext(CommandOwner);
  if (!owner) throw new Error("CANONICAL_COMMAND_OWNER_REQUIRED");
  return owner;
}

/** Forms present a captured command; they never own a second pending packet. */
export function useCanonicalIntakeCommand<P extends object, R extends Receipt>(context: CanonicalIntakeReadContext,
  send: (packet: P, signal: AbortSignal) => Promise<R>, describe: (packet: P) => string,
  referenceFor: (packet: P) => CanonicalCommandReceiptReference) {
  const owner = useCanonicalIntakeCommandState();
  const source = useRef(Symbol("canonical command form")).current;
  const mounted = useRef(false);
  // Read-only presentation of this form's validated result, never another
  // command packet. Dismissing the workspace summary cannot re-enable it.
  const presented = useRef<{ context: CanonicalIntakeReadContext; receipt: R } | null>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const bound = owner.matches(context);
  const own = bound && owner.command?.source === source;
  if (presented.current && !owner.matches(presented.current.context)) presented.current = null;
  if (own && owner.command?.receipt) presented.current = { context, receipt: owner.command.receipt as R };
  return { busy: bound && owner.busy, blocked: !bound || Boolean(owner.command && !owner.command.receipt && !own),
    error: bound && owner.error?.source === source ? owner.error.value : null,
    receipt: bound ? presented.current?.receipt ?? null : null, hasPending: own && owner.command !== null,
    submit: async (prepare: () => P, onCapture?: () => void) => {
      if (!mounted.current || presented.current) return null;
      const value = await owner.submit(source, context, prepare, send, describe, referenceFor, onCapture);
      // Retain the owner's receipt without running a removed form's onCreated.
      return mounted.current && owner.matches(context) ? value : null;
    } };
}
