import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildNetworkFindAreasViewModel } from "../../networkFindAreasViewModel";
import type { Listing } from "../../types";
import { CandidatePanel } from "../CandidatePanel";
import { ListingRadarPanel } from "../ListingRadarPanel";
import { resolveListingNavigation, type RelatedIntake } from "../listingNavigation";
import type { ScoringCandidate } from "../networkScoringTypes";

vi.mock("../intake/AssistedIntakeSection", () => ({ AssistedIntakeSection: () => <div /> }));
afterEach(cleanup);

// Explicit synthetic inputs for navigation tests; no production admission claim.
const listing: Listing = {
  id: "a676c720-4018-4c91-ad62-a49e951a5501", sourceId: "recorded-source", heatZoneId: null,
  address: "Recorded test location", status: "new", rentPerMonth: 30000, areaPing: 25,
  geocodeConfidence: null, hardRuleFailures: null,
};
function intake(id: string): RelatedIntake {
  return { id, stage: "READY", matchResult: { targetListingId: listing.id, outcome: "NEW", outcomeLabel: "New", confidence: 1,
    agreeingSignals: [], contradictingSignals: [], summary: "Explicit test relation" } };
}
const candidate = (id: string): ScoringCandidate => ({
  id, listingId: listing.id, heatZoneId: "recorded-zone", title: id, address: "Recorded test location", zoneLabel: "Area",
  modelVersion: "", datasetSnapshotId: "", stage: "new", scored: false, score: null, recommendation: null, inCompare: false,
  gate: { passed: false, state: "blocked", missing: ["required input"], otherMissing: [], blockNote: "Awaiting evidence", checks: [], okCount: 0, totalCount: 1 },
});

function radar(intakes: RelatedIntake[], onNavigate = vi.fn()) {
  const view = buildNetworkFindAreasViewModel({ heatZones: [], candidates: [], listings: [listing] });
  const navigation = resolveListingNavigation(listing.id, { source: "api", listings: [listing], candidates: [], assistedIntakes: intakes }, true);
  render(<ListingRadarPanel activeRoleId="expansion-manager" listings={[listing]} rows={view.listingRadar} sources={[]}
    navigationByListing={new Map([[listing.id, navigation]])} onNavigate={onNavigate} />);
  return onNavigate;
}

describe("Radar and candidate exact navigation", () => {
  it("opens a real-shaped UUID intake from both row and detail even when hard-rule evidence is unknown", () => {
    const onNavigate = radar([intake("a676c720-4018-4c91-ad62-a49e951a5502")]);
    fireEvent.click(screen.getByTestId(`listing-navigate-${listing.id}`));
    fireEvent.click(screen.getByTestId("listing-detail-primary"));
    expect(onNavigate).toHaveBeenCalledTimes(2);
    expect(onNavigate).toHaveBeenLastCalledWith(listing.id, { kind: "intake", id: "a676c720-4018-4c91-ad62-a49e951a5502" });
    expect(screen.queryByText("轉為候選點")).not.toBeInTheDocument();
  });

  it("offers explicit choices for multiple related intakes without choosing one on the primary click", () => {
    const onNavigate = radar([intake("recorded-first"), intake("recorded-second")]);
    fireEvent.click(screen.getByTestId(`listing-navigate-${listing.id}`));
    expect(onNavigate).not.toHaveBeenCalled();
    expect(screen.getByTestId("listing-detail-primary")).toBeDisabled();
    fireEvent.click(within(screen.getByTestId("listing-related-intakes")).getByRole("button", { name: /recorded-second/ }));
    expect(onNavigate).toHaveBeenCalledWith(listing.id, { kind: "intake", id: "recorded-second" });
  });

  it("explains no visible relation rather than rendering an enabled no-op", () => {
    radar([]);
    expect(screen.getByTestId("listing-detail-primary")).toBeDisabled();
    expect(screen.getByTestId("listing-detail-primary")).toHaveTextContent("目前未找到可存取的關聯紀錄");
  });

  it.each([
    { name: "merged", id: "L-2029", status: "duplicate" as const, mergedIntoId: "L-2025", label: "已標記重複至 L-2025" },
    { name: "archived", id: "L-2030", status: "archived" as const, mergedIntoId: undefined, label: "已封存" },
  ])("keeps the $name primary terminal and preserves authorized intake history", ({ id, status, mergedIntoId, label }) => {
    const terminal = { ...listing, id, status, mergedIntoId, duplicateOfId: "L-2025", hardRuleFailures: [] };
    const view = buildNetworkFindAreasViewModel({ heatZones: [], candidates: [], listings: [terminal] });
    const onNavigate = vi.fn();
    const onMerge = vi.fn();
    const onArchive = vi.fn();
    const panel = (relatedIntakes: RelatedIntake[]) => <ListingRadarPanel activeRoleId="expansion-manager"
      listings={[terminal]} rows={view.listingRadar} sources={[]} onNavigate={onNavigate} onMerge={onMerge} onArchive={onArchive}
      navigationByListing={new Map([[id, resolveListingNavigation(id,
        { source: "api", listings: [terminal], candidates: [], assistedIntakes: relatedIntakes }, true)]])} />;
    const rendered = render(panel([]));
    const assertTerminal = (expectedReads = 0) => {
      const primary = screen.getByTestId("listing-detail-primary");
      expect(primary).toHaveTextContent(label);
      expect(primary).toBeDisabled();
      expect(screen.getByTestId(`listing-navigate-${id}`)).toBeDisabled();
      expect(screen.queryByTestId("merge-L-2029")).not.toBeInTheDocument();
      expect(screen.queryByTestId("archive-L-2030")).not.toBeInTheDocument();
      expect(screen.queryByTestId("listing-detail-merge-denied")).not.toBeInTheDocument();
      fireEvent.click(primary);
      fireEvent.click(screen.getByTestId(`listing-navigate-${id}`));
      expect(onNavigate).toHaveBeenCalledTimes(expectedReads);
      expect(onMerge).not.toHaveBeenCalled();
      expect(onArchive).not.toHaveBeenCalled();
    };
    assertTerminal();
    expect(screen.queryByTestId("listing-related-intakes")).not.toBeInTheDocument();
    const retained = intake("retained-terminal-intake");
    retained.matchResult = { ...retained.matchResult!, targetListingId: id };
    rendered.rerender(panel([retained]));
    assertTerminal();
    const history = within(screen.getByTestId("listing-related-intakes")).getByRole("button", { name: /retained-terminal-intake/ });
    expect(history).toBeEnabled();
    fireEvent.click(history);
    expect(onNavigate).toHaveBeenLastCalledWith(id, { kind: "intake", id: retained.id });
    assertTerminal(1);
  });

  it("selects the requested candidate instead of the first and restores selection after rerender", () => {
    const records = [candidate("unrelated-first"), candidate("requested")];
    const view = render(<CandidatePanel candidates={records} fallbackRows={[]} selectedCandidateId="requested" />);
    expect(screen.getByTestId("candidate-detail-score-requested")).toBeInTheDocument();
    expect(screen.queryByTestId("candidate-detail-score-unrelated-first")).not.toBeInTheDocument();
    view.rerender(<CandidatePanel candidates={records} fallbackRows={[]} selectedCandidateId="unrelated-first" />);
    expect(screen.getByTestId("candidate-detail-score-unrelated-first")).toBeInTheDocument();
  });

  it("never substitutes another candidate when the exact ID disappears or is filtered out", () => {
    const records = [candidate("unrelated-first"), candidate("requested")];
    const view = render(<CandidatePanel candidates={records} fallbackRows={[]} selectedCandidateId="requested" zoneScope={{ id: "different-zone", label: "Other" }} />);
    expect(screen.getByTestId("candidate-empty")).toHaveTextContent("指定候選點");
    view.rerender(<CandidatePanel candidates={[records[0]]} fallbackRows={[]} selectedCandidateId="requested" />);
    expect(screen.getByTestId("candidate-selection-unavailable")).toHaveTextContent("指定候選點");
    expect(screen.queryByTestId("candidate-detail-score-unrelated-first")).not.toBeInTheDocument();
  });

  it("refuses duplicate candidate IDs on a direct URL rather than selecting the first", () => {
    render(<CandidatePanel candidates={[candidate("duplicate"), { ...candidate("duplicate"), title: "Conflicting record" }]}
      fallbackRows={[]} selectedCandidateId="duplicate" />);
    expect(screen.getByTestId("candidate-selection-unavailable")).toHaveTextContent("資料不一致");
    expect(screen.queryByTestId("candidate-detail-score-duplicate")).not.toBeInTheDocument();
  });

  it("keeps malformed explicit links from falling back to the first candidate", () => {
    render(<CandidatePanel candidates={[candidate("unrelated-first")]} fallbackRows={[]} selectionError="候選點連結不一致" />);
    expect(screen.getByTestId("candidate-selection-unavailable")).toHaveTextContent("候選點連結不一致");
    expect(screen.queryByTestId("candidate-detail-score-unrelated-first")).not.toBeInTheDocument();
  });
});
