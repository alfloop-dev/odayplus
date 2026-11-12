import type { IntakeUrlState } from "./types";
import { clearIntakeSelection, intakeNavigationHash } from "./intakeNavigation";
export { clearIntakeSelection, intakePathSelection, intakeNavigationHash } from "./intakeNavigation";

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
  const kinds = params.getAll("intakeKind");
  const cursors = params.getAll("canonicalCursor");
  const validCursor = cursors.every((value) => value === cursors[0] && value.length <= 2048);

  return {
    ...(kinds.length ? { intakeKind: kinds.every((value) => value === "canonical") && validCursor
      ? "canonical" as const : "invalid" as const } : {}),
    ...(cursors.length ? { canonicalCursor: validCursor ? cursors[0] : null } : {}),
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

  clearIntakeSelection(params);
  const keysToClear = [
    "stage",
    "outcome",
    "source",
    "hz",
    "sort",
    "view",
  ];
  for (const k of keysToClear) {
    params.delete(k);
  }

  if (state.intakeKind) params.set("intakeKind", state.intakeKind);
  if (state.intakeKind === "canonical" && state.canonicalCursor != null) params.set("canonicalCursor", state.canonicalCursor);

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

export function buildInboxReturnHref(
  state: IntakeUrlState,
  existingParams?: URLSearchParams,
  hash = "",
): string {
  const params = serializeUrlState(state, existingParams);
  params.set("ws", "network");
  params.set("tab", "radar");
  return `/operator?${params.toString()}${intakeNavigationHash(hash)}`;
}

export function buildDurableDetailHref(
  intakeId: string,
  state: IntakeUrlState,
  existingParams?: URLSearchParams,
  hash = "",
): string {
  const params = serializeUrlState(
    { ...state, selectedId: intakeId, dialog: "detail" },
    existingParams,
  );
  params.delete("ws");
  params.delete("tab");
  return `/intake/${encodeURIComponent(intakeId)}?${params.toString()}${intakeNavigationHash(hash)}`;
}
