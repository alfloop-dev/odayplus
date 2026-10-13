import type { AssignmentState, IntakeInboxQuery } from "@oday-plus/openapi-client";

export type SavedViewType = "all" | "needsReview" | "awaitingEntry" | "blocked" | "processing" | "ready";
export type ViewMode = "list" | "map";
export type BooleanFilter = "" | "true" | "false";
export type IntakeInboxFilterState = {
  search: string; intakeMethod: string; intakeStage: string; matchOutcome: string;
  slaState: string; heatZoneId: string; savedView: SavedViewType; viewMode: ViewMode;
  sourceId: string; submittedBy: string; ownerId: string; assignmentStatus: AssignmentState | ""; assignedAreaId: string;
  observedFrom: string; observedTo: string; updatedFrom: string; updatedTo: string;
  masked: BooleanFilter; restricted: BooleanFilter; retryable: BooleanFilter;
  page: number; pageSize: number; sortBy: string; sortOrder: "asc" | "desc";
  selectedIntakeId: string | null;
};

export const DEFAULT_INBOX_FILTERS: IntakeInboxFilterState = {
  search: "", intakeMethod: "", intakeStage: "", matchOutcome: "", slaState: "", heatZoneId: "",
  sourceId: "", submittedBy: "", ownerId: "", assignmentStatus: "", assignedAreaId: "",
  observedFrom: "", observedTo: "", updatedFrom: "", updatedTo: "", masked: "", restricted: "", retryable: "",
  savedView: "all", viewMode: "list", page: 1, pageSize: 10, sortBy: "updatedAt", sortOrder: "desc",
  selectedIntakeId: null,
};

type Params = Pick<URLSearchParams, "getAll" | "toString">;
export const INBOX_FILTER_KEYS = ["search", "intakeMethod", "intakeStage", "matchOutcome", "slaState", "heatZoneId",
  "savedView", "sourceId", "submittedBy", "ownerId", "assignmentStatus", "assignedAreaId", "observedFrom",
  "observedTo", "updatedFrom", "updatedTo", "masked", "restricted", "retryable"] as const;
const OWNED = [...INBOX_FILTER_KEYS, "viewMode", "page", "pageSize", "sortBy", "sortOrder"] as const;
const LEGACY_SORT: Record<string, [string, "asc" | "desc"]> = {
  updated_at_desc: ["updatedAt", "desc"], status_asc: ["stage", "asc"],
  due_at_asc: ["dueAt", "asc"], submitted_at_desc: ["submittedAt", "desc"], submitted_desc: ["submittedAt", "desc"],
};

function one(params: Params, key: string): string | null {
  const values = params.getAll(key);
  return values.length && values.every((value) => value === values[0]) ? values[0] : null;
}
function integer(value: string | null, fallback: number, maximum: number) {
  if (!value || !/^[1-9]\d*$/.test(value)) return fallback;
  const number = Number(value);
  return Number.isSafeInteger(number) && number <= maximum ? number : fallback;
}

export function parseInboxUrlState(params: Params, hash = ""): IntakeInboxFilterState {
  const state = { ...DEFAULT_INBOX_FILTERS };
  for (const key of ["search", "intakeMethod", "slaState", "sourceId", "submittedBy", "ownerId",
    "assignedAreaId", "observedFrom", "observedTo", "updatedFrom", "updatedTo"] as const) state[key] = one(params, key) ?? "";
  const assignmentStatus = one(params, "assignmentStatus");
  if (assignmentStatus === "UNASSIGNED" || assignmentStatus === "ASSIGNED" || assignmentStatus === "CLAIMED"
      || assignmentStatus === "TRANSFERRED" || assignmentStatus === "ESCALATED" || assignmentStatus === "COMPLETED") state.assignmentStatus = assignmentStatus;
  for (const key of ["masked", "restricted", "retryable"] as const) {
    const value = one(params, key);
    if (value === "true" || value === "false") state[key] = value;
  }
  state.intakeStage = one(params, "intakeStage") ?? one(params, "stage") ?? "";
  state.matchOutcome = one(params, "matchOutcome") ?? one(params, "outcome") ?? "";
  state.heatZoneId = one(params, "heatZoneId") ?? one(params, "hz") ?? "";
  const saved = one(params, "savedView");
  if (["all", "needsReview", "awaitingEntry", "blocked", "processing", "ready"].includes(saved ?? "")) state.savedView = saved as SavedViewType;
  const view = one(params, "viewMode") ?? one(params, "view");
  if (view === "map" || view === "list") state.viewMode = view;
  state.page = integer(one(params, "page"), 1, Number.MAX_SAFE_INTEGER);
  state.pageSize = integer(one(params, "pageSize"), 10, 100);
  const legacy = LEGACY_SORT[one(params, "sort") ?? ""];
  const sort = one(params, "sortBy") ?? legacy?.[0];
  if (["id", "sourceId", "stage", "updatedAt", "submittedAt", "lastObservedAt", "dueAt"].includes(sort ?? "")) state.sortBy = sort!;
  const order = one(params, "sortOrder") ?? legacy?.[1];
  if (order === "asc" || order === "desc") state.sortOrder = order;
  state.selectedIntakeId = one(params, "selected") || null;
  if (!params.getAll("selected").length && /^#intake\/[^/]+$/.test(hash)) {
    try { state.selectedIntakeId = decodeURIComponent(hash.slice("#intake/".length)) || null; } catch { /* Invalid legacy link has no alternate selection. */ }
  }
  return state;
}

export function serializeInboxUrlState(state: IntakeInboxFilterState, existing: Params): URLSearchParams {
  const params = new URLSearchParams(existing.toString());
  for (const key of OWNED) {
    params.delete(key);
    const value = state[key];
    if (value !== "" && value !== DEFAULT_INBOX_FILTERS[key]) params.set(key, String(value));
  }
  for (const alias of ["stage", "outcome", "hz", "sort", "view"]) params.delete(alias);
  // Dialog, section, selection, workspace and unrelated repeated parameters
  // belong to the surrounding navigation and remain untouched.
  return params;
}

/** Translate URL state once for both the standalone view and the Next container. */
export function toIntakeInboxQuery(state: IntakeInboxFilterState, selectedHeatZoneId?: string): IntakeInboxQuery {
  const booleanValue = (value: BooleanFilter) => value === "" ? undefined : value === "true";
  return {
    page: state.page, pageSize: state.pageSize, search: state.search, savedView: state.savedView,
    intakeMethod: state.intakeMethod, intakeStage: state.intakeStage, matchOutcome: state.matchOutcome,
    slaState: state.slaState, sortBy: state.sortBy, sortOrder: state.sortOrder,
    sourceId: state.sourceId, submittedBy: state.submittedBy, ownerId: state.ownerId,
    assignmentStatus: state.assignmentStatus || undefined, assignedAreaId: state.assignedAreaId,
    observedFrom: state.observedFrom, observedTo: state.observedTo,
    updatedFrom: state.updatedFrom, updatedTo: state.updatedTo,
    masked: booleanValue(state.masked), restricted: booleanValue(state.restricted), retryable: booleanValue(state.retryable),
    selectedHeatZoneId: state.heatZoneId || selectedHeatZoneId || undefined,
  };
}
