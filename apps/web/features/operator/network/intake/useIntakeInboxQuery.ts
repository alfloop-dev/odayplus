"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type SavedViewType = string;
export type ViewMode = "list" | "map";

export type IntakeInboxFilterState = {
  search: string;
  intakeMethod: string;
  intakeStage: string;
  matchOutcome: string;
  sourceId: string;
  submittedBy: string;
  owner: string;
  assignmentStatus: string;
  needsReview: string;
  slaState: string;
  heatZoneId: string;
  areaId: string;
  observedFrom: string;
  observedTo: string;
  updatedFrom: string;
  updatedTo: string;
  restrictedData: string;
  quarantined: string;
  failed: string;
  retryable: string;
  savedView: SavedViewType;
  viewMode: ViewMode;
  page: number;
  pageSize: number;
  cursor: string;
  sortBy: string;
  sortOrder: "asc" | "desc";
  selectedIntakeId: string | null;
};

const DEFAULT_FILTERS: IntakeInboxFilterState = {
  search: "",
  intakeMethod: "",
  intakeStage: "",
  matchOutcome: "",
  sourceId: "",
  submittedBy: "",
  owner: "",
  assignmentStatus: "",
  needsReview: "",
  slaState: "",
  heatZoneId: "",
  areaId: "",
  observedFrom: "",
  observedTo: "",
  updatedFrom: "",
  updatedTo: "",
  restrictedData: "",
  quarantined: "",
  failed: "",
  retryable: "",
  savedView: "",
  viewMode: "list",
  page: 1,
  pageSize: 10,
  cursor: "",
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
  if (params.has("sourceId")) result.sourceId = params.get("sourceId") ?? "";
  if (params.has("submittedBy")) result.submittedBy = params.get("submittedBy") ?? "";
  if (params.has("owner")) result.owner = params.get("owner") ?? "";
  if (params.has("assignmentStatus")) result.assignmentStatus = params.get("assignmentStatus") ?? "";
  if (params.has("needsReview")) result.needsReview = params.get("needsReview") ?? "";
  if (params.has("slaState")) result.slaState = params.get("slaState") ?? "";
  if (params.has("heatZoneId")) result.heatZoneId = params.get("heatZoneId") ?? "";
  if (params.has("areaId")) result.areaId = params.get("areaId") ?? "";
  if (params.has("observedFrom")) result.observedFrom = params.get("observedFrom") ?? "";
  if (params.has("observedTo")) result.observedTo = params.get("observedTo") ?? "";
  if (params.has("updatedFrom")) result.updatedFrom = params.get("updatedFrom") ?? "";
  if (params.has("updatedTo")) result.updatedTo = params.get("updatedTo") ?? "";
  if (params.has("restrictedData")) result.restrictedData = params.get("restrictedData") ?? "";
  if (params.has("quarantined")) result.quarantined = params.get("quarantined") ?? "";
  if (params.has("failed")) result.failed = params.get("failed") ?? "";
  if (params.has("retryable")) result.retryable = params.get("retryable") ?? "";
  if (params.has("savedView")) result.savedView = params.get("savedView") ?? "";
  if (params.has("viewMode")) result.viewMode = (params.get("viewMode") as ViewMode) ?? "list";

  const page = parseInt(params.get("page") ?? "", 10);
  if (!isNaN(page) && page > 0) result.page = page;

  const pageSize = parseInt(params.get("pageSize") ?? "", 10);
  if (!isNaN(pageSize) && pageSize > 0) result.pageSize = pageSize;

  if (params.has("cursor")) result.cursor = params.get("cursor") ?? "";
  if (params.has("sortBy")) result.sortBy = params.get("sortBy") ?? "updatedAt";
  if (params.has("sortOrder")) {
    const order = params.get("sortOrder");
    if (order === "asc" || order === "desc") result.sortOrder = order;
  }

  if (params.has("selected")) {
    result.selectedIntakeId = params.get("selected");
  } else if (window.location.hash.startsWith("#intake/")) {
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
  setOrDelete("sourceId", state.sourceId);
  setOrDelete("submittedBy", state.submittedBy);
  setOrDelete("owner", state.owner);
  setOrDelete("assignmentStatus", state.assignmentStatus);
  setOrDelete("needsReview", state.needsReview);
  setOrDelete("slaState", state.slaState);
  setOrDelete("heatZoneId", state.heatZoneId);
  setOrDelete("areaId", state.areaId);
  setOrDelete("observedFrom", state.observedFrom);
  setOrDelete("observedTo", state.observedTo);
  setOrDelete("updatedFrom", state.updatedFrom);
  setOrDelete("updatedTo", state.updatedTo);
  setOrDelete("restrictedData", state.restrictedData);
  setOrDelete("quarantined", state.quarantined);
  setOrDelete("failed", state.failed);
  setOrDelete("retryable", state.retryable);
  setOrDelete("savedView", state.savedView);
  setOrDelete("viewMode", state.viewMode);
  setOrDelete("page", state.page);
  setOrDelete("pageSize", state.pageSize);
  setOrDelete("cursor", state.cursor);
  setOrDelete("sortBy", state.sortBy);
  setOrDelete("sortOrder", state.sortOrder);
  setOrDelete("selected", state.selectedIntakeId);

  const newSearch = params.toString();
  const queryString = newSearch ? `?${newSearch}` : "";
  const newUrl = `${window.location.pathname}${queryString}`;

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
      if (
        "search" in updates ||
        "savedView" in updates ||
        "intakeMethod" in updates ||
        "intakeStage" in updates ||
        "matchOutcome" in updates ||
        "sourceId" in updates ||
        "submittedBy" in updates ||
        "owner" in updates ||
        "assignmentStatus" in updates ||
        "needsReview" in updates ||
        "slaState" in updates ||
        "heatZoneId" in updates ||
        "areaId" in updates ||
        "observedFrom" in updates ||
        "observedTo" in updates ||
        "updatedFrom" in updates ||
        "updatedTo" in updates ||
        "restrictedData" in updates ||
        "quarantined" in updates ||
        "failed" in updates ||
        "retryable" in updates ||
        "pageSize" in updates ||
        "sortBy" in updates ||
        "sortOrder" in updates
      ) {
        if (!("page" in updates)) {
          next.page = 1;
        }
        if (!("cursor" in updates)) {
          next.cursor = "";
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
        return {
          ...prev,
          cursor: "",
          page: 1,
          sortOrder: prev.sortOrder === "asc" ? "desc" : "asc",
        };
      }
      return { ...prev, cursor: "", page: 1, sortBy: column, sortOrder: "asc" };
    });
  }, []);

  return {
    filters,
    updateFilters,
    resetFilters,
    toggleSort,
  };
}
