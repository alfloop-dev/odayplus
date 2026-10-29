"use client";

import { createContext, createElement, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { bindCanonicalIntakeFetch, CanonicalIntakeReadError, type CanonicalIntakeReadContext } from "./canonicalIntakeTransport";
import { operatorSubjectId } from "../../operatorSecurityHeaders";

type Receipt = { audit_event_id: string; correlation_id: string };
type Captured = { source: symbol; description: string;
  send: (signal: AbortSignal) => Promise<{ receipt: Receipt; description: string }>;
  uncertain: boolean; receipt: Receipt | null; receiptDescription: string | null };
type Lifetime = { context: CanonicalIntakeReadContext; controller: AbortController; command: Captured | null;
  inFlight: boolean; error: { source: symbol; value: unknown } | null };

function useCommandOwner(context: CanonicalIntakeReadContext | null) {
  const [, render] = useState(0);
  const latestContext = useRef(context); latestContext.current = context;
  const lifetime = useRef<Lifetime | null>(null);
  const publish = () => render((value) => value + 1);
  useEffect(() => {
    const active: Lifetime | null = context ? { context, controller: new AbortController(), command: null,
      inFlight: false, error: null } : null;
    lifetime.current = active; publish();
    return () => { active?.controller.abort(); };
  }, [context]);
  const current = (active: Lifetime) => !active.controller.signal.aborted
    && latestContext.current === active.context && lifetime.current === active;
  const matches = (candidate: CanonicalIntakeReadContext) => {
    const owner = lifetime.current;
    return Boolean(owner && current(owner) && candidate.fetchImpl === owner.context.fetchImpl
      && candidate.contextKey === owner.context.contextKey && candidate.headers === owner.context.headers);
  };
  const active = lifetime.current?.context === context ? lifetime.current : null;

  async function execute(owner: Lifetime, captured: Captured) {
    if (!current(owner) || owner.inFlight || captured.receipt) return null;
    owner.inFlight = true; owner.error = null; publish();
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
          && [400, 401, 403, 404, 409, 412, 422, 428].includes(failure.status)) owner.command = null;
        else captured.uncertain = true;
      }
      return null;
    } finally { if (current(owner)) { owner.inFlight = false; publish(); } }
  }

  async function submit<P extends object, R extends Receipt>(source: symbol, candidate: CanonicalIntakeReadContext,
    prepare: () => P, send: (packet: P, signal: AbortSignal) => Promise<R>, describe: (packet: P) => string,
    summarize: (receipt: R) => string, onCapture?: () => void) {
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
        owner.command = { source, description: describe(packet),
          send: async (signal) => { const receipt = await send(packet, signal); return { receipt, description: summarize(receipt) }; },
          uncertain: false, receipt: null, receiptDescription: null };
      } catch (failure) { owner.error = { source, value: failure }; publish(); return null; }
      onCapture?.();
    }
    // A source token can only read the result of the sender captured above.
    return await execute(owner, owner.command) as R | null;
  }
  return { matches, submit, busy: Boolean(active?.inFlight), command: active?.command ?? null,
    error: active?.error ?? null,
    retry: async () => { const owner = lifetime.current; if (owner?.command) await execute(owner, owner.command); },
    dismissReceipt: () => {
      const owner = lifetime.current;
      if (owner && current(owner) && !owner.inFlight && owner.command?.receipt) { owner.command = null; owner.error = null; publish(); }
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
  const contextKey = context === null ? "" : createAuthenticatedReadGuard ? JSON.stringify([
    context.contextKey, operatorSubjectId(), typeof window === "undefined" ? null : window.sessionStorage.getItem("oday.operator.tenant"),
  ]) : context.contextKey;
  const raw = useMemo(() => context ? { fetchImpl: context.fetchImpl, contextKey, headers: context.headers } : null,
    [context?.fetchImpl, contextKey, context?.headers]);
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
  const owner = useCommandOwner(binding);
  return createElement(CommandOwner.Provider, { value: owner }, typeof children === "function" ? children(binding) : children);
}

export function useCanonicalIntakeCommandState() {
  const owner = useContext(CommandOwner);
  if (!owner) throw new Error("CANONICAL_COMMAND_OWNER_REQUIRED");
  return owner;
}

/** Forms present a captured command; they never own a second pending packet. */
export function useCanonicalIntakeCommand<P extends object, R extends Receipt>(context: CanonicalIntakeReadContext,
  send: (packet: P, signal: AbortSignal) => Promise<R>, describe: (packet: P) => string, summarize: (receipt: R) => string) {
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
      const value = await owner.submit(source, context, prepare, send, describe, summarize, onCapture);
      // Retain the owner's receipt without running a removed form's onCreated.
      return mounted.current && owner.matches(context) ? value : null;
    } };
}
