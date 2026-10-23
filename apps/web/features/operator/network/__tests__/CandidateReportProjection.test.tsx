import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CandidatePanel } from "../CandidatePanel";
import type { ScoringCandidate } from "../networkScoringTypes";

afterEach(cleanup);

// Recorded-shaped synthetic read data; no model admission or business score.
function candidate(id: string, modelVersion: string | null, scored: boolean): ScoringCandidate {
  return {
    id, listingId: `listing-${id}`, heatZoneId: "synthetic-zone", title: id,
    zoneLabel: "Synthetic area", address: "Synthetic address", modelVersion,
    datasetSnapshotId: "current-input", stage: scored ? "evaluated" : "ready",
    scored, score: null, recommendation: scored ? "INVESTIGATE" : null, inCompare: false,
    gate: { state: "ready", passed: true, missing: [], otherMissing: [], blockNote: "",
      checks: [], okCount: 0, totalCount: 0 },
  };
}

describe("Candidate committed model projection", () => {
  it("shows each own recorded model and keeps exact second selection without inventing confidence score", () => {
    render(<CandidatePanel candidates={[
      candidate("candidate-A", "actual-model-A-v1", true),
      candidate("candidate-B", "actual-model-B-v2", true),
    ]} fallbackRows={[]} selectedCandidateId="candidate-B" />);
    expect(screen.getByTestId("candidate-row-candidate-A")).toHaveTextContent("actual-model-A-v1");
    expect(screen.getByTestId("candidate-row-candidate-B")).toHaveTextContent("actual-model-B-v2");
    expect(screen.getByTestId("candidate-row-candidate-B")).toHaveAttribute("data-active", "true");
    expect(screen.getByTestId("candidate-row-candidate-A")).not.toHaveAttribute("data-active");
    expect(screen.getByTestId("candidate-row-candidate-B")).toHaveTextContent("INVESTIGATE");
    expect(screen.getByTestId("candidate-row-candidate-B")).not.toHaveTextContent("91");
  });

  it("makes absent own report explicit even when another candidate has a model", () => {
    render(<CandidatePanel candidates={[
      candidate("candidate-A", "other-candidate-model", true), candidate("candidate-B", null, false),
    ]} fallbackRows={[]} selectedCandidateId="candidate-B" />);
    const row = screen.getByTestId("candidate-row-candidate-B");
    expect(row).toHaveTextContent("尚無評分報告");
    expect(row).not.toHaveTextContent("other-candidate-model");
    expect(row).not.toHaveTextContent("null");
  });

  it("keeps report availability distinct from unavailable model metadata", () => {
    render(<CandidatePanel candidates={[candidate("candidate-A", null, true)]} fallbackRows={[]} />);
    const row = screen.getByTestId("candidate-row-candidate-A");
    expect(row).toHaveTextContent("模型版本未提供");
    expect(row).toHaveTextContent("INVESTIGATE");
    expect(row).not.toHaveTextContent("尚無評分報告");
    expect(row).not.toHaveTextContent("null");
  });
});
