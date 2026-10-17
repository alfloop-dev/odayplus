import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReviewPanel } from "../ReviewPanel";
import type { NetworkReviewsSnapshot, ReviewItem } from "../networkReviewTypes";
import { inspectReviewsReadProjection } from "../networkReviewProjection";
import { inspectNetworkReviewsSnapshot } from "../../NetworkFindAreasWorkspace";

afterEach(cleanup);

function review(): ReviewItem {
  return {
    id: "synthetic-decision-a", candidateId: "synthetic-candidate-a", candidateTitle: "Recorded candidate A",
    zoneLabel: "synthetic-zone", recommendation: "GO", score: null, confidence: 0.84,
    confidenceStatus: "measured", reportAvailability: "available", reportId: "synthetic-report-a", reportVersion: 1,
    canonicalDecisionStatus: "PENDING_REVIEW", risk: "recorded risk", status: "pending", statusLabel: "待審核",
    requestedBy: "synthetic-author", reviewerRole: "Site Reviewer", submittedAt: "2026-10-13T00:00:00Z",
    dueAt: null, payback: "36 個月", m12P50: "150000", rentReasonableness: "0.6", cannibalization: "0.2",
    sourceListingId: null, fieldVisit: null, brokerContact: null, notes: null, modelVersion: "synthetic-model",
    datasetSnapshotId: "synthetic-snapshot", compareText: null, eventChips: [], history: [], decision: null,
  };
}

function snapshot(row = review()): NetworkReviewsSnapshot {
  return { source: "canonical", reviews: [row],
    decisions: [{ decision_id: row.id, candidate_site_id: row.candidateId, report_id: row.reportId!,
      report_version: row.reportVersion!, decision_status: row.canonicalDecisionStatus!,
      recommendation: row.recommendation, model_version: row.modelVersion, created_by: row.requestedBy,
      created_at: row.submittedAt, history: [] }],
    candidates: [{ id: row.candidateId, reviewId: row.id, score: null }],
    approvals: [{ id: row.id, candidateId: row.candidateId, reviewId: row.id }],
  };
}

describe("nonempty canonical review read contract", () => {
  it("admits the exact recorded graph and keeps an authorized empty queue authoritative", () => {
    expect(inspectNetworkReviewsSnapshot(snapshot())).toBe("ready");
    expect(inspectNetworkReviewsSnapshot({ source: "canonical", reviews: [], decisions: [], candidates: [], approvals: [] })).toBe("empty");
  });

  it.each(["candidate", "report", "version", "duplicate", "missing-render-fields", "fake-score"])(
    "refuses %s conflicts rather than accepting only a canonical label", (failure) => {
      const value = snapshot();
      if (failure === "candidate") value.decisions![0].candidate_site_id = "synthetic-other-candidate";
      if (failure === "report") value.decisions![0].report_id = "synthetic-other-report";
      if (failure === "version") value.decisions![0].report_version = 2;
      if (failure === "duplicate") value.reviews.push({ ...value.reviews[0] });
      if (failure === "missing-render-fields") (value.reviews[0] as any).eventChips = undefined;
      if (failure === "fake-score") value.reviews[0].score = 84;
      expect(inspectNetworkReviewsSnapshot(value)).toBe("error");
    },
  );

  it.each(["requester-object", "status-label-object", "financial-object", "unknown-recommendation", "model-conflict", "actor-conflict", "terminal-without-decision", "terminal-actor-conflict", "terminal-final-label-conflict", "terminal-display-status-conflict"])(
    "rejects malformed canonical render metadata: %s", (failure) => {
      const value = snapshot();
      const row = value.reviews[0];
      if (failure === "requester-object") (row as any).requestedBy = { value: "not a renderable string" };
      if (failure === "status-label-object") (row as any).statusLabel = { value: "not a renderable string" };
      if (failure === "financial-object") (row as any).payback = { value: 36 };
      if (failure === "unknown-recommendation") (row as any).recommendation = "not-a-recorded-recommendation";
      if (failure === "model-conflict") value.decisions![0].model_version = "synthetic-other-model";
      if (failure === "actor-conflict") value.decisions![0].created_by = "synthetic-other-author";
      if (failure.startsWith("terminal")) {
        row.canonicalDecisionStatus = "APPROVED"; row.status = "approved";
        value.decisions![0].decision_status = "APPROVED";
        value.decisions![0].history = [{ to_status: "APPROVED", actor: "synthetic-reviewer", reason: "recorded approved reason",
          at: "2026-10-13T01:00:00Z", action: "APPROVE" }];
        if (failure !== "terminal-without-decision") row.decision = { decision: "GO", finalLabel: "Approved", mappedStatus: "approved",
          reason: "recorded approved reason", override: false, decidedAt: "2026-10-13T01:00:00Z", decidedBy: "synthetic-reviewer" };
        if (failure === "terminal-actor-conflict") row.decision!.decidedBy = "synthetic-other-reviewer";
        if (failure === "terminal-final-label-conflict") row.decision!.finalLabel = "Rejected";
        if (failure === "terminal-display-status-conflict") row.decision!.mappedStatus = "rejected";
      }
      expect(inspectNetworkReviewsSnapshot(value)).toBe("error");
    },
  );

  it("renders measured confidence separately and never manufactures a suitability score", () => {
    render(<ReviewPanel reviews={[review()]} fallbackRows={[]} />);
    expect(screen.getByTestId("review-card-synthetic-decision-a")).toHaveTextContent("GO —");
    expect(screen.getByText("信心值")).toBeInTheDocument();
    expect(screen.getByText("0.84")).toBeInTheDocument();
    expect(screen.getByTestId("review-metrics-synthetic-decision-a")).not.toHaveTextContent("84");
    expect(screen.queryByText("NT$428K")).not.toBeInTheDocument();
  });

  it("admits and renders a terminal decision using its recorded actor, reason and timestamp", () => {
    const row = { ...review(), canonicalDecisionStatus: "REJECTED" as const, status: "rejected", statusLabel: "已駁回",
      decision: { decision: "REJECT" as const, finalLabel: "Rejected", mappedStatus: "rejected", reason: "recorded rejection reason",
        override: true, decidedAt: "2026-10-13T01:00:00Z", decidedBy: "synthetic-independent-reviewer", decisionId: "synthetic-decision-a" } };
    const value = snapshot(row);
    value.decisions![0].history = [{ to_status: "REJECTED", actor: row.decision.decidedBy, reason: row.decision.reason,
      at: row.decision.decidedAt, action: "REJECT" }];
    expect(inspectNetworkReviewsSnapshot(value)).toBe("ready");
    render(<ReviewPanel reviews={[row]} fallbackRows={[]} />);
    expect(screen.getByTestId("review-decided-synthetic-decision-a")).toHaveTextContent("recorded rejection reason");
    expect(screen.queryByTestId("review-btn-go-synthetic-decision-a")).not.toBeInTheDocument();
  });

  it.each([0, null])("distinguishes confidence %s from an invented or missing scalar", (confidence) => {
    const value = { ...review(), confidence, confidenceStatus: confidence === null ? "unmeasured" as const : "measured" as const };
    render(<ReviewPanel reviews={[value]} fallbackRows={[]} />);
    expect(screen.getByTestId("review-card-synthetic-decision-a")).toHaveTextContent("GO —");
    expect(screen.getByText(confidence === null ? "未量測" : "0")).toBeInTheDocument();
  });

  it.each(["unavailable", "conflict"] as const)("keeps %s report history readable without offering a decision", (reportAvailability) => {
    const value = { ...review(), reportAvailability, confidence: null, confidenceStatus: "unmeasured" as const };
    expect(inspectNetworkReviewsSnapshot(snapshot(value))).toBe("ready");
    render(<ReviewPanel reviews={[value]} fallbackRows={[]} />);
    expect(screen.getByTestId("review-card-synthetic-decision-a")).toBeInTheDocument();
    expect(screen.getByTestId("review-report-note-synthetic-decision-a")).toBeInTheDocument();
    expect(screen.queryByTestId("review-btn-go-synthetic-decision-a")).not.toBeInTheDocument();
    expect(screen.queryByTestId("review-btn-wait-synthetic-decision-a")).not.toBeInTheDocument();
    expect(screen.queryByTestId("review-btn-return-synthetic-decision-a")).not.toBeInTheDocument();
    expect(screen.queryByTestId("review-btn-reject-synthetic-decision-a")).not.toBeInTheDocument();
  });

  it("never fabricates a canonical decision after a successful request but before authoritative readback", async () => {
    const decide = vi.fn().mockResolvedValue(true);
    render(<ReviewPanel reviews={[review()]} fallbackRows={[]} onDecide={decide} />);
    fireEvent.click(screen.getByTestId("review-btn-reject-synthetic-decision-a"));
    const dialog = screen.getByTestId("review-decision-dialog");
    fireEvent.change(within(dialog).getByTestId("review-decision-reason"), { target: { value: "synthetic recorded rejection reason" } });
    fireEvent.click(within(dialog).getByTestId("review-decision-ack"));
    fireEvent.click(within(dialog).getByTestId("review-decision-submit"));
    await waitFor(() => expect(decide).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByTestId("review-decision-dialog")).not.toBeInTheDocument());
    expect(screen.getByTestId("review-card-synthetic-decision-a")).toHaveTextContent("待審核");
    expect(screen.queryByTestId("review-decided-synthetic-decision-a")).not.toBeInTheDocument();
  });
});

// Captured actual local NetworkReviewService JSON; legacy scalar outcomes and
// the four recorded legacy decision mappings are not canonical relationship graphs.
const legacyProducerRow = {
  "id": "RV-702",
  "candidateId": "CS-1001",
  "candidateTitle": "信義松仁候選點",
  "zoneLabel": "信義松仁 86",
  "recommendation": "GO",
  "score": 82,
  "risk": "回本 22 個月 · 週末停車不易",
  "requestedBy": "王若寧（拓展）",
  "reviewerRole": "選址審核 / Site Reviewer",
  "submittedAt": "2026-07-13 09:20",
  "dueAt": "2026-07-16 18:00",
  "payback": "22 個月",
  "m12P50": "NT$428K",
  "rentReasonableness": "合理（區間 P45）",
  "cannibalization": "4%（低）",
  "sourceListingId": "L-2024",
  "fieldVisit": "已完成（現勘照片 6 張）",
  "brokerContact": "已聯絡（王仲介）",
  "notes": "住宅＋商辦混合，夜間洗烘需求強；既有店 650m 稀釋僅 4%。",
  "modelVersion": "SiteScore v2.3",
  "datasetSnapshotId": "FS-20260704-0600",
  "compareText": "P1 主推（GO 82，領先板橋府中 6 分）",
  "candidateStatus": "pendingreview",
  "eventChips": [
    "SiteScore v2.3",
    "Dataset FS-20260704-0600",
    "現勘完成",
    "比較 P1"
  ],
  "history": [
    {
      "t": "2026-07-04 06:10",
      "v": "SiteScore 完成 GO 82"
    },
    {
      "t": "2026-07-13 09:20",
      "v": "王若寧送審（拓展）"
    }
  ],
  "status": "pending",
  "statusLabel": "待審核",
  "decision": null,
  "candidateStatusLabel": "待審核",
  "candidateMissingData": [],
  "pending": true,
  "decided": false,
  "overrideAvailable": true
};
const legacyTerminalRows = {
  "GO": {
    "id": "RV-702",
    "candidateId": "CS-1001",
    "candidateTitle": "信義松仁候選點",
    "zoneLabel": "信義松仁 86",
    "recommendation": "GO",
    "score": 82,
    "risk": "回本 22 個月 · 週末停車不易",
    "requestedBy": "王若寧（拓展）",
    "reviewerRole": "選址審核 / Site Reviewer",
    "submittedAt": "2026-07-13 09:20",
    "dueAt": "2026-07-16 18:00",
    "payback": "22 個月",
    "m12P50": "NT$428K",
    "rentReasonableness": "合理（區間 P45）",
    "cannibalization": "4%（低）",
    "sourceListingId": "L-2024",
    "fieldVisit": "已完成（現勘照片 6 張）",
    "brokerContact": "已聯絡（王仲介）",
    "notes": "住宅＋商辦混合，夜間洗烘需求強；既有店 650m 稀釋僅 4%。",
    "modelVersion": "SiteScore v2.3",
    "datasetSnapshotId": "FS-20260704-0600",
    "compareText": "P1 主推（GO 82，領先板橋府中 6 分）",
    "candidateStatus": "approved",
    "eventChips": [
      "SiteScore v2.3",
      "Dataset FS-20260704-0600",
      "現勘完成",
      "比較 P1"
    ],
    "history": [
      {
        "t": "2026-10-13T12:06:34Z",
        "v": "選址審核 / Site Reviewer決策 GO → Approved"
      },
      {
        "t": "2026-07-04 06:10",
        "v": "SiteScore 完成 GO 82"
      },
      {
        "t": "2026-07-13 09:20",
        "v": "王若寧送審（拓展）"
      }
    ],
    "status": "approved",
    "statusLabel": "已核准 GO",
    "decision": {
      "decision": "GO",
      "finalLabel": "Approved",
      "mappedStatus": "approved",
      "reason": "synthetic recorded review reason",
      "conditions": "",
      "requiredData": [],
      "override": false,
      "decidedAt": "2026-10-13T12:06:34Z",
      "decidedBy": "synthetic-local-reviewer",
      "actorRoleId": "siteReviewer",
      "decisionId": "DEC-REVIEW-0a108cde",
      "approvalId": "AP-RV-702",
      "auditId": "AUD-REVIEW-1ccf0e84b7"
    },
    "candidateStatusLabel": "已核准 GO",
    "candidateMissingData": [],
    "pending": false,
    "decided": true,
    "overrideAvailable": false
  },
  "WAIT": {
    "id": "RV-702",
    "candidateId": "CS-1001",
    "candidateTitle": "信義松仁候選點",
    "zoneLabel": "信義松仁 86",
    "recommendation": "GO",
    "score": 82,
    "risk": "回本 22 個月 · 週末停車不易",
    "requestedBy": "王若寧（拓展）",
    "reviewerRole": "選址審核 / Site Reviewer",
    "submittedAt": "2026-07-13 09:20",
    "dueAt": "2026-07-16 18:00",
    "payback": "22 個月",
    "m12P50": "NT$428K",
    "rentReasonableness": "合理（區間 P45）",
    "cannibalization": "4%（低）",
    "sourceListingId": "L-2024",
    "fieldVisit": "已完成（現勘照片 6 張）",
    "brokerContact": "已聯絡（王仲介）",
    "notes": "住宅＋商辦混合，夜間洗烘需求強；既有店 650m 稀釋僅 4%。",
    "modelVersion": "SiteScore v2.3",
    "datasetSnapshotId": "FS-20260704-0600",
    "compareText": "P1 主推（GO 82，領先板橋府中 6 分）",
    "candidateStatus": "onhold",
    "eventChips": [
      "SiteScore v2.3",
      "Dataset FS-20260704-0600",
      "現勘完成",
      "比較 P1"
    ],
    "history": [
      {
        "t": "2026-10-13T12:06:34Z",
        "v": "選址審核 / Site Reviewer決策 WAIT → On Hold（覆寫）"
      },
      {
        "t": "2026-07-04 06:10",
        "v": "SiteScore 完成 GO 82"
      },
      {
        "t": "2026-07-13 09:20",
        "v": "王若寧送審（拓展）"
      }
    ],
    "status": "onhold",
    "statusLabel": "On Hold（WAIT）",
    "decision": {
      "decision": "WAIT",
      "finalLabel": "On Hold",
      "mappedStatus": "onhold",
      "reason": "synthetic recorded review reason",
      "conditions": "synthetic recorded pass condition",
      "requiredData": [],
      "override": true,
      "decidedAt": "2026-10-13T12:06:34Z",
      "decidedBy": "synthetic-local-reviewer",
      "actorRoleId": "siteReviewer",
      "decisionId": "DEC-REVIEW-b233a966",
      "approvalId": "AP-RV-702",
      "auditId": "AUD-REVIEW-ebab6d0b4e"
    },
    "candidateStatusLabel": "On Hold（WAIT）",
    "candidateMissingData": [],
    "pending": false,
    "decided": true,
    "overrideAvailable": false
  },
  "RETURN": {
    "id": "RV-702",
    "candidateId": "CS-1001",
    "candidateTitle": "信義松仁候選點",
    "zoneLabel": "信義松仁 86",
    "recommendation": "GO",
    "score": 82,
    "risk": "回本 22 個月 · 週末停車不易",
    "requestedBy": "王若寧（拓展）",
    "reviewerRole": "選址審核 / Site Reviewer",
    "submittedAt": "2026-07-13 09:20",
    "dueAt": "2026-07-16 18:00",
    "payback": "22 個月",
    "m12P50": "NT$428K",
    "rentReasonableness": "合理（區間 P45）",
    "cannibalization": "4%（低）",
    "sourceListingId": "L-2024",
    "fieldVisit": "已完成（現勘照片 6 張）",
    "brokerContact": "已聯絡（王仲介）",
    "notes": "住宅＋商辦混合，夜間洗烘需求強；既有店 650m 稀釋僅 4%。",
    "modelVersion": "SiteScore v2.3",
    "datasetSnapshotId": "FS-20260704-0600",
    "compareText": "P1 主推（GO 82，領先板橋府中 6 分）",
    "candidateStatus": "needdata",
    "eventChips": [
      "SiteScore v2.3",
      "Dataset FS-20260704-0600",
      "現勘完成",
      "比較 P1"
    ],
    "history": [
      {
        "t": "2026-10-13T12:06:34Z",
        "v": "選址審核 / Site Reviewer決策 RETURN → Need Data"
      },
      {
        "t": "2026-07-04 06:10",
        "v": "SiteScore 完成 GO 82"
      },
      {
        "t": "2026-07-13 09:20",
        "v": "王若寧送審（拓展）"
      }
    ],
    "status": "needdata",
    "statusLabel": "退回補件（Need Data）",
    "decision": {
      "decision": "RETURN",
      "finalLabel": "Need Data",
      "mappedStatus": "needdata",
      "reason": "synthetic recorded review reason",
      "conditions": "",
      "requiredData": [
        "synthetic recorded requested field"
      ],
      "override": false,
      "decidedAt": "2026-10-13T12:06:34Z",
      "decidedBy": "synthetic-local-reviewer",
      "actorRoleId": "siteReviewer",
      "decisionId": "DEC-REVIEW-759bfd37",
      "approvalId": "AP-RV-702",
      "auditId": "AUD-REVIEW-39928453e3"
    },
    "candidateStatusLabel": "退回補件（Need Data）",
    "candidateMissingData": [
      "synthetic recorded requested field"
    ],
    "pending": false,
    "decided": true,
    "overrideAvailable": false
  },
  "REJECT": {
    "id": "RV-702",
    "candidateId": "CS-1001",
    "candidateTitle": "信義松仁候選點",
    "zoneLabel": "信義松仁 86",
    "recommendation": "GO",
    "score": 82,
    "risk": "回本 22 個月 · 週末停車不易",
    "requestedBy": "王若寧（拓展）",
    "reviewerRole": "選址審核 / Site Reviewer",
    "submittedAt": "2026-07-13 09:20",
    "dueAt": "2026-07-16 18:00",
    "payback": "22 個月",
    "m12P50": "NT$428K",
    "rentReasonableness": "合理（區間 P45）",
    "cannibalization": "4%（低）",
    "sourceListingId": "L-2024",
    "fieldVisit": "已完成（現勘照片 6 張）",
    "brokerContact": "已聯絡（王仲介）",
    "notes": "住宅＋商辦混合，夜間洗烘需求強；既有店 650m 稀釋僅 4%。",
    "modelVersion": "SiteScore v2.3",
    "datasetSnapshotId": "FS-20260704-0600",
    "compareText": "P1 主推（GO 82，領先板橋府中 6 分）",
    "candidateStatus": "rejected",
    "eventChips": [
      "SiteScore v2.3",
      "Dataset FS-20260704-0600",
      "現勘完成",
      "比較 P1"
    ],
    "history": [
      {
        "t": "2026-10-13T12:06:34Z",
        "v": "選址審核 / Site Reviewer決策 REJECT → Rejected（覆寫）"
      },
      {
        "t": "2026-07-04 06:10",
        "v": "SiteScore 完成 GO 82"
      },
      {
        "t": "2026-07-13 09:20",
        "v": "王若寧送審（拓展）"
      }
    ],
    "status": "rejected",
    "statusLabel": "已駁回",
    "decision": {
      "decision": "REJECT",
      "finalLabel": "Rejected",
      "mappedStatus": "rejected",
      "reason": "synthetic recorded review reason",
      "conditions": "",
      "requiredData": [],
      "override": true,
      "decidedAt": "2026-10-13T12:06:34Z",
      "decidedBy": "synthetic-local-reviewer",
      "actorRoleId": "siteReviewer",
      "decisionId": "DEC-REVIEW-0e152810",
      "approvalId": "AP-RV-702",
      "auditId": "AUD-REVIEW-cc4ca65604"
    },
    "candidateStatusLabel": "已駁回",
    "candidateMissingData": [],
    "pending": false,
    "decided": true,
    "overrideAvailable": false
  }
};
function legacySnapshot(row: unknown = structuredClone(legacyProducerRow)): NetworkReviewsSnapshot {
  return { source: "api", reviews: [row] } as NetworkReviewsSnapshot;
}
function RenderVerifiedLegacyRead({ value }: { value: NetworkReviewsSnapshot }) {
  const state = inspectNetworkReviewsSnapshot(value);
  return state === "ready" ? <ReviewPanel reviews={value.reviews} fallbackRows={[]} />
    : <p data-testid="legacy-review-read-refused">{state}</p>;
}

describe("legacy API Review renderer contracts", () => {
  it("admits and renders actual legacy scalar/history without requiring a canonical graph", () => {
    const value = legacySnapshot();
    expect(inspectNetworkReviewsSnapshot(value)).toBe("ready");
    render(<RenderVerifiedLegacyRead value={value} />);
    expect(screen.getByTestId("review-card-RV-702")).toHaveTextContent("GO 82");
    expect(screen.getAllByText("王若寧（拓展）")).toHaveLength(2);
    expect(screen.getByText("SiteScore v2.3", { exact: true })).toBeInTheDocument();
    expect(value.reviews[0].score).toBe(82);
    expect(value.reviews[0].canonicalDecisionStatus).toBeUndefined();
    expect(value.decisions).toBeUndefined();
  });
  it.each(["GO", "WAIT", "RETURN", "REJECT"] as const)("preserves actual API %s decision history and shape", (action) => {
    const value = legacySnapshot(legacyTerminalRows[action]);
    expect(inspectNetworkReviewsSnapshot(value)).toBe("ready");
    render(<RenderVerifiedLegacyRead value={value} />);
    expect(screen.getByTestId("review-decided-RV-702")).toHaveTextContent(legacyTerminalRows[action].decision!.finalLabel);
    expect(screen.getByTestId("review-decided-RV-702")).toHaveTextContent("synthetic recorded review reason");
    expect(screen.queryByTestId("review-btn-go-RV-702")).not.toBeInTheDocument();
  });
  it("keeps successful API empty reads authoritative", () => {
    expect(inspectNetworkReviewsSnapshot({ source: "api", reviews: [] })).toBe("empty");
  });
  it.each([0, null])("keeps API scalar/measurement %s distinct from invented confidence conversion", (score) => {
    const row: any = structuredClone(legacyProducerRow); row.score = score; row.confidence = 0; row.confidenceStatus = "measured";
    const value = legacySnapshot(row); expect(inspectNetworkReviewsSnapshot(value)).toBe("ready");
    render(<RenderVerifiedLegacyRead value={value} />);
    expect(screen.getByTestId("review-card-RV-702")).toHaveTextContent(score === null ? "GO —" : "GO 0");
    expect(screen.getByText("信心值")).toBeInTheDocument();
    expect(row.score).toBe(score); expect(row.confidence).toBe(0);
  });
  it("retains safely optional nullable legacy metadata without inventing a model or decision", () => {
    const row: any = structuredClone(legacyProducerRow); row.confidence = null; row.confidenceStatus = "unmeasured";
    row.modelVersion = null; row.datasetSnapshotId = null; row.dueAt = null; row.payback = null;
    delete row.sourceListingId; delete row.decision;
    const value = legacySnapshot(row); expect(inspectNetworkReviewsSnapshot(value)).toBe("ready");
    render(<RenderVerifiedLegacyRead value={value} />);
    expect(screen.getByTestId("review-card-RV-702")).toHaveTextContent("GO 82");
    expect(screen.getByText("未量測")).toBeInTheDocument();
  });
  it.each([
    ["null row", (x: any) => { x.reviews.push(null); }],
    ["array row", (x: any) => { x.reviews.push([]); }],
    ["empty object row", (x: any) => { x.reviews.push({}); }],
    ["numeric row ID", (x: any) => { x.reviews[0].id = 7; }],
    ["candidate ID object", (x: any) => { x.reviews[0].candidateId = {}; }],
    ["requester object", (x: any) => { x.reviews[0].requestedBy = {}; }],
    ["status label object", (x: any) => { x.reviews[0].statusLabel = {}; }],
    ["financial React object", (x: any) => { x.reviews[0].payback = {}; }],
    ["score object", (x: any) => { x.reviews[0].score = {}; }],
    ["nonfinite score", (x: any) => { x.reviews[0].score = NaN; }],
    ["confidence status object", (x: any) => { x.reviews[0].confidenceStatus = {}; }],
    ["canonical status object", (x: any) => { x.reviews[0].canonicalDecisionStatus = {}; }],
    ["report availability object", (x: any) => { x.reviews[0].reportAvailability = {}; }],
    ["confidence object", (x: any) => { x.reviews[0].confidence = {}; }],
    ["confidence Infinity", (x: any) => { x.reviews[0].confidence = Infinity; }],
    ["missing eventChips", (x: any) => { delete x.reviews[0].eventChips; }],
    ["eventChips null", (x: any) => { x.reviews[0].eventChips = null; }],
    ["eventChips object", (x: any) => { x.reviews[0].eventChips = [{}]; }],
    ["history null", (x: any) => { x.reviews[0].history = null; }],
    ["history null entry", (x: any) => { x.reviews[0].history = [null]; }],
    ["history React object", (x: any) => { x.reviews[0].history = [{ t: {}, v: "text" }]; }],
    ["missing history detail", (x: any) => { x.reviews[0].history = [{ t: "time" }]; }],
    ["decision array", (x: any) => { x.reviews[0].decision = []; }],
    ["decision reason object", (x: any) => { x.reviews[0].decision = { ...legacyTerminalRows.GO.decision, reason: {} }; }],
    ["decision actor object", (x: any) => { x.reviews[0].decision = { ...legacyTerminalRows.GO.decision, decidedBy: {} }; }],
    ["decision action object", (x: any) => { x.reviews[0].decision = { ...legacyTerminalRows.GO.decision, decision: {} }; }],
    ["decision requiredData object", (x: any) => { x.reviews[0].decision = { ...legacyTerminalRows.RETURN.decision, requiredData: { length: 1 } }; }],
    ["decision requiredData null entry", (x: any) => { x.reviews[0].decision = { ...legacyTerminalRows.RETURN.decision, requiredData: [null] }; }],
    ["decision override object", (x: any) => { x.reviews[0].decision = { ...legacyTerminalRows.GO.decision, override: {} }; }],
  ])("rejects the whole API read instead of skipping malformed %s", (_name, damage) => {
    const value: any = legacySnapshot(); damage(value);
    expect(() => inspectNetworkReviewsSnapshot(value)).not.toThrow();
    expect(inspectNetworkReviewsSnapshot(value)).toBe("error");
  });
  it.each([null, false, []])("refuses malformed standalone top-level Review read %j without throwing", (value) => {
    expect(() => inspectReviewsReadProjection(value as never)).not.toThrow();
    expect(inspectReviewsReadProjection(value as never)).toBe("error");
  });
  it.each(["null-row", "requester", "missing-events", "decision-required-data"])("gates actual Panel rendering for %s", (failure) => {
    const value: any = legacySnapshot();
    if (failure === "null-row") value.reviews.push(null);
    if (failure === "requester") value.reviews[0].requestedBy = {};
    if (failure === "missing-events") delete value.reviews[0].eventChips;
    if (failure === "decision-required-data") {
      value.reviews[0] = { ...legacyTerminalRows.RETURN, decision: { ...legacyTerminalRows.RETURN.decision, requiredData: { length: 1 } } };
    }
    expect(() => render(<RenderVerifiedLegacyRead value={value} />)).not.toThrow();
    expect(screen.getByTestId("legacy-review-read-refused")).toHaveTextContent("error");
    expect(screen.queryByTestId("review-card-RV-702")).not.toBeInTheDocument();
  });
});
