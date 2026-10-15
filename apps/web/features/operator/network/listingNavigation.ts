import type { AssistedIntake } from "@oday-plus/openapi-client";

export type RelatedIntake = Pick<AssistedIntake, "id" | "stage" | "matchResult">;
export type ListingNavigationSnapshot = {
  source?: string;
  listings?: readonly { id: string; candidateId?: string | null }[];
  candidates?: readonly { id: string; listingId?: string | null }[];
  assistedIntakes?: readonly RelatedIntake[];
};

export type ListingNavigationTarget = { kind: "candidate" | "intake"; id: string };
export type ListingNavigation = {
  state: "candidate" | "intake" | "multiple" | "none" | "unavailable" | "conflict";
  label: string;
  primary: ListingNavigationTarget | null;
  intakes: readonly { id: string; stage: string }[];
};

const unavailable = (): ListingNavigation => ({
  state: "unavailable", label: "關聯資料尚未完成讀取", primary: null, intakes: [],
});
const conflict = (): ListingNavigation => ({
  state: "conflict", label: "關聯資料不一致，請重新確認", primary: null, intakes: [],
});

/** A join of records already returned by the guarded read; never infer a link. */
export function resolveListingNavigation(
  listingId: string,
  snapshot: ListingNavigationSnapshot | null,
  readyForCurrentContext: boolean,
): ListingNavigation {
  if (!readyForCurrentContext || snapshot?.source !== "api"
    || !Array.isArray(snapshot.listings) || !Array.isArray(snapshot.candidates)
    || !Array.isArray(snapshot.assistedIntakes)) return unavailable();
  const hasId = (row: unknown): boolean => typeof row === "object" && row !== null
    && typeof (row as { id?: unknown }).id === "string" && Boolean((row as { id: string }).id);
  if (!snapshot.listings.every(hasId) || !snapshot.candidates.every(hasId)
    || !snapshot.assistedIntakes.every(hasId)) return unavailable();
  const parents = snapshot.listings.filter((row) => row.id === listingId);
  if (parents.length !== 1) return parents.length ? conflict() : unavailable();
  const parent = parents[0];
  const candidates = snapshot.candidates.filter((row) => row.listingId === listingId);
  if (candidates.length > 1) return conflict();
  const candidate = candidates[0];
  if ((parent.candidateId && parent.candidateId !== candidate?.id)
    || (candidate && (!candidate.id || snapshot.candidates.filter((row) => row.id === candidate.id).length !== 1))) {
    return conflict();
  }
  const related = snapshot.assistedIntakes.filter((row) => row.matchResult?.targetListingId === listingId);
  if (related.some((row) => !row.id || snapshot.assistedIntakes!.filter((other) => other.id === row.id).length !== 1)) {
    return conflict();
  }
  const intakes = related.map(({ id, stage }) => ({ id, stage }));
  if (candidate) return {
    state: "candidate", label: "前往候選點", primary: { kind: "candidate", id: candidate.id }, intakes,
  };
  if (intakes.length === 1) return {
    state: "intake", label: "查看收件與審查", primary: { kind: "intake", id: intakes[0].id }, intakes,
  };
  if (intakes.length > 1) return {
    state: "multiple", label: `${intakes.length} 筆關聯收件，請選擇`, primary: null, intakes,
  };
  return { state: "none", label: "目前未找到可存取的關聯紀錄", primary: null, intakes: [] };
}

export function navigationIncludesTarget(navigation: ListingNavigation, target: ListingNavigationTarget): boolean {
  if (navigation.state === "unavailable" || navigation.state === "conflict") return false;
  return target.kind === "candidate"
    ? navigation.primary?.kind === "candidate" && navigation.primary.id === target.id
    : navigation.intakes.some((intake) => intake.id === target.id);
}
