"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type SavedViewType = "all" | "needsReview" | "awaitingEntry" | "blocked" | "processing" | "ready";
export type ViewMode = "list" | "map";

export type IntakeInboxFilterState = {
  search: string;
  intakeMethod: string;
  intakeStage: string;
  matchOutcome: string;
  slaState: string;
  heatZoneId: string;
  savedView: SavedViewType;
  viewMode: ViewMode;
  page: number;
  pageSize: number;
  sortBy: string;
  sortOrder: "asc" | "desc";
  selectedIntakeId: string | null;
};

const DEFAULT_FILTERS: IntakeInboxFilterState = {
  search: "",
  intakeMethod: "",
  intakeStage: "",
  matchOutcome: "",
  slaState: "",
  heatZoneId: "",
  savedView: "all",
  viewMode: "list",
  page: 1,
  pageSize: 10,
  sortBy: "updatedAt",
  sortOrder: "desc",
  selectedIntakeId: null,
};

function parseUrlQueryParams(): Partial<IntakeInboxFilterState> {
  if (typeof window === "undefined") return {};
  const params = new URLSearchParams(window.location.search);
  const result: Partial<IntakeInboxFilterState> = {};

  if (params.has("search")) result.search = params.get("search") ?? "";
  if (params.has("intakeMethod")) result.intakeMethod = params.get("intakeMethod") ?? "";
  if (params.has("intakeStage")) result.intakeStage = params.get("intakeStage") ?? "";
  if (params.has("matchOutcome")) result.matchOutcome = params.get("matchOutcome") ?? "";
  if (params.has("slaState")) result.slaState = params.get("slaState") ?? "";
  if (params.has("heatZoneId")) result.heatZoneId = params.get("heatZoneId") ?? "";
  if (params.has("savedView")) result.savedView = (params.get("savedView") as SavedViewType) ?? "all";
  if (params.has("viewMode")) result.viewMode = (params.get("viewMode") as ViewMode) ?? "list";

  const page = parseInt(params.get("page") ?? "", 10);
  if (!isNaN(page) && page > 0) result.page = page;

  const pageSize = parseInt(params.get("pageSize") ?? "", 10);
  if (!isNaN(pageSize) && pageSize > 0) result.pageSize = pageSize;

  if (params.has("sortBy")) result.sortBy = params.get("sortBy") ?? "updatedAt";
  if (params.has("sortOrder")) {
    const order = params.get("sortOrder");
    if (order === "asc" || order === "desc") result.sortOrder = order;
  }

  if (window.location.hash.startsWith("#intake/")) {
    result.selectedIntakeId = window.location.hash.replace("#intake/", "");
  }

  return result;
}

function updateUrlQueryParams(state: IntakeInboxFilterState, mode: "push" | "replace") {
  if (typeof window === "undefined") return;
  const params = new URLSearchParams(window.location.search);

  const setOrDelete = (key: string, val: string | number | null) => {
    if (val !== null && val !== "" && val !== DEFAULT_FILTERS[key as keyof IntakeInboxFilterState]) {
      params.set(key, String(val));
    } else {
      params.delete(key);
    }
  };

  setOrDelete("search", state.search);
  setOrDelete("intakeMethod", state.intakeMethod);
  setOrDelete("intakeStage", state.intakeStage);
  setOrDelete("matchOutcome", state.matchOutcome);
  setOrDelete("slaState", state.slaState);
  setOrDelete("heatZoneId", state.heatZoneId);
  setOrDelete("savedView", state.savedView);
  setOrDelete("viewMode", state.viewMode);
  setOrDelete("page", state.page);
  setOrDelete("pageSize", state.pageSize);
  setOrDelete("sortBy", state.sortBy);
  setOrDelete("sortOrder", state.sortOrder);

  const newSearch = params.toString();
  const queryString = newSearch ? `?${newSearch}` : "";
  const hashString = state.selectedIntakeId ? `#intake/${state.selectedIntakeId}` : "";
  const newUrl = `${window.location.pathname}${queryString}${hashString}`;

  if (newUrl !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
    window.history[mode === "push" ? "pushState" : "replaceState"](null, "", newUrl);
  }
}

export function useIntakeInboxQuery() {
  const [filters, setFilters] = useState<IntakeInboxFilterState>(() => ({
    ...DEFAULT_FILTERS,
    ...parseUrlQueryParams(),
  }));
  const restoringFromHistory = useRef(false);
  const hasSyncedInitialState = useRef(false);

  // Direct open & back/forward URL restoration support
  useEffect(() => {
    function handlePopState() {
      restoringFromHistory.current = true;
      const parsed = parseUrlQueryParams();
      setFilters({ ...DEFAULT_FILTERS, ...parsed });
    }
    window.addEventListener("popstate", handlePopState);
    window.addEventListener("hashchange", handlePopState);
    return () => {
      window.removeEventListener("popstate", handlePopState);
      window.removeEventListener("hashchange", handlePopState);
    };
  }, []);

  // Sync state to URL
  useEffect(() => {
    if (!hasSyncedInitialState.current) {
      hasSyncedInitialState.current = true;
      updateUrlQueryParams(filters, "replace");
      return;
    }
    if (restoringFromHistory.current) {
      restoringFromHistory.current = false;
      return;
    }
    updateUrlQueryParams(filters, "push");
  }, [filters]);

  const updateFilters = useCallback((updates: Partial<IntakeInboxFilterState>) => {
    setFilters((current) => {
      const next = { ...current, ...updates };
      // Reset page to 1 on filter changes unless page is explicitly set
      if ("search" in updates || "savedView" in updates || "intakeMethod" in updates ||
          "intakeStage" in updates || "matchOutcome" in updates || "slaState" in updates ||
          "heatZoneId" in updates) {
        if (!("page" in updates)) {
          next.page = 1;
        }
      }
      return next;
    });
  }, []);

  const resetFilters = useCallback(() => {
    setFilters({
      ...DEFAULT_FILTERS,
      viewMode: filters.viewMode,
    });
  }, [filters.viewMode]);

  const toggleSort = useCallback((column: string) => {
    setFilters((prev) => {
      if (prev.sortBy === column) {
        return { ...prev, sortOrder: prev.sortOrder === "asc" ? "desc" : "asc" };
      }
      return { ...prev, sortBy: column, sortOrder: "asc" };
    });
  }, []);

  return {
    filters,
    updateFilters,
    resetFilters,
    toggleSort,
  };
}
