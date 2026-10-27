"use client";

import { useEffect, useRef, useState } from "react";
import type { CanonicalIntakeReadContext } from "./canonicalIntakeReadClient";

/** One captured request lifecycle for native submission, entry and decisions. */
export function useCanonicalIntakeCommand<P extends object, R>(context: CanonicalIntakeReadContext,
  send: (packet: P, signal: AbortSignal) => Promise<R>, onBusyChange: (busy: boolean) => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [receipt, setReceipt] = useState<R | null>(null);
  const latestContext = useRef(context);
  latestContext.current = context;
  const lifetime = useRef<{ context: CanonicalIntakeReadContext; controller: AbortController } | null>(null);
  const pending = useRef<{ packet: P; send: typeof send } | null>(null);
  const inFlight = useRef(false);
  useEffect(() => {
    const controller = new AbortController(); lifetime.current = { context, controller };
    pending.current = null; inFlight.current = false;
    setBusy(false); setError(null); setReceipt(null);
    return () => { controller.abort(); onBusyChange(false); };
  }, [context, onBusyChange]);
  async function submit(prepare: () => P, onCapture?: () => void) {
    const active = lifetime.current;
    if (!active || active.context !== context || inFlight.current || receipt || active.controller.signal.aborted) return null;
    const current = () => !active.controller.signal.aborted && latestContext.current === active.context;
    if (!pending.current) {
      try { pending.current = { packet: structuredClone(prepare()), send }; }
      catch (failure) { setError(failure); return null; }
      onCapture?.();
    }
    const captured = pending.current;
    inFlight.current = true; setBusy(true); setError(null); onBusyChange(true);
    try { const value = await captured.send(captured.packet, active.controller.signal); if (current()) { setReceipt(value); return value; } return null; }
    catch (failure) { if (current()) setError(failure); return null; }
    finally { if (current()) { inFlight.current = false; setBusy(false); onBusyChange(false); } }
  }
  const bound = lifetime.current?.context === context;
  return { busy: bound && busy, error: bound ? error : null, receipt: bound ? receipt : null,
    hasPending: bound && pending.current !== null, submit };
}
