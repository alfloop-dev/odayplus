export const NETWORK_TAB_IDS = [
  "areas",
  "radar",
  "candidates",
  "score",
  "compare",
  "review",
  "rebalance",
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
