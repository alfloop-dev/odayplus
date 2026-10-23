import type { IntakeUrlState } from "./types";

export function parseUrlState(
  searchParams: URLSearchParams | Record<string, string | string[] | undefined> | string,
): IntakeUrlState {
  let params: URLSearchParams;
  if (typeof searchParams === "string") {
    params = new URLSearchParams(searchParams);
  } else if (searchParams instanceof URLSearchParams) {
    params = searchParams;
  } else {
    params = new URLSearchParams();
    for (const [key, val] of Object.entries(searchParams)) {
      if (val !== undefined) {
        if (Array.isArray(val)) {
          val.forEach((v) => params.append(key, v));
        } else {
          params.set(key, val);
        }
      }
    }
  }

  // Conflicting repeated navigation values are not a deterministic target.
  const get = (key: string) => {
    const values = params.getAll(key);
    return values.length && values.every((value) => value === values[0]) ? values[0] : null;
  };

  const compareVal = get("compare");

  return {
    filters: {
      stage: (params.get("stage") as any) || undefined,
      matchOutcome: (params.get("outcome") as any) || undefined,
      sourceId: params.get("source") || undefined,
      heatZoneId: get("hz") ?? undefined,
    },
    sort: (params.get("sort") as any) || undefined,
    view: (params.get("view") as "list" | "map") || undefined,
    selectedId: get("selected") || null,
    dialog: (["add", "detail", "fix", "decide", "assignmentSla"].includes(get("dialog") ?? "") ? get("dialog") : null) as IntakeUrlState["dialog"],
    activeSection: get("section") || null,
    fixFieldKey: params.get("field") || null,
    decisionKind: (params.get("decision") as any) || null,
    receiptId: params.get("receipt") || null,
    compareTask: compareVal === "true" ? true : compareVal === "false" ? false : null,
  };
}

export function serializeUrlState(
  state: Partial<IntakeUrlState>,
  existingParams?: URLSearchParams,
): URLSearchParams {
  const params = existingParams
    ? new URLSearchParams(existingParams.toString())
    : new URLSearchParams();

  const keysToClear = [
    "stage",
    "outcome",
    "source",
    "hz",
    "sort",
    "view",
    "selected",
    "dialog",
    "section",
    "field",
    "decision",
    "receipt",
    "compare",
  ];
  for (const k of keysToClear) {
    params.delete(k);
  }

  if (state.filters) {
    if (state.filters.stage) params.set("stage", state.filters.stage);
    if (state.filters.matchOutcome) params.set("outcome", state.filters.matchOutcome);
    if (state.filters.sourceId) params.set("source", state.filters.sourceId);
    if (state.filters.heatZoneId !== undefined) params.set("hz", state.filters.heatZoneId);
  }
  if (state.sort) params.set("sort", state.sort);
  if (state.view) params.set("view", state.view);
  if (state.selectedId) params.set("selected", state.selectedId);
  if (state.dialog) params.set("dialog", state.dialog);
  if (state.activeSection) params.set("section", state.activeSection);
  if (state.fixFieldKey) params.set("field", state.fixFieldKey);
  if (state.decisionKind) params.set("decision", state.decisionKind);
  if (state.receiptId) params.set("receipt", state.receiptId);
  if (state.compareTask !== undefined && state.compareTask !== null) {
    params.set("compare", String(state.compareTask));
  }

  return params;
}
