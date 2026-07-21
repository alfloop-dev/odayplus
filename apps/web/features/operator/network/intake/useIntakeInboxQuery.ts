"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { AssistedIntake, IntakeStage, MatchOutcome } from "@oday-plus/openapi-client";
import { queueCounts } from "./intakeTypes";

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

function updateUrlQueryParams(state: IntakeInboxFilterState) {
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
    window.history.replaceState(null, "", newUrl);
  }
}

export function useIntakeInboxQuery(records: AssistedIntake[]) {
  const [filters, setFilters] = useState<IntakeInboxFilterState>(() => ({
    ...DEFAULT_FILTERS,
    ...parseUrlQueryParams(),
  }));

  // Direct open & back/forward URL restoration support
  useEffect(() => {
    function handlePopState() {
      const parsed = parseUrlQueryParams();
      setFilters((current) => ({
        ...current,
        ...parsed,
      }));
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
    updateUrlQueryParams(filters);
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

  // Filter records
  const filteredRecords = useMemo(() => {
    return records.filter((r) => {
      // Saved views
      if (filters.savedView === "needsReview" && r.stage !== "NEEDS_REVIEW") return false;
      if (filters.savedView === "awaitingEntry" && r.stage !== "AWAITING_ASSISTED_ENTRY") return false;
      if (filters.savedView === "blocked" && r.stage !== "QUARANTINED" && r.stage !== "FAILED") return false;
      if (filters.savedView === "processing" && ["NEEDS_REVIEW", "READY", "QUARANTINED", "FAILED", "CANCELLED"].includes(r.stage)) return false;
      if (filters.savedView === "ready" && r.stage !== "READY") return false;

      // Method filter
      if (filters.intakeMethod && (r.policyLabel || "").indexOf(filters.intakeMethod) === -1) return false;

      // Stage filter
      if (filters.intakeStage && r.stage !== filters.intakeStage) return false;

      // Match outcome filter
      if (filters.matchOutcome && r.matchResult?.outcome !== filters.matchOutcome) return false;

      // HeatZone filter
      if (filters.heatZoneId && r.heatZoneId !== filters.heatZoneId) return false;

      // Search filter
      if (filters.search) {
        const q = filters.search.toLowerCase();
        const matches =
          r.id.toLowerCase().includes(q) ||
          r.canonicalUrl.toLowerCase().includes(q) ||
          (r.sourceId && r.sourceId.toLowerCase().includes(q)) ||
          (r.submitter && r.submitter.toLowerCase().includes(q)) ||
          (r.owner && r.owner.toLowerCase().includes(q));
        if (!matches) return false;
      }

      return true;
    });
  }, [records, filters]);

  // Sorted records
  const sortedRecords = useMemo(() => {
    const sorted = [...filteredRecords];
    sorted.sort((a, b) => {
      let valA: string | number = "";
      let valB: string | number = "";

      if (filters.sortBy === "id") {
        valA = a.id;
        valB = b.id;
      } else if (filters.sortBy === "stage") {
        valA = a.stage;
        valB = b.stage;
      } else if (filters.sortBy === "sourceId") {
        valA = a.sourceId || "";
        valB = b.sourceId || "";
      } else if (filters.sortBy === "submittedAt") {
        valA = a.submittedAt || "";
        valB = b.submittedAt || "";
      } else {
        // updatedAt default
        valA = a.updatedAt || a.submittedAt || "";
        valB = b.updatedAt || b.submittedAt || "";
      }

      if (valA < valB) return filters.sortOrder === "asc" ? -1 : 1;
      if (valA > valB) return filters.sortOrder === "asc" ? 1 : -1;

      // Stable secondary tie-breaker by ID
      return a.id.localeCompare(b.id);
    });

    return sorted;
  }, [filteredRecords, filters.sortBy, filters.sortOrder]);

  // Server-paginated slice
  const pageCount = Math.max(1, Math.ceil(sortedRecords.length / filters.pageSize));
  const currentPage = Math.min(filters.page, pageCount);

  const paginatedRecords = useMemo(() => {
    const start = (currentPage - 1) * filters.pageSize;
    return sortedRecords.slice(start, start + filters.pageSize);
  }, [sortedRecords, currentPage, filters.pageSize]);

  const counts = useMemo(() => queueCounts(records), [records]);

  const toggleSort = useCallback((column: string) => {
    setFilters((prev) => {
      if (prev.sortBy === column) {
        return { ...prev, sortOrder: prev.sortOrder === "asc" ? "desc" : "asc" };
      }
      return { ...prev, sortBy: column, sortOrder: "asc" };
    });
  }, []);

  return {
    filters: {
      ...filters,
      page: currentPage,
    },
    updateFilters,
    resetFilters,
    toggleSort,
    counts,
    totalRecords: filteredRecords.length,
    pageCount,
    paginatedRecords,
  };
}
