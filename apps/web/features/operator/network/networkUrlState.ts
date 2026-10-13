export const NETWORK_TAB_IDS = [
  "areas",
  "radar",
  "candidates",
  "score",
  "compare",
  "review",
  "rebalance",
  "composition",
] as const;

export type NetworkTabId = (typeof NETWORK_TAB_IDS)[number];

type SearchParamsLike = Pick<URLSearchParams, "get" | "toString">;

const NETWORK_TAB_ALIASES: Readonly<Record<string, NetworkTabId>> = {
  overview: "areas",
  find: "areas",
  "find-areas": "areas",
  listings: "radar",
  "listing-radar": "radar",
  candidate: "candidates",
  sitescore: "score",
  merge: "composition",
  split: "composition",
  "merge-split": "composition",
  topology: "composition",
};

export function parseNetworkTabIndex(
  searchParams: SearchParamsLike | string,
): number {
  const params =
    typeof searchParams === "string"
      ? new URLSearchParams(searchParams)
      : searchParams;
  const requestedTab = params.get("tab")?.trim().toLowerCase() ?? "";
  const tabId =
    NETWORK_TAB_ALIASES[requestedTab] ??
    NETWORK_TAB_IDS.find((candidate) => candidate === requestedTab) ??
    "areas";
  return NETWORK_TAB_IDS.indexOf(tabId);
}

export function serializeNetworkTab(
  tabIndex: number,
  existingParams?: SearchParamsLike,
): URLSearchParams {
  const params = new URLSearchParams(existingParams?.toString() ?? "");
  const tabId = NETWORK_TAB_IDS[tabIndex] ?? NETWORK_TAB_IDS[0];
  params.set("tab", tabId);
  return params;
}

export function buildNetworkTabHref(
  pathname: string,
  tabIndex: number,
  existingParams?: SearchParamsLike,
  hash = "",
): string {
  const params = serializeNetworkTab(tabIndex, existingParams);
  const query = params.toString();
  const normalizedHash = hash
    ? hash.startsWith("#")
      ? hash
      : `#${hash}`
    : "";
  return `${pathname}${query ? `?${query}` : ""}${normalizedHash}`;
}

export const CANDIDATE_SELECTION_PARAM = "candidate";
export const REVIEW_SELECTION_PARAM = "review";

export function parseReviewSelection(params: { toString(): string }): { id: string | null; invalid: boolean } {
  const values = new URLSearchParams(params.toString()).getAll(REVIEW_SELECTION_PARAM);
  if (!values.length) return { id: null, invalid: false };
  if (!values[0].trim() || values.some((value) => value !== values[0])) return { id: null, invalid: true };
  return { id: values[0], invalid: false };
}

/** The exact workflow candidate is reloadable only within its read chain. */
export function isCandidateWorkflowTab(tabIndex: number): boolean {
  return tabIndex >= 2 && tabIndex <= 5;
}

export function candidateWorkflowNavigationParams(
  tabIndex: number,
  existing: { toString(): string },
  currentCandidateId: string | null,
  extraParams?: Record<string, string>,
): URLSearchParams {
  const explicitCandidate = extraParams?.[CANDIDATE_SELECTION_PARAM];
  const candidateId = isCandidateWorkflowTab(tabIndex)
    ? explicitCandidate || (extraParams?.zone ? null : currentCandidateId) : null;
  const params = candidateId ? candidateNavigationParams(candidateId, existing) : new URLSearchParams(existing.toString());
  params.delete(REVIEW_SELECTION_PARAM);
  params.delete(CANDIDATE_SELECTION_PARAM);
  params.delete("zone");
  for (const [key, value] of Object.entries(extraParams ?? {})) params.set(key, value);
  if (tabIndex !== 2 || candidateId) params.delete("zone");
  if (candidateId) params.set(CANDIDATE_SELECTION_PARAM, candidateId);
  else params.delete(CANDIDATE_SELECTION_PARAM);
  return params;
}

export function parseCandidateSelection(params: { toString(): string }): { id: string | null; invalid: boolean } {
  const values = new URLSearchParams(params.toString()).getAll(CANDIDATE_SELECTION_PARAM);
  if (!values.length) return { id: null, invalid: false };
  if (!values[0].trim() || values.some((value) => value !== values[0])) return { id: null, invalid: true };
  return { id: values[0], invalid: false };
}

/** Candidate and intake selection have separate URL owners. */
export function candidateNavigationParams(candidateId: string, existing: { toString(): string }): URLSearchParams {
  const params = new URLSearchParams(existing.toString());
  for (const key of ["zone", "selected", "dialog", "section", "field", "decision", "receipt", "compare", REVIEW_SELECTION_PARAM]) {
    params.delete(key);
  }
  params.set(CANDIDATE_SELECTION_PARAM, candidateId);
  return params;
}
