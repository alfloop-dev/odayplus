"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { DEFAULT_INBOX_FILTERS, INBOX_FILTER_KEYS, parseInboxUrlState, serializeInboxUrlState, type IntakeInboxFilterState } from "./inboxUrlState";
export type { IntakeInboxFilterState, SavedViewType, ViewMode } from "./inboxUrlState";

function currentFilters() {
  return typeof window === "undefined" ? { ...DEFAULT_INBOX_FILTERS }
    : parseInboxUrlState(new URLSearchParams(window.location.search), window.location.hash);
}

type ControlledQuery = { filters: IntakeInboxFilterState; onChange: (next: IntakeInboxFilterState) => void };

export function useIntakeInboxQuery(controlled?: ControlledQuery) {
  const isControlled = controlled !== undefined;
  const [local, setLocal] = useState(currentFilters);
  const filters = controlled?.filters ?? local;
  const signature = JSON.stringify(filters);
  const previous = useRef(signature);
  const current = useRef(filters);
  if (previous.current !== signature) { current.current = filters; previous.current = signature; }

  useEffect(() => {
    if (isControlled) return;
    const restore = () => { const value = currentFilters(); current.current = value; setLocal(value); };
    window.addEventListener("popstate", restore);
    window.addEventListener("hashchange", restore);
    return () => { window.removeEventListener("popstate", restore); window.removeEventListener("hashchange", restore); };
  }, [isControlled]);

  const commit = useCallback((next: IntakeInboxFilterState) => {
    current.current = next;
    if (controlled) { controlled.onChange(next); return; }
    setLocal(next);
    const params = serializeInboxUrlState(next, new URLSearchParams(window.location.search));
    const search = params.toString();
    const href = `${window.location.pathname}${search ? `?${search}` : ""}${window.location.hash}`;
    if (href !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
      window.history.pushState(window.history.state, "", href);
    }
  }, [controlled]);

  const updateFilters = useCallback((updates: Partial<IntakeInboxFilterState>) => {
    const next = { ...current.current, ...updates };
    if (INBOX_FILTER_KEYS.some((key) => key in updates) && !("page" in updates)) next.page = 1;
    commit(next);
  }, [commit]);
  const resetFilters = useCallback(() => commit({ ...DEFAULT_INBOX_FILTERS,
    viewMode: current.current.viewMode, selectedIntakeId: current.current.selectedIntakeId }), [commit]);
  const toggleSort = useCallback((column: string) => commit({ ...current.current, page: 1, sortBy: column,
    sortOrder: current.current.sortBy === column && current.current.sortOrder === "asc" ? "desc" : "asc" }), [commit]);
  return { filters, updateFilters, resetFilters, toggleSort };
}
