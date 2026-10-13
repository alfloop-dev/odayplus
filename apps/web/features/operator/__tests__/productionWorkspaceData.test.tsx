import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DesignStoreOpsWorkspace,
  DesignTodayWorkspace,
  inspectStoreOpsApiPayload,
} from "../DesignAlignedWorkspaces";
import {
  inspectNetworkListingsSnapshot,
  inspectNetworkRebalanceSnapshot,
  inspectNetworkReviewsSnapshot,
  inspectNetworkScoringSnapshot,
  resolveNetworkCountState,
  resolveNetworkFindAreasLoadState,
  resolveNetworkTabGateState,
} from "../NetworkFindAreasWorkspace";
import { resolveGovernanceDecisionAuthority } from "../GovernanceWorkspace";
import { HEAT_ZONE_FIXTURES, LISTING_FIXTURES, LISTING_SOURCE_FIXTURES } from "../fixtures";
import { normalizeGovernanceActionAuthority } from "../governance/governanceLoader";
import {
  operatorLoadFailureFromError,
  operatorLoadFailureFromResponse,
} from "../operatorDataMode";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

describe("production workspace data contracts", () => {
  it("keeps the legacy Today fixture surface unavailable in production", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");

    render(<DesignTodayWorkspace onQueueSelect={vi.fn()} />);

    expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute(
      "data-status",
      "empty",
    );
    expect(screen.queryByText(/林承翰/)).not.toBeInTheDocument();
    expect(screen.queryByText("Kiosk 離線＋遠端重啟失敗")).not.toBeInTheDocument();
  });

  it("retains the legacy Today fixtures for local POC mode", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");

    render(<DesignTodayWorkspace onQueueSelect={vi.fn()} />);

    expect(screen.getByText(/林承翰/)).toBeInTheDocument();
    expect(screen.getByText("Kiosk 離線＋遠端重啟失敗")).toBeInTheDocument();
  });

  it("blocks an incomplete Store Ops response without displaying an issue fixture", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        source: "operator-store-ops-production",
        issues: [],
        stores: [],
        evidence: [],
        auditEvents: [],
        fourLightSummary: [],
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ));

    render(
      <DesignStoreOpsWorkspace
        onOpenWorkflow={vi.fn()}
      />,
    );

    const gate = await screen.findByTestId("operator-data-unavailable");
    await waitFor(() => expect(gate).toHaveAttribute("data-status", "empty"));
    expect(screen.queryByText("付款機前卡住＋付款失敗＋Google 負評")).not.toBeInTheDocument();
  });

  it("classifies Store Ops seed, empty, and usable API payloads", () => {
    const livePayload = {
      source: "operator-store-ops-production",
      issues: [{ id: "LIVE-ISSUE-1" }],
      stores: [{ id: "LIVE-STORE-1" }],
      evidence: [],
      auditEvents: [],
    };
    expect(inspectStoreOpsApiPayload(livePayload)).toBe("ready");
    expect(inspectStoreOpsApiPayload({ ...livePayload, source: undefined })).toBe("ready");
    expect(inspectStoreOpsApiPayload({ ...livePayload, source: "fixture-store-ops" })).toBe("seed");
    expect(inspectStoreOpsApiPayload({ ...livePayload, issues: [] })).toBe("empty");
  });

  it("requires non-seed, non-empty Network and Listing API snapshots", () => {
    const listingSnapshot = {
      source: "api" as const,
      heatZones: [{ ...HEAT_ZONE_FIXTURES[0], id: "LIVE-HZ-1" }],
      listingSources: [{ ...LISTING_SOURCE_FIXTURES[0], id: "LIVE-SOURCE-1" }],
      listings: [{ ...LISTING_FIXTURES[0], id: "LIVE-LISTING-1" }],
      candidates: [],
      siteReviews: [],
    } as any;
    // Actual NetworkScoringService fixture DTOs supply renderer prerequisites.
    // An id-only record is not a scorecard or a candidate Gate contract.
    const scoringCandidatePrerequisites = {
    "id": "CS-1001",
    "listingId": "L-2024",
    "heatZoneId": "HZ-01",
    "title": "信義松仁候選點",
    "zoneLabel": "信義松仁 86",
    "address": "台北市信義區松仁路 96 號 1F",
    "district": "台北市信義區",
    "modelVersion": "SiteScore v2.3",
    "datasetSnapshotId": "FS-20260704-0600",
    "stage": "evaluated",
    "gate": {
        "state": "ready",
        "passed": true,
        "missing": [],
        "otherMissing": [],
        "blockNote": "",
        "checks": [
            {
                "key": "address",
                "label": "地址定位",
                "state": "ok",
                "note": "台北市信義區松仁路 96 號 1F"
            },
            {
                "key": "geocode",
                "label": "Geocode ≥ 0.80",
                "state": "ok",
                "note": "0.94（高）"
            },
            {
                "key": "rent",
                "label": "租金",
                "state": "ok",
                "note": "NT$58,000／月（區間 P45）"
            },
            {
                "key": "area",
                "label": "坪數",
                "state": "ok",
                "note": "18 坪"
            },
            {
                "key": "floor",
                "label": "樓層",
                "state": "ok",
                "note": "1F 臨路"
            },
            {
                "key": "hardRule",
                "label": "Hard rule",
                "state": "ok",
                "note": "3/3 通過（面積／樓層／用途）"
            }
        ],
        "okCount": 6,
        "totalCount": 6
    },
    "scored": true,
    "score": 82,
    "recommendation": "GO",
    "reviewId": null,
    "inCompare": true
};
    const scorecardPrerequisites = {
    "id": "CS-1001",
    "title": "信義松仁候選點",
    "zoneLabel": "信義松仁 86",
    "heatZoneId": "HZ-01",
    "score": 82,
    "recommendation": "GO",
    "modelVersion": "SiteScore v2.3",
    "datasetSnapshotId": "FS-20260704-0600",
    "generatedAt": "2026-07-04 06:10",
    "confidence": "中高",
    "payback": "22 個月",
    "revenuePath": {
        "m1": 182,
        "m3": 268,
        "m6": 342,
        "m12": 428
    },
    "band": {
        "p10": "NT$356K",
        "p50": "NT$428K",
        "p90": "NT$512K"
    },
    "subScores": {
        "rentReasonableness": "合理（區間 P45）",
        "cannibalization": "4%（低）",
        "competition": "中（無 24h 競店）",
        "demand": "強（夜間人流 84）",
        "poiFit": "高",
        "access": "中（週末停車不易）"
    },
    "capex": "NT$1.6M（14 台配置）",
    "rentAssumption": "月租 NT$58,000（區間 P45）",
    "drivers": [
        "夜間人流指數 84",
        "千戶級社區 6 個",
        "無 24h 競店"
    ],
    "reasons": [
        "住宅＋商辦混合，夜間洗烘需求強",
        "既有店 650m，稀釋僅 4%",
        "降租後租金落於區間 P45，仍可接受"
    ],
    "risks": [
        "週末停車不易",
        "租約含 3% 年調條款",
        "晚間人流資料信心中等"
    ],
    "conditions": [],
    "conditionTitle": "",
    "reviewId": null
};
    const compareColumnPrerequisites = {
    "id": "CS-1001",
    "title": "信義松仁候選點",
    "priority": "P1",
    "recommendation": "GO",
    "score": 82,
    "isBest": true
};
    const scoringSnapshot = {
      source: "api" as const,
      modelVersion: "sitescore-production-v1",
      candidates: [{ ...scoringCandidatePrerequisites, id: "LIVE-CANDIDATE-1" }],
      scorecards: [{ ...scorecardPrerequisites, id: "LIVE-CANDIDATE-1", candidateId: "LIVE-CANDIDATE-1" }],
      batchResults: [],
      compare: { columns: [{ ...compareColumnPrerequisites, id: "LIVE-CANDIDATE-1" }], metrics: [], recommendation: null, empty: false },
      compareSet: [],
    } as any;
    const rebalanceSnapshot = {
      source: "api" as const,
      stores: [{ id: "LIVE-STORE-1" }],
    } as any;
    // Actual legacy NetworkReviewService DTO supplies safe Review renderer fields.
    const legacyReviewPrerequisites = {
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
    const reviewsSnapshot = {
      source: "api" as const,
      reviews: [{ ...legacyReviewPrerequisites, id: "LIVE-REVIEW-1" }],
    } as any;

    expect(inspectNetworkListingsSnapshot(listingSnapshot)).toBe("ready");
    expect(inspectNetworkScoringSnapshot(scoringSnapshot)).toBe("ready");
    expect(inspectNetworkListingsSnapshot({ ...listingSnapshot, candidates: [null] })).toBe("error");
    expect(inspectNetworkScoringSnapshot({ ...scoringSnapshot,
      candidates: [{ ...scoringSnapshot.candidates[0], gate: null }] })).toBe("error");
    expect(inspectNetworkScoringSnapshot({ ...scoringSnapshot,
      scorecards: [{ ...scoringSnapshot.scorecards[0], band: null }] })).toBe("error");
    expect(inspectNetworkRebalanceSnapshot(rebalanceSnapshot)).toBe("ready");
    expect(inspectNetworkReviewsSnapshot(reviewsSnapshot)).toBe("ready");

    expect(inspectNetworkListingsSnapshot({ ...listingSnapshot, source: "fixture" })).toBe("seed");
    expect(
      inspectNetworkListingsSnapshot({ ...listingSnapshot, heatZones: [], listings: [], candidates: [] }),
    ).toBe("empty");
    // Withheld zone aggregates do not void authorized listing rows.
    expect(inspectNetworkListingsSnapshot({ ...listingSnapshot, heatZones: [] })).toBe("ready");
    // Malformed collections and seed payloads remain refused.
    expect(inspectNetworkListingsSnapshot({ ...listingSnapshot, heatZones: undefined })).toBe("empty");
    expect(inspectNetworkListingsSnapshot({ ...listingSnapshot, source: undefined })).toBe("empty");
    // A canonical candidate waiting for its worker has no scorecard/ranking.
    expect(inspectNetworkScoringSnapshot({ ...scoringSnapshot, source: "canonical", scorecards: [],
      compare: { columns: [], metrics: [], recommendation: null, empty: true } })).toBe("ready");
    expect(inspectNetworkScoringSnapshot({ ...scoringSnapshot, source: "canonical" })).toBe("ready");
    expect(inspectNetworkScoringSnapshot({ ...scoringSnapshot, candidates: [], scorecards: [] })).toBe("empty");
    expect(inspectNetworkScoringSnapshot({ ...scoringSnapshot, candidates: undefined })).toBe("empty");
    expect(inspectNetworkScoringSnapshot({ ...scoringSnapshot, source: "fixture" })).toBe("seed");
    expect(inspectNetworkRebalanceSnapshot({ ...rebalanceSnapshot, stores: [] })).toBe("empty");
    expect(inspectNetworkReviewsSnapshot({ ...reviewsSnapshot, reviews: [] })).toBe("empty");
  });

  it("does not let unrelated Network snapshots gate Listing Radar intake", () => {
    expect(resolveNetworkTabGateState({
      activeTab: 1,
      fixturesAllowed: false,
      findAreasLoadState: "error",
      scoringLoadState: "loading",
      reviewsLoadState: "empty",
      rebalanceLoadState: "error",
    })).toBeNull();
  });

  it("keeps unavailable data scoped to the tab that owns it", () => {
    const states = {
      fixturesAllowed: false,
      findAreasLoadState: "ready" as const,
      scoringLoadState: "error" as const,
      reviewsLoadState: "loading" as const,
      rebalanceLoadState: "empty" as const,
    };

    expect(resolveNetworkTabGateState({ activeTab: 0, ...states })).toBeNull();
    expect(resolveNetworkTabGateState({ activeTab: 2, ...states })).toBe("error");
    expect(resolveNetworkTabGateState({ activeTab: 5, ...states })).toBe("loading");
    expect(resolveNetworkTabGateState({ activeTab: 6, ...states })).toBe("empty");
  });

  it("gates Find Areas only on the scoped operator snapshot it renders", () => {
    const states = {
      fixturesAllowed: false,
      scoringLoadState: "error" as const,
      reviewsLoadState: "error" as const,
      rebalanceLoadState: "error" as const,
    };
    expect(resolveNetworkTabGateState({ activeTab: 0, findAreasLoadState: "ready", ...states })).toBeNull();
    expect(resolveNetworkTabGateState({ activeTab: 0, findAreasLoadState: "empty", ...states })).toBe("empty");
    expect(resolveNetworkTabGateState({ activeTab: 0, findAreasLoadState: "error", ...states })).toBe("error");
    expect(resolveNetworkTabGateState({ activeTab: 0, findAreasLoadState: "seed", ...states })).toBe("seed");
  });

  it("reports Find Areas empty when a ready scoped snapshot withholds HeatZones", () => {
    expect(resolveNetworkFindAreasLoadState("ready", 0)).toBe("empty");
    expect(resolveNetworkFindAreasLoadState("ready", 2)).toBe("ready");
    for (const state of ["error", "seed", "empty", "loading"] as const) {
      expect(resolveNetworkFindAreasLoadState(state, 0)).toBe(state);
    }
  });

  it("classifies a received 403 envelope as an authenticated denial with its correlation ID", async () => {
    const refused = new Response(
      JSON.stringify({
        detail: "role does not permit view on operator_network",
        error: { code: "forbidden", message: "role does not permit view on operator_network", correlation_id: "corr-real-403" },
      }),
      { status: 403, headers: { "Content-Type": "application/json", "X-Correlation-Id": "corr-header" } },
    );
    // The label deliberately contains "network": the path must not decide the kind.
    const failure = await operatorLoadFailureFromResponse(refused, "network-listings", "corr-request");
    expect(failure).toMatchObject({ correlationId: "corr-real-403", httpStatus: 403, kind: "forbidden" });
    expect(failure.technicalDetail).toContain("network-listings returned 403 forbidden");

    const bare = await operatorLoadFailureFromResponse(new Response("denied", { status: 403 }), "network-scoring", "corr-request");
    expect(bare).toMatchObject({ correlationId: "corr-request", httpStatus: 403, kind: "forbidden" });

    const storeOps = await operatorLoadFailureFromResponse(
      new Response(JSON.stringify({ detail: "STORE_OPS_LIVE_DATA_UNAVAILABLE" }), { status: 503 }),
      "store-ops",
    );
    expect(storeOps).toMatchObject({ httpStatus: 503, kind: "server" });

    expect(operatorLoadFailureFromError(new TypeError("Failed to fetch"), "network-listings").kind).toBe("network");
  });

  it("only counts collections that were actually read", () => {
    expect(resolveNetworkCountState("ready", null, false)).toEqual({ known: true });
    // A scoped row-level 200 cannot turn withheld aggregate counts into zero.
    expect(resolveNetworkCountState("ready", null, false, true)).toEqual({
      known: false,
      label: "未授權",
      reason: "withheld",
    });
    expect(resolveNetworkCountState("ready", null, true, true)).toEqual({ known: true });
    // An authorized empty 200 is an authoritative zero.
    expect(resolveNetworkCountState("empty", null, false)).toEqual({ known: true });
    expect(resolveNetworkCountState("error", { kind: "forbidden", httpStatus: 403 }, false)).toEqual({
      known: false,
      label: "未授權",
      reason: "unread",
    });
    expect(resolveNetworkCountState("error", { kind: "server", httpStatus: 503 }, false)).toMatchObject({
      known: false,
      label: "無法取得",
    });
    expect(resolveNetworkCountState("loading", null, false)).toMatchObject({ known: false, reason: "pending" });
    expect(resolveNetworkCountState("seed", null, false)).toMatchObject({ known: false, reason: "unread" });
    expect(resolveNetworkCountState("error", null, true)).toEqual({ known: true });
  });

  it("derives Governance decision authority from the server, never the persona", () => {
    const readAdmin = normalizeGovernanceActionAuthority({
      verified: true,
      systemRoles: ["auditor", "platform_admin"],
      decide: false,
      exportEvidence: false,
    });
    expect(resolveGovernanceDecisionAuthority({ callerAllows: true, fixturesAllowed: false, serverAuthority: readAdmin }))
      .toEqual({ canDecide: false, canExport: false, state: "denied", systemRoles: ["auditor", "platform_admin"] });

    const manager = normalizeGovernanceActionAuthority({
      verified: true,
      systemRoles: ["operations_manager"],
      decide: true,
      exportEvidence: true,
    });
    expect(resolveGovernanceDecisionAuthority({ callerAllows: true, fixturesAllowed: false, serverAuthority: manager }))
      .toMatchObject({ canDecide: true, canExport: true, state: "granted" });
    // The caller may narrow but never widen.
    expect(resolveGovernanceDecisionAuthority({ callerAllows: false, fixturesAllowed: false, serverAuthority: manager }))
      .toMatchObject({ canDecide: false, state: "denied" });

    // Missing, unverified or malformed authority confirms nothing in production.
    for (const value of [undefined, null, "yes", { decide: true }, { verified: "true", decide: true }]) {
      expect(
        resolveGovernanceDecisionAuthority({
          callerAllows: true,
          fixturesAllowed: false,
          serverAuthority: normalizeGovernanceActionAuthority(value),
        }),
      ).toMatchObject({ canDecide: false, canExport: false });
    }
    expect(normalizeGovernanceActionAuthority({ verified: true, decide: "true", exportEvidence: 1 }))
      .toEqual({ verified: true, systemRoles: [], decide: false, exportEvidence: false });
  });
});
