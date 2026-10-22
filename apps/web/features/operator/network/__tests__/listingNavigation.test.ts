import { describe, expect, it } from "vitest";
import { navigationIncludesTarget, resolveListingNavigation, type ListingNavigationSnapshot, type RelatedIntake } from "../listingNavigation";

const listingId = "57f8835e-f90f-441b-bf16-9b40bfeac001";
const candidateId = "57f8835e-f90f-441b-bf16-9b40bfeac002";
function intake(id: string, targetListingId: string | null = listingId): RelatedIntake {
  return { id, stage: "READY", matchResult: { targetListingId, outcome: "NEW", outcomeLabel: "New",
    confidence: 1, agreeingSignals: [], contradictingSignals: [], summary: "Explicit test relationship" } };
}
function snapshot(overrides: Partial<ListingNavigationSnapshot> = {}): ListingNavigationSnapshot {
  return { source: "api", listings: [{ id: listingId }], candidates: [], assistedIntakes: [], ...overrides };
}

describe("Radar navigation uses explicit visible relationships", () => {
  it("finds a canonical candidate without a legacy listing.candidateId", () => {
    const result = resolveListingNavigation(listingId, snapshot({ candidates: [{ id: candidateId, listingId }] }), true);
    expect(result.primary).toEqual({ kind: "candidate", id: candidateId });
  });

  it("opens the one related intake, not an unrelated first record", () => {
    const result = resolveListingNavigation(listingId, snapshot({ assistedIntakes: [intake("other", "another-listing"), intake("related")] }), true);
    expect(result.primary).toEqual({ kind: "intake", id: "related" });
    expect(navigationIncludesTarget(result, { kind: "intake", id: "other" })).toBe(false);
  });

  it("requires explicit selection when several visible intakes point to one listing", () => {
    const result = resolveListingNavigation(listingId, snapshot({ assistedIntakes: [intake("older"), intake("newer")] }), true);
    expect(result).toMatchObject({ state: "multiple", primary: null, intakes: [{ id: "older" }, { id: "newer" }] });
    expect(navigationIncludesTarget(result, { kind: "intake", id: "newer" })).toBe(true);
  });

  it("distinguishes a successful read with no visible relation from a missing collection", () => {
    expect(resolveListingNavigation(listingId, snapshot(), true).state).toBe("none");
    expect(resolveListingNavigation(listingId, snapshot({ assistedIntakes: undefined }), true).state).toBe("unavailable");
  });

  it.each([null, "fixture", "seed"])("does not join an unverified source: %s", (source) => {
    expect(resolveListingNavigation(listingId, snapshot({ source: source ?? undefined }), true).state).toBe("unavailable");
  });

  it("rejects data retained from an earlier role/session generation", () => {
    expect(resolveListingNavigation(listingId, snapshot({ assistedIntakes: [intake("hidden")] }), false))
      .toMatchObject({ state: "unavailable", primary: null, intakes: [] });
  });

  it("cannot recover a missing parent from a child or a masked relation", () => {
    expect(resolveListingNavigation(listingId, snapshot({ listings: [], candidates: [{ id: candidateId, listingId }] }), true).state).toBe("unavailable");
    expect(resolveListingNavigation(listingId, snapshot({ assistedIntakes: [intake("masked", null)] }), true).state).toBe("none");
  });

  it.each([
    { listings: [{ id: listingId, candidateId: "inaccessible-candidate" }] },
    { candidates: [{ id: candidateId, listingId }, { id: "second", listingId }] },
    { candidates: [{ id: candidateId, listingId }, { id: candidateId, listingId: "another-parent" }] },
    { assistedIntakes: [intake("same-id"), intake("same-id", "another-parent")] },
  ])("does not silently select a conflicting relationship %#", (overrides) => {
    const result = resolveListingNavigation(listingId, snapshot(overrides), true);
    expect(result).toMatchObject({ state: "conflict", primary: null, intakes: [] });
    expect(navigationIncludesTarget(result, { kind: "candidate", id: candidateId })).toBe(false);
  });
});
