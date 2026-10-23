import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CandidatePanel } from "../CandidatePanel";
import type { CandidateReadField, CandidateWorkflowRead, ScoringCandidate } from "../networkScoringTypes";

afterEach(cleanup);

// Explicit synthetic read receipts, not prototype seed values or production evidence.
function candidate(id = "selected-second", overrides: Partial<ScoringCandidate> = {}): ScoringCandidate {
  const field = (value: string | number | string[] | null, unit: string | null, fieldPath: string): CandidateReadField => ({
    value, unit, availability: "recorded", reason: null,
    provenance: { recordType: "Listing", recordId: `listing-${id}`, fieldPath, snapshotId: `snapshot-${id}` },
  });
  return {
    id, listingId: `listing-${id}`, heatZoneId: "actual-zone", title: `Candidate ${id}`,
    zoneLabel: "Recorded area", address: `Address ${id}`, modelVersion: "", datasetSnapshotId: `snapshot-${id}`,
    stage: "ready", scored: false, score: null, recommendation: null, inCompare: false,
    gate: { state: "ready", passed: true, missing: [], otherMissing: [], blockNote: "", okCount: 6, totalCount: 6,
      checks: ["address", "geocode", "rent", "area", "floor", "hardRule"].map((key) => ({ key, label: key, state: "ok", note: "Recorded" })) },
    readEvidence: { source: "canonical", candidateId: id, listingId: `listing-${id}`,
      addressId: `address-${id}`, datasetSnapshotId: `snapshot-${id}`, fields: {
        address: { ...field(`Address ${id}`, null, "normalized_address"), provenance: {
          recordType: "AddressLocation", recordId: `address-${id}`, fieldPath: "normalized_address", snapshotId: null,
        } },
        geocode: { ...field(0.93, "ratio", "geocode_confidence"), provenance: {
          recordType: "AddressLocation", recordId: `address-${id}`, fieldPath: "geocode_confidence", snapshotId: null,
        } },
        rent: field(54321.5, "TWD", "rent_amount"),
        area: field(29.75, "ping", "area_ping"), floor: field("2F", null, "floor"),
        hardRule: field([], null, "hard_rule_failures"), sourceConfidence: field(0, "ratio", "confidence"),
      } },
    ...overrides,
  };
}

describe("R7 Candidate original design controls with actual read evidence", () => {
  it("restores all ten original Pipeline positions and keeps comparison separate", () => {
    render(<CandidatePanel candidates={[candidate()]} fallbackRows={[]} />);
    const pipeline = within(screen.getByTestId("candidate-original-pipeline"));
    expect(pipeline.getAllByRole("button").map((button) => button.querySelector("span")?.textContent))
      .toEqual(["全部", "缺資料", "可評分", "評分中", "已評分", "短名單", "審核中", "已核准", "未通過", "暫停"]);
    expect(screen.getByTestId("candidate-compare-filter")).toHaveTextContent("比較中");
    expect(pipeline.queryByText("比較中")).not.toBeInTheDocument();
  });

  it("does not use client busy, comparison membership or model recommendations as human workflow stages", () => {
    const records = [candidate("model-go", { scored: true, recommendation: "GO", inCompare: true }),
      candidate("model-reject", { scored: true, recommendation: "REJECT" }), candidate("client-busy")];
    render(<CandidatePanel candidates={records} fallbackRows={[]} busyCandidateId="client-busy" />);
    for (const stage of ["scoring", "shortlisted", "pendingreview", "approved", "rejected", "onhold"]) {
      const control = screen.getByTestId(`candidate-pipeline-${stage}`);
      expect(control).toBeDisabled();
      expect(control).toHaveTextContent("—");
      expect(control).not.toHaveTextContent(/\b0\b/);
      expect(control).toHaveAttribute("title", expect.stringContaining("未接通"));
    }
    expect(screen.getByTestId("candidate-pipeline-evaluated")).toHaveTextContent("2");
    fireEvent.click(screen.getByTestId("candidate-compare-filter"));
    expect(screen.getByTestId("candidate-row-model-go")).toBeInTheDocument();
    expect(screen.queryByTestId("candidate-row-model-reject")).not.toBeInTheDocument();
  });

  it("restores eleven Gate display rows with actual units and the original order", () => {
    render(<CandidatePanel candidates={[candidate()]} fallbackRows={[]} />);
    const rows = within(screen.getByTestId("candidate-gate-checks-selected-second")).getAllByRole("listitem");
    expect(rows.map((row) => row.querySelector("[data-gate-label]")?.textContent)).toEqual([
      "地址定位", "租金", "坪數", "樓層", "用途", "Geocode ≥ 0.80", "Hard rule", "來源可信度", "現勘", "仲介／房東聯絡", "照片／附件",
    ]);
    expect(screen.getByTestId("candidate-evidence-rent")).toHaveTextContent("54,321.5 TWD");
    expect(screen.getByTestId("candidate-evidence-rent")).not.toHaveTextContent("/ 月");
    expect(screen.getByTestId("candidate-evidence-area")).toHaveTextContent("29.75 坪");
    expect(screen.getByTestId("candidate-evidence-geocode")).toHaveTextContent("0.93");
    expect(screen.getByTestId("candidate-evidence-sourceConfidence")).toHaveTextContent("0");
    expect(screen.getByTestId("candidate-evidence-sourceConfidence")).not.toHaveTextContent(/高|中/);
    expect(screen.getByTestId("candidate-evidence-rent")).toHaveAttribute("title", expect.stringContaining("listing-selected-second"));
    expect(screen.getByTestId("candidate-evidence-rent")).toHaveAttribute("title", expect.stringContaining("snapshot-selected-second"));
    expect(screen.getByTestId("candidate-gate-selected-second")).toHaveTextContent("6/6");
  });

  it("shows unavailable supplemental records without turning them into five mandatory admission gates", () => {
    const onScore = vi.fn();
    render(<CandidatePanel candidates={[candidate()]} fallbackRows={[]} onScore={onScore} />);
    for (const key of ["use", "visit", "contact", "photos"]) {
      expect(screen.getByTestId(`candidate-evidence-${key}`)).toHaveTextContent("未接通");
      expect(screen.getByTestId(`candidate-evidence-${key}`)).toHaveAttribute("data-state", "unknown");
    }
    const primary = screen.getByTestId("candidate-detail-score-selected-second");
    expect(primary).toBeEnabled();
    fireEvent.click(primary);
    expect(onScore).toHaveBeenCalledExactlyOnceWith("selected-second");
    expect(screen.getByTestId("candidate-evidence-photos")).not.toHaveTextContent(/6 張|0 張/);
  });

  it("retains original workflow actions and audit positions, with reasons where producers are unavailable", () => {
    const onToggleCompare = vi.fn();
    render(<CandidatePanel candidates={[candidate("selected-second", { scored: true, recommendation: "GO" })]}
      fallbackRows={[]} onToggleCompare={onToggleCompare} />);
    const detail = within(screen.getByRole("complementary", { name: "資料完整度 Gate detail" }));
    expect(detail.getByRole("button", { name: "送審（SiteScore Review）" })).toBeDisabled();
    for (const name of ["指派現勘", "聯絡仲介", "封存（On Hold）"]) {
      expect(detail.getByRole("button", { name })).toBeDisabled();
      expect(detail.getByRole("button", { name })).toHaveAttribute("title", expect.stringContaining("未接通"));
    }
    fireEvent.click(detail.getByRole("button", { name: "加入比較" }));
    expect(onToggleCompare).toHaveBeenCalledExactlyOnceWith("selected-second");
    expect(screen.getByTestId("candidate-audit-readback")).toHaveTextContent("稽核紀錄未接通");
    expect(screen.getByTestId("candidate-audit-readback")).not.toHaveTextContent("尚無操作紀錄");
  });

  it("keeps exact selected candidate measurements after filtering instead of substituting the first record", () => {
    const first = candidate("unrelated-first", { gate: { ...candidate().gate, passed: false, missing: ["租金"] } });
    render(<CandidatePanel candidates={[first, candidate()]} fallbackRows={[]} selectedCandidateId="selected-second" />);
    expect(screen.getByTestId("candidate-evidence-address")).toHaveTextContent("Address selected-second");
    fireEvent.click(screen.getByTestId("candidate-pipeline-needdata"));
    expect(screen.getByTestId("candidate-selection-unavailable")).toHaveTextContent("指定候選點");
    expect(screen.queryByTestId("candidate-evidence-address")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("candidate-pipeline-all"));
    expect(screen.getByTestId("candidate-evidence-address")).toHaveTextContent("Address selected-second");
  });

  it("does not display another candidate's evidence or a masked value from legacy aliases", () => {
    const row = candidate();
    const evidence = row.readEvidence!;
    evidence.candidateId = "different-candidate";
    render(<CandidatePanel candidates={[row]} fallbackRows={[]} />);
    expect(screen.getByTestId("candidate-evidence-rent")).toHaveTextContent("不可得");
    expect(screen.getByTestId("candidate-evidence-rent")).not.toHaveTextContent("54,321.5");
  });

  it("keeps evidence unavailable when the candidate's own listing relationship is missing", () => {
    const row = candidate("selected-second", { listingId: null });
    render(<CandidatePanel candidates={[row]} fallbackRows={[]} />);
    expect(screen.getByTestId("candidate-evidence-address")).toHaveTextContent("不可得");
    expect(screen.getByTestId("candidate-evidence-rent")).toHaveTextContent("不可得");
    expect(screen.getByTestId("candidate-evidence-rent")).not.toHaveTextContent("54,321.5");
  });

  it.each(["wrong-record", "wrong-record-type", "stale-snapshot"])("refuses %s field lineage", (failure) => {
    const row = candidate();
    const provenance = row.readEvidence!.fields.rent.provenance;
    if (failure === "wrong-record") provenance.recordId = "different-listing";
    if (failure === "wrong-record-type") provenance.recordType = "AddressLocation";
    if (failure === "stale-snapshot") provenance.snapshotId = "different-source-snapshot";
    render(<CandidatePanel candidates={[row]} fallbackRows={[]} />);
    expect(screen.getByTestId("candidate-evidence-rent")).toHaveTextContent("不可得");
    expect(screen.getByTestId("candidate-evidence-rent")).not.toHaveTextContent("54,321.5");
    expect(screen.getByTestId("candidate-evidence-address")).toHaveTextContent("Address selected-second");
  });

  it("honors explicit unavailable and masked fields without recovering legacy display values", () => {
    const row = candidate();
    row.readEvidence!.fields.address = { ...row.readEvidence!.fields.address, value: null, availability: "denied", reason: "FIELD_MASKED" };
    row.readEvidence!.fields.rent = { ...row.readEvidence!.fields.rent, value: null, availability: "unmeasured", reason: "DEFAULT_OR_UNMEASURED" };
    row.readEvidence!.fields.sourceConfidence = { ...row.readEvidence!.fields.sourceConfidence, value: null, availability: "unmeasured", reason: "CONFIDENCE_UNMEASURED" };
    row.readEvidence!.fields.hardRule = { ...row.readEvidence!.fields.hardRule, value: null, availability: "unmeasured", reason: "HARD_RULE_UNMEASURED" };
    render(<CandidatePanel candidates={[row]} fallbackRows={[]} />);
    expect(screen.getByTestId("candidate-evidence-address")).toHaveTextContent("欄位存取受限");
    expect(screen.getByTestId("candidate-evidence-address")).not.toHaveTextContent("Address selected-second");
    expect(screen.getByTestId("candidate-evidence-rent")).toHaveTextContent("尚未量測或未記錄");
    expect(screen.getByTestId("candidate-evidence-sourceConfidence")).not.toHaveTextContent("0（比例）");
    expect(screen.getByTestId("candidate-evidence-hardRule")).toHaveAttribute("data-state", "unknown");
  });

  it("keeps recorded hard-rule failure evidence distinct from an unmeasured default", () => {
    const row = candidate();
    row.readEvidence!.fields.hardRule.value = ["Recorded failure A", "Recorded failure B"];
    render(<CandidatePanel candidates={[row]} fallbackRows={[]} />);
    expect(screen.getByTestId("candidate-evidence-hardRule")).toHaveTextContent("Recorded failure A、Recorded failure B");
    expect(screen.getByTestId("candidate-evidence-hardRule")).toHaveAttribute("data-state", "fail");
    // Supplemental presentation neither re-evaluates nor changes the authoritative server gate.
    expect(screen.getByTestId("candidate-gate-selected-second")).toHaveTextContent("6/6");
  });

  it("keeps all eleven original positions when canonical measurements have not been supplied", () => {
    const row = candidate();
    delete row.readEvidence;
    render(<CandidatePanel candidates={[row]} fallbackRows={[]} />);
    expect(within(screen.getByTestId("candidate-gate-checks-selected-second")).getAllByRole("listitem")).toHaveLength(11);
    expect(screen.getByTestId("candidate-evidence-geocode")).toHaveTextContent("不可得");
    expect(screen.getByTestId("candidate-evidence-rent")).not.toHaveTextContent("54,321.5");
  });

  it("accepts scoped workflow producer reads, filters the exact record, and never uses legacy stage labels", () => {
    const read = (id: string, matches: boolean): CandidateWorkflowRead => ({ source: "canonical", candidateId: id,
      availability: "recorded", matches, producer: "synthetic-workflow-read", recordId: matches ? `receipt-${id}` : null,
      status: matches ? "producer-recorded-status" : null, reason: null });
    const first = candidate("first", { stage: "shortlisted", inCompare: true, pipelineEvidence: { shortlisted: read("first", false) } });
    const second = candidate("selected-second", { pipelineEvidence: { shortlisted: read("selected-second", true) } });
    render(<CandidatePanel candidates={[first, second]} fallbackRows={[]} />);
    expect(screen.getByTestId("candidate-pipeline-shortlisted")).toBeEnabled();
    expect(screen.getByTestId("candidate-pipeline-shortlisted")).toHaveTextContent("1");
    fireEvent.click(screen.getByTestId("candidate-pipeline-shortlisted"));
    expect(screen.queryByTestId("candidate-row-first")).not.toBeInTheDocument();
    expect(screen.getByTestId("candidate-row-selected-second")).toBeInTheDocument();
  });

  it.each(["wrong-candidate", "missing-record", "conflict", "partial-coverage"])(
    "keeps workflow counts unavailable for %s producer reads", (failure) => {
      const read: CandidateWorkflowRead = { source: "canonical", candidateId: "selected-second", availability: "recorded",
        matches: true, producer: "synthetic-workflow-read", recordId: "actual-receipt", status: "actual-status", reason: null };
      if (failure === "wrong-candidate") read.candidateId = "other";
      if (failure === "missing-record") read.recordId = null;
      if (failure === "conflict") read.availability = "conflict";
      const rows = [candidate("selected-second", { pipelineEvidence: { approved: read } })];
      if (failure === "partial-coverage") rows.push(candidate("unresolved-second"));
      render(<CandidatePanel candidates={rows} fallbackRows={[]} />);
      expect(screen.getByTestId("candidate-pipeline-approved")).toBeDisabled();
      expect(screen.getByTestId("candidate-pipeline-approved")).toHaveTextContent("—");
      if (failure === "conflict" || failure === "wrong-candidate") {
        expect(screen.getByTestId("candidate-pipeline-approved")).toHaveAttribute("title", expect.stringContaining("不一致"));
      }
    },
  );
});


it("uses only the selected canonical candidate ID for the original assignment action without making visits or photos score gates", () => {
  const open = vi.fn(), score = vi.fn();
  render(<CandidatePanel candidates={[candidate()]} fallbackRows={[]} onOpenSurvey={open} onScore={score} />);
  fireEvent.click(screen.getByRole("button", { name: "指派現勘" }));
  expect(open).toHaveBeenCalledOnce();
  expect(open).toHaveBeenCalledWith({ kind: "CANDIDATE_SITE", id: "selected-second" });
  expect(screen.getByTestId("candidate-evidence-visit")).toHaveAttribute("data-state", "unknown");
  expect(screen.getByTestId("candidate-evidence-photos")).toHaveAttribute("data-state", "unknown");
  fireEvent.click(screen.getByTestId("candidate-detail-score-selected-second"));
  expect(score).toHaveBeenCalledWith("selected-second");
});
