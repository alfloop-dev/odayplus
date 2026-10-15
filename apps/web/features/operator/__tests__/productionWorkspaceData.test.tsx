import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DesignStoreOpsWorkspace,
  DesignTodayWorkspace,
  inspectStoreOpsApiPayload,
  normalizeStoreOpsApiHealth,
  hasRecordedCameraPurpose,
} from "../DesignAlignedWorkspaces";
import type { AuditEvent, EvidenceItem, Issue } from "../types";
import nativeCameraPurpose from "../../../../../tests/fixtures/operator-store-camera-purpose-native.json";
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
import { EVIDENCE_FIXTURES, ISSUE_FIXTURES, HEAT_ZONE_FIXTURES, LISTING_FIXTURES, LISTING_SOURCE_FIXTURES } from "../fixtures";
import { normalizeGovernanceActionAuthority } from "../governance/governanceLoader";
import { getEvidenceStrength, getTrendPoints } from "../storeOpsViewModel";
import { STORE_OPS_REFRESH_EVENT } from "../storeOpsWorkflowTypes";
import {
  operatorLoadFailureFromError,
  operatorLoadFailureFromResponse,
} from "../operatorDataMode";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

describe("recorded CameraPurpose native producer contract", () => {
  function proof() {
    return {
      camera: structuredClone(nativeCameraPurpose.evidenceItem) as EvidenceItem,
      issue: structuredClone(nativeCameraPurpose.snapshot.issues.find((row) => row.id === "ISS-1024")) as Issue,
      audits: [structuredClone(nativeCameraPurpose.auditEvent) as AuditEvent],
    };
  }
  type Proof = ReturnType<typeof proof>;
  const blankIdentityCases: Array<[string, (record: Proof) => void]> = [
    ...["", " \t\n"].map((id): [string, (record: Proof) => void] => [
      id === "" ? "empty current issue identity" : "whitespace current issue identity",
      ({ camera, issue, audits }) => {
        issue.id = id;
        camera.issueId = id;
        audits[0].targetId = id;
        audits[0].metadata!.issueId = id;
      },
    ]),
    ...["", " \t\n"].map((id): [string, (record: Proof) => void] => [
      id === "" ? "empty current evidence identity" : "whitespace current evidence identity",
      ({ camera, issue, audits }) => {
        camera.id = id;
        issue.evidenceIds = [id];
        audits[0].metadata!.evidenceId = id;
      },
    ]),
  ];
  const rejected: Array<[string, (record: Proof) => void]> = [
    ...blankIdentityCases,
    ["empty evidence issue identity", ({ camera }) => { camera.issueId = ""; }],
    ["whitespace evidence issue identity", ({ camera }) => { camera.issueId = " \t\n"; }],
    ["missing purpose", ({ camera }) => { delete camera.purpose; }],
    ["blank purpose", ({ camera }) => { camera.purpose = " "; }],
    ["missing camera location", ({ camera }) => { delete camera.cameraLocation; }],
    ["missing time window", ({ camera }) => { delete camera.timeWindow; }],
    ["missing unlocked timestamp", ({ camera }) => { delete camera.unlockedAt; }],
    ["invalid unlocked timestamp", ({ camera }) => { camera.unlockedAt = "not a timestamp"; }],
    ["invalid normalized calendar date", ({ camera }) => { camera.unlockedAt = "2026-02-30T00:00:00Z"; }],
    ["invalid normalized hour", ({ camera, audits }) => {
      camera.unlockedAt = "2026-10-14T24:00:00Z";
      audits[0].occurredAt = "2026-10-16T00:00:00Z";
    }],
    ["zero retention", ({ camera }) => { camera.retentionHours = 0; }],
    ["excess retention", ({ camera }) => { camera.retentionHours = 73; }],
    ["fractional retention", ({ camera }) => { camera.retentionHours = 24.5; }],
    ["missing audit", (record) => { record.audits = []; }],
    ["wrong audit action", ({ audits }) => { audits[0].action = "issue.triaged"; }],
    ["wrong audit category", ({ audits }) => { audits[0].category = "workflow"; }],
    ["wrong audit target", ({ audits }) => { audits[0].targetId = "OTHER-ISSUE"; }],
    ["wrong audit target type", ({ audits }) => { audits[0].targetType = "listing"; }],
    ["wrong audit issue", ({ audits }) => { audits[0].metadata!.issueId = "OTHER-ISSUE"; }],
    ["wrong audit evidence", ({ audits }) => { audits[0].metadata!.evidenceId = "OTHER-EVIDENCE"; }],
    ["wrong audit purpose", ({ audits }) => { audits[0].metadata!.purpose = "different purpose"; }],
    ["wrong audit location", ({ audits }) => { audits[0].metadata!.cameraLocation = "different camera"; }],
    ["wrong audit window", ({ audits }) => { audits[0].metadata!.timeWindow = "different time window"; }],
    ["wrong audit retention", ({ audits }) => { audits[0].metadata!.retentionHours = 48; }],
    ["older audit", ({ audits }) => { audits[0].occurredAt = "2001-01-01T00:00:00Z"; }],
    ["older audit within the native microsecond timestamp's same millisecond", ({ camera, audits }) => {
      camera.unlockedAt = "2026-10-14T04:00:00.000200+00:00";
      audits[0].occurredAt = "2026-10-14T04:00:00.000100+00:00";
    }],
    ["invalid audit timestamp", ({ audits }) => { audits[0].occurredAt = "not a timestamp"; }],
    ["another issue's camera", ({ camera }) => { camera.issueId = "OTHER-ISSUE"; }],
    ["non-camera evidence", ({ camera }) => { camera.kind = "payment"; }],
    ["camera absent from issue evidence", ({ issue }) => { issue.evidenceIds = []; }],
    ["current source still locked", ({ camera }) => { camera.lockedReason = "Current source refuses access"; }],
  ];

  it("recognizes the complete actual native memory producer receipt", () => {
    const { camera, issue, audits } = proof();
    expect(hasRecordedCameraPurpose(camera, issue, audits)).toBe(true);
  });

  it.each([
    "2026-10-14T04:00:00.000300+00:00",
    "2026-10-14T04:00:00.000200000Z",
    "2026-10-14T12:00:00.000200+08:00",
  ])("retains exact or later native purpose audit precision for %s", (occurredAt) => {
    const { camera, issue, audits } = proof();
    camera.unlockedAt = "2026-10-14T04:00:00.000200+00:00";
    audits[0].occurredAt = occurredAt;
    expect(hasRecordedCameraPurpose(camera, issue, audits)).toBe(true);
  });

  it.each(rejected)("does not infer unlock from %s", (_name, mutate) => {
    const record = proof();
    mutate(record);
    expect(hasRecordedCameraPurpose(record.camera, record.issue, record.audits)).toBe(false);
  });

  it.each(blankIdentityCases)("does not render an unlock from matching invalid API %s", async (_name, mutate) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const record = proof();
    mutate(record);
    const snapshot = { ...structuredClone(nativeCameraPurpose.snapshot),
      issues: [record.issue], evidence: [record.camera], auditEvents: record.audits,
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(snapshot), {
      status: 200, headers: { "Content-Type": "application/json" },
    })));
    render(<DesignStoreOpsWorkspace onOpenWorkflow={vi.fn()} />);
    // The existing general DTO shape guard accepts these string identities.
    // The camera proof must still fail closed while source facts stay visible.
    expect((await screen.findAllByText(nativeCameraPurpose.evidenceItem.title)).length).toBeGreaterThan(0);
    expect(screen.queryByText("影像已解鎖")).not.toBeInTheDocument();
  });

  it.each(["true", "false"])("renders recorded unlock from native receipt without reviving demo health when production=%s", async (productionMode) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", productionMode);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(nativeCameraPurpose.snapshot), {
      status: 200, headers: { "Content-Type": "application/json" },
    })));
    render(<DesignStoreOpsWorkspace initialIssueId="ISS-1024" onOpenWorkflow={vi.fn()} />);
    expect(await screen.findByText("影像已解鎖")).toBeInTheDocument();
    expect(screen.queryByText("14 台")).not.toBeInTheDocument();
    expect(screen.queryByText("交易正常")).not.toBeInTheDocument();
  });

  it.each(["true", "false"])("keeps missing-lock-only source facts when production=%s", async (productionMode) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", productionMode);
    const snapshot = structuredClone(nativeCameraPurpose.snapshot);
    const camera = snapshot.evidence.find((row) => row.id === nativeCameraPurpose.evidenceItem.id)!;
    // Remove all purpose proof while retaining the actual source title/summary.
    for (const key of ["purpose", "cameraLocation", "timeWindow", "retentionHours", "unlockedAt"])
      delete (camera as Record<string, unknown>)[key];
    snapshot.auditEvents = snapshot.auditEvents.filter((row) => row.action !== "evidence.camera_purpose.recorded");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(snapshot), {
      status: 200, headers: { "Content-Type": "application/json" },
    })));
    render(<DesignStoreOpsWorkspace initialIssueId="ISS-1024" onOpenWorkflow={vi.fn()} />);
    await screen.findByLabelText("ISS-1024 detail");
    expect(screen.getAllByText(nativeCameraPurpose.evidenceItem.title).length).toBeGreaterThan(0);
    expect(screen.queryByText("影像已解鎖")).not.toBeInTheDocument();
  });
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

  const canonicalStoreOpsPayload = {
    source: "operator-store-ops-production",
    stores: [{
      id: "LIVE-STORE-1", name: "正式門市", district: "正式行政區", city: "台北市", manager: "正式負責人",
      lights: { demand: "green", operations: "yellow", staffing: "green", margin: "green" }, riskScore: 15,
    }],
    issues: [{
      id: "LIVE-ISSUE-1", title: "來源事件標題", storeId: "LIVE-STORE-1", storeName: "正式門市",
      status: "new", severity: "medium", source: "multiSignal", ownerRoleId: "opsLead", ownerName: "正式負責人",
      slaDueAt: "2026-10-14T12:00:00Z", createdAt: "2026-10-14T01:00:00Z", updatedAt: "2026-10-14T01:00:00Z",
      evidenceIds: [], summary: "來源事件摘要",
    }],
    evidence: [], auditEvents: [], fourLightSummary: [], count: 1,
  };

  const unavailableStorePayloads: Array<[string, unknown]> = [
    ["null", null], ["string", "unexpected"], ["number", 42], ["boolean", true],
    ["array", []], ["array of snapshots", [canonicalStoreOpsPayload]],
    ["missing collections", {}],
    ...["issues", "stores", "evidence", "auditEvents"].flatMap((field): Array<[string, unknown]> => [
      [`${field} object`, { ...canonicalStoreOpsPayload, [field]: {} }],
      [`${field} null`, { ...canonicalStoreOpsPayload, [field]: null }],
      [`${field} string`, { ...canonicalStoreOpsPayload, [field]: "unexpected" }],
      [`${field} null row`, { ...canonicalStoreOpsPayload, [field]: [null] }],
    ]),
    ["issue object title", { ...canonicalStoreOpsPayload, issues: [{ ...canonicalStoreOpsPayload.issues[0], title: {} }] }],
    ["issue nonarray evidence IDs", { ...canonicalStoreOpsPayload, issues: [{ ...canonicalStoreOpsPayload.issues[0], evidenceIds: {} }] }],
    ["store missing lights", { ...canonicalStoreOpsPayload, stores: [{ ...canonicalStoreOpsPayload.stores[0], lights: null }] }],
    ["store incomplete lights", { ...canonicalStoreOpsPayload, stores: [{ ...canonicalStoreOpsPayload.stores[0], lights: {} }] }],
    ["store array-valued light", { ...canonicalStoreOpsPayload, stores: [{ ...canonicalStoreOpsPayload.stores[0], lights: {
      ...canonicalStoreOpsPayload.stores[0].lights, demand: ["green"],
    } }] }],
    ["store unknown light", { ...canonicalStoreOpsPayload, stores: [{ ...canonicalStoreOpsPayload.stores[0], lights: {
      ...canonicalStoreOpsPayload.stores[0].lights, demand: "unknown",
    } }] }],
    ["seed malformed collection", { ...canonicalStoreOpsPayload, source: "fixture-store-ops", issues: {} }],
    ["seed malformed row", { ...canonicalStoreOpsPayload, source: "fixture-store-ops", stores: [null] }],
  ];
  it.each(unavailableStorePayloads)("keeps a successful unknown Store API %s unavailable without demo rows or a render crash", async (_shape, payload) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const onOpenWorkflow = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), {
      status: 200, headers: { "Content-Type": "application/json" },
    })));

    render(<DesignStoreOpsWorkspace onOpenWorkflow={onOpenWorkflow} />);

    const gate = await screen.findByTestId("operator-data-unavailable");
    expect(gate).toHaveAttribute("data-status", "empty");
    expect(screen.queryByLabelText("門市 Issue queue")).not.toBeInTheDocument();
    expect(screen.queryByText("14 台")).not.toBeInTheDocument();
    expect(screen.queryByText("0 件 正常")).not.toBeInTheDocument();
    expect(onOpenWorkflow).not.toHaveBeenCalled();
  });

  it("preserves the initial local demo after a transport failure before any successful API response", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Owned test transport refusal")));
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);

    render(<DesignStoreOpsWorkspace onOpenWorkflow={vi.fn()} />);

    await waitFor(() => expect(error).toHaveBeenCalled());
    expect(screen.getByLabelText("ISS-1024 detail")).toBeInTheDocument();
    expect(screen.getByText("14 台")).toBeInTheDocument();
    expect(screen.queryByTestId("operator-data-unavailable")).not.toBeInTheDocument();
  });

  it("does not revive the initial demo after an empty successful response and a failed retry", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response("null", { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockRejectedValue(new Error("Owned retry transport refusal")));
    render(<DesignStoreOpsWorkspace onOpenWorkflow={vi.fn()} />);
    const gate = await screen.findByTestId("operator-data-unavailable");
    expect(gate).toHaveAttribute("data-status", "empty");

    fireEvent.click(screen.getByRole("button", { name: "重新載入" }));

    await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error"));
    expect(screen.queryByLabelText("門市 Issue queue")).not.toBeInTheDocument();
    expect(screen.queryByText("14 台")).not.toBeInTheDocument();
  });

  it("clears previously explicit seed rows when a successful refresh body is unknown", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...canonicalStoreOpsPayload, source: "fixture-store-ops" }), {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValue(new Response("null", { status: 200, headers: { "Content-Type": "application/json" } })));
    render(<DesignStoreOpsWorkspace onOpenWorkflow={vi.fn()} />);
    await screen.findByLabelText("LIVE-ISSUE-1 detail");
    expect(screen.getByText("14 台")).toBeInTheDocument();

    window.dispatchEvent(new Event(STORE_OPS_REFRESH_EVENT));

    expect(await screen.findByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "empty");
    expect(screen.queryByLabelText("LIVE-ISSUE-1 detail")).not.toBeInTheDocument();
    expect(screen.queryByText("14 台")).not.toBeInTheDocument();
  });

  it("keeps an undecodable successful API body unavailable", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{invalid JSON", {
      status: 200, headers: { "Content-Type": "application/json" },
    })));
    render(<DesignStoreOpsWorkspace onOpenWorkflow={vi.fn()} />);

    const gate = await screen.findByTestId("operator-data-unavailable");
    await waitFor(() => expect(gate).toHaveAttribute("data-status", "error"));
    expect(screen.queryByLabelText("門市 Issue queue")).not.toBeInTheDocument();
    expect(screen.queryByText("14 台")).not.toBeInTheDocument();
  });

  it("retains a valid explicitly seeded local response as a fixture", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ...canonicalStoreOpsPayload, source: "fixture-store-ops",
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    render(<DesignStoreOpsWorkspace onOpenWorkflow={vi.fn()} />);

    await screen.findByLabelText("LIVE-ISSUE-1 detail");
    expect(screen.getByText("14 台")).toBeInTheDocument();
    expect(screen.queryByTestId("operator-data-unavailable")).not.toBeInTheDocument();
  });

  it.each(["true", "false"])("does not fill canonical Store API gaps with demo metrics or healthy evidence when production=%s", async (productionMode) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", productionMode);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(canonicalStoreOpsPayload), {
      status: 200, headers: { "Content-Type": "application/json" },
    })));

    render(<DesignStoreOpsWorkspace onOpenWorkflow={vi.fn()} />);

    const detail = await screen.findByLabelText("LIVE-ISSUE-1 detail");
    const strip = detail.querySelector('[class*="storeStrip"]')!;
    expect(strip.children).toHaveLength(5);
    expect(strip).toHaveTextContent("正式門市");
    expect(within(strip as HTMLElement).getAllByText("不可用")).toHaveLength(3);
    expect(strip).not.toHaveTextContent(/自助洗衣|14 台|18,420|14,200|9,500|NT\$/);
    const fusion = within(detail).getByRole("heading", { name: "證據融合" }).closest("section")!;
    expect(within(fusion).getAllByText("資料不可用")).toHaveLength(6);
    expect(fusion).not.toHaveTextContent(/4\.8|0 件|正常|在線|健康|皆綠/);
    expect(screen.getByLabelText("門市 Issue queue")).toBeInTheDocument();
    expect(screen.getByLabelText("Action rail")).toBeInTheDocument();
  });

  it.each(["true", "false"])("renders source evidence titles without inventing ratings/status and keeps masked confidence or missing forecasts unavailable when production=%s", async (productionMode) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", productionMode);
    const kinds = ["googleReview", "csCase", "camera", "payment", "iot", "forecastOps"];
    const evidence = kinds.map((kind, index) => ({
      id: `LIVE-EVIDENCE-${index}`, issueId: "LIVE-ISSUE-1", kind, title: `來源證據 ${kind}`,
      sourceLabel: `正式來源 ${kind}`, summary: `來源摘要 ${kind}`, polarity: "neutral",
      confidence: index === 0 ? null : 0.91, occurredAt: "2026-10-14T01:00:00Z",
    }));
    const payload = {
      ...canonicalStoreOpsPayload,
      issues: [{ ...canonicalStoreOpsPayload.issues[0], evidenceIds: evidence.map((row) => row.id) }], evidence,
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), {
      status: 200, headers: { "Content-Type": "application/json" },
    })));

    render(<DesignStoreOpsWorkspace onOpenWorkflow={vi.fn()} />);

    const detail = await screen.findByLabelText("LIVE-ISSUE-1 detail");
    const fusion = within(detail).getByRole("heading", { name: "證據融合" }).closest("section")!;
    for (const kind of kinds) expect(within(fusion).getByText(`來源證據 ${kind}`)).toBeInTheDocument();
    expect(fusion).not.toHaveTextContent(/★|未回覆|Zendesk 異常|交易延遲警告|Telemetry 警告|四燈評估警告|影像已解鎖/);
    const panel = within(screen.getByRole("tabpanel"));
    const confidence = panel.getByText("信心度").closest("div")!;
    expect(confidence).toHaveTextContent("不可用");
    expect(confidence).not.toHaveTextContent("0%");
    fireEvent.click(screen.getByRole("tab", { name: "ForecastOps" }));
    expect(screen.getByTestId("store-forecast-unavailable")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /28 天/ })).not.toBeInTheDocument();
  });

  it.each(["empty", "missing"])("keeps a partial canonical API with %s stores unavailable in local demo mode", async (storeArrayState) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const payload: Record<string, unknown> = { ...canonicalStoreOpsPayload, stores: [] };
    if (storeArrayState === "missing") delete payload.stores;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), {
      status: 200, headers: { "Content-Type": "application/json" },
    })));

    render(<DesignStoreOpsWorkspace onOpenWorkflow={vi.fn()} />);

    const gate = await screen.findByTestId("operator-data-unavailable");
    expect(gate).toHaveAttribute("data-status", "empty");
    expect(screen.queryByLabelText("LIVE-ISSUE-1 detail")).not.toBeInTheDocument();
    expect(screen.queryByText("14 台")).not.toBeInTheDocument();
    expect(screen.queryByText("0 件 正常")).not.toBeInTheDocument();
  });

  it("preserves source-provided fixture confidence metadata while camera images remain purpose-locked", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => new Promise(() => {})));

    render(<DesignStoreOpsWorkspace onOpenWorkflow={vi.fn()} />);

    fireEvent.click(screen.getByRole("tab", { name: "Camera" }));
    const panel = within(screen.getByRole("tabpanel"));
    expect(panel.getByText("信心度").closest("div")).toHaveTextContent("70%");
    expect(panel.getByRole("button", { name: "輸入調閱目的以檢視" })).toBeInTheDocument();
    expect(screen.getByText("14 台")).toBeInTheDocument();
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

  describe("canonical partial health preserves real workflows", () => {
    // Explicit synthetic HTTP unit inputs, never native KPI/source approval.
    const slot = () => ({ state: "available" as const, source: "synthetic-http-unit-record", recordIds: ["unit-record-1"] });
    const missingSlot = () => ({ state: "unavailable" as const, source: null });
    const partialStore = () => ({
      ...canonicalStoreOpsPayload.stores[0],
      lights: { demand: "red", operations: null, staffing: null, margin: null }, riskScore: null,
      healthAvailability: {
        state: "degraded", complete: false,
        lights: { demand: slot(), operations: missingSlot(), staffing: missingSlot(), margin: missingSlot() },
        riskScore: missingSlot(),
      },
    });
    const completeStore = () => ({
      ...canonicalStoreOpsPayload.stores[0],
      lights: { demand: "green", operations: "green", staffing: "green", margin: "green" }, riskScore: 0,
      healthAvailability: {
        state: "available", complete: true,
        lights: { demand: slot(), operations: slot(), staffing: slot(), margin: slot() }, riskScore: slot(),
      },
    });
    function mockCanonical(payload: unknown, productionMode = "true") {
      vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", productionMode);
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), {
        status: 200, headers: { "Content-Type": "application/json" },
      })));
    }

    it.each(["true", "false"])("masks only valid legacy health without provenance and preserves the actual issue action when production=%s", async (mode) => {
      const payload = structuredClone(canonicalStoreOpsPayload);
      const before = JSON.stringify(payload);
      expect(normalizeStoreOpsApiHealth(payload)).toMatchObject({
        stores: [{ lights: { demand: null, operations: null, staffing: null, margin: null }, riskScore: null }],
        healthAvailability: { complete: false, coveredCount: 0, unavailableCount: 1, warningCount: 0 },
      });
      expect(JSON.stringify(payload)).toBe(before);
      mockCanonical(payload, mode);
      const onOpenWorkflow = vi.fn();
      render(<DesignStoreOpsWorkspace onOpenWorkflow={onOpenWorkflow} />);
      const detail = await screen.findByLabelText("LIVE-ISSUE-1 detail");
      const strip = detail.querySelector('[class*="storeStrip"]')!;
      expect(strip).toHaveTextContent("資料不可用");
      expect(strip.querySelectorAll("[data-status], i")).toHaveLength(0);
      expect(detail).toHaveTextContent("來源事件標題");
      expect(screen.queryByTestId("operator-data-unavailable")).not.toBeInTheDocument();
      expect(screen.getByText(/PARTIAL，1 家資料不完整/)).toBeInTheDocument();
      fireEvent.click(within(screen.getByLabelText("Action rail")).getAllByRole("button")[0]);
      expect(onOpenWorkflow).toHaveBeenCalledWith("triage", payload.issues[0]);
    });

    it.each(["colour", "risk"])("does not repair a malformed legacy %s into accepted missing health", async (field) => {
      const payload = {
        ...canonicalStoreOpsPayload,
        stores: [{ ...canonicalStoreOpsPayload.stores[0], ...(field === "risk"
          ? { riskScore: "0" }
          : { lights: { ...canonicalStoreOpsPayload.stores[0].lights, demand: "blue" } }) }],
      };
      expect(normalizeStoreOpsApiHealth(payload)).toBe(payload);
      mockCanonical(payload);
      render(<DesignStoreOpsWorkspace onOpenWorkflow={vi.fn()} />);
      await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "empty"));
      expect(screen.queryByLabelText("Action rail")).not.toBeInTheDocument();
    });

    it("keeps a supported warning with incomplete coverage without counting it twice or painting unknown slots", async () => {
      const payload = { ...canonicalStoreOpsPayload, stores: [partialStore()] };
      expect(normalizeStoreOpsApiHealth(payload)).toMatchObject({
        stores: [{ lights: { demand: "red", operations: null, staffing: null, margin: null }, riskScore: null }],
        healthAvailability: { totalCount: 1, coveredCount: 0, unavailableCount: 1, warningCount: 1 },
      });
      mockCanonical(payload);
      render(<DesignStoreOpsWorkspace onOpenWorkflow={vi.fn()} />);
      const detail = await screen.findByLabelText("LIVE-ISSUE-1 detail");
      const strip = detail.querySelector('[class*="storeStrip"]')!;
      expect(strip.querySelectorAll("[data-status=red]")).toHaveLength(1);
      expect(strip.querySelectorAll("[data-status=green], [data-status=yellow]")).toHaveLength(0);
      expect(strip.querySelectorAll("i")).toHaveLength(1);
      expect(within(strip as HTMLElement).getAllByText(/資料不可用/)).toHaveLength(3);
      const overview = screen.getByRole("region", { name: "全店四燈摘要" });
      expect(overview).toHaveAttribute("data-health-state", "degraded");
      expect(within(overview).getByRole("button", { name: /^紅燈/ }).querySelector("strong")).toHaveTextContent("1");
      expect(within(overview).getByRole("button", { name: /^綠燈/ }).querySelector("strong")).toHaveTextContent("0");
      expect(overview).not.toHaveTextContent("營運正常");
    });

    it("does not classify known green lights as overall healthy when risk is unavailable or revive them through the green filter", async () => {
      const known = completeStore();
      const payload = { ...canonicalStoreOpsPayload, stores: [{ ...known, riskScore: null,
        healthAvailability: { ...known.healthAvailability, state: "degraded", complete: false, riskScore: missingSlot() },
      }] };
      mockCanonical(payload);
      const onOpenWorkflow = vi.fn();
      render(<DesignStoreOpsWorkspace onOpenWorkflow={onOpenWorkflow} />);
      await screen.findByLabelText("LIVE-ISSUE-1 detail");
      const green = screen.getByRole("button", { name: /^綠燈/ });
      expect(green.querySelector("strong")).toHaveTextContent("0");
      fireEvent.click(green);
      await screen.findByText("沒有符合條件的項目，請調整篩選。");
      expect(screen.queryByLabelText("Action rail")).not.toBeInTheDocument();
      expect(within(screen.getByLabelText("門市 Issue queue")).queryAllByRole("button", { name: /LIVE-ISSUE-1/ })).toHaveLength(0);
      expect(screen.queryByLabelText("LIVE-STORE-1 門市唯讀資訊")).not.toBeInTheDocument();
      expect(onOpenWorkflow).not.toHaveBeenCalled();
    });

    it("preserves a supported real zero and accepts optional recordIds without adding a client admission gate", async () => {
      const known = completeStore();
      const payload = { ...canonicalStoreOpsPayload, stores: [{ ...known,
        healthAvailability: { ...known.healthAvailability, riskScore: { ...slot(), recordIds: undefined } },
      }] };
      expect(normalizeStoreOpsApiHealth(payload)).toMatchObject({
        stores: [{ riskScore: 0 }], healthAvailability: { complete: true, coveredCount: 1, unavailableCount: 0 },
      });
      mockCanonical(payload);
      render(<DesignStoreOpsWorkspace onOpenWorkflow={vi.fn()} />);
      await screen.findByLabelText("LIVE-ISSUE-1 detail");
      expect(screen.getByRole("button", { name: /^綠燈/ }).querySelector("strong")).toHaveTextContent("1");
      expect(screen.getByText("營運正常")).toBeInTheDocument();
    });

    it("masks an invalid slot structure without hiding the usable issue", async () => {
      const store = partialStore();
      const payload = { ...canonicalStoreOpsPayload, stores: [{ ...store,
        healthAvailability: { ...store.healthAvailability, lights: { ...store.healthAvailability.lights,
          demand: { ...slot(), recordIds: [42] },
        } },
      }] };
      mockCanonical(payload);
      render(<DesignStoreOpsWorkspace onOpenWorkflow={vi.fn()} />);
      const detail = await screen.findByLabelText("LIVE-ISSUE-1 detail");
      expect(detail.querySelectorAll('[class*="storeStrip"] [data-status], [class*="storeStrip"] i')).toHaveLength(0);
      expect(screen.getByLabelText("Action rail")).toBeInTheDocument();
    });

    it("renders every real store with no issue in the existing read-only strips without creating issue controls", async () => {
      const payload = { ...canonicalStoreOpsPayload, issues: [], count: 0, stores: [
        { ...partialStore(), lights: { demand: null, operations: null, staffing: null, margin: null } },
        { ...partialStore(), id: "LIVE-STORE-2", name: "第二間真實門市", lights: { demand: null, operations: null, staffing: null, margin: null } },
      ] };
      expect(inspectStoreOpsApiPayload(payload)).toBe("ready");
      mockCanonical(payload);
      const onOpenWorkflow = vi.fn();
      render(<DesignStoreOpsWorkspace onOpenWorkflow={onOpenWorkflow} />);
      const first = await screen.findByLabelText("LIVE-STORE-1 門市唯讀資訊");
      const second = screen.getByLabelText("LIVE-STORE-2 門市唯讀資訊");
      expect(first).toHaveTextContent("LIVE-STORE-1 · 正式門市");
      expect(second).toHaveTextContent("LIVE-STORE-2 · 第二間真實門市");
      expect(first).toHaveTextContent("資料不可用");
      expect(first.querySelectorAll("button, [data-status], i")).toHaveLength(0);
      expect(within(screen.getByLabelText("門市 Issue queue")).getByText("目前沒有門市事件。")).toBeInTheDocument();
      expect(screen.queryByLabelText("Action rail")).not.toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "證據融合" })).not.toBeInTheDocument();
      expect(screen.queryByRole("tab")).not.toBeInTheDocument();
      expect(onOpenWorkflow).not.toHaveBeenCalled();
    });

    it("keeps a real store without an issue visible alongside a different store's real issue", async () => {
      mockCanonical({ ...canonicalStoreOpsPayload, stores: [partialStore(), { ...partialStore(), id: "LIVE-NO-ISSUE", name: "無事件門市" }] });
      render(<DesignStoreOpsWorkspace onOpenWorkflow={vi.fn()} />);
      await screen.findByLabelText("LIVE-ISSUE-1 detail");
      expect(screen.getByLabelText("LIVE-NO-ISSUE 門市唯讀資訊")).toHaveTextContent("無事件門市");
      expect(screen.getByLabelText("Action rail")).toBeInTheDocument();
    });

    it("keeps null canonical confidence unavailable in the existing evidence panel instead of claiming 100%", async () => {
      const evidence = { ...EVIDENCE_FIXTURES[0], id: "SYNTHETIC-FORECAST-EVIDENCE", issueId: "LIVE-ISSUE-1",
        kind: "forecastOps", confidence: null, title: "原生訊號來源", sourceLabel: "Synthetic HTTP unit input" };
      mockCanonical({ ...canonicalStoreOpsPayload, stores: [partialStore()], evidence: [evidence],
        issues: [{ ...canonicalStoreOpsPayload.issues[0], evidenceIds: [evidence.id] }],
      });
      render(<DesignStoreOpsWorkspace onOpenWorkflow={vi.fn()} />);
      await screen.findByLabelText("LIVE-ISSUE-1 detail");
      fireEvent.click(screen.getByRole("tab", { name: "ForecastOps" }));
      // Forecast graph stays unavailable and no confidence score is invented.
      expect(screen.getByTestId("store-forecast-unavailable")).toBeInTheDocument();
      expect(screen.getByLabelText("LIVE-ISSUE-1 detail")).not.toHaveTextContent("100%");
    });

    it("excludes missing confidence from observed averages with explicit coverage and retains null trend points", () => {
      const missing: EvidenceItem = { ...EVIDENCE_FIXTURES[0], confidence: null };
      const measured: EvidenceItem = { ...EVIDENCE_FIXTURES[1], confidence: 0.8 };
      expect(getEvidenceStrength([missing, measured])).toMatchObject({
        averageConfidence: 80, confidenceCount: 1, unavailableConfidenceCount: 1,
      });
      expect(getEvidenceStrength([missing]).averageConfidence).toBeNull();
      expect(getEvidenceStrength([{ ...measured, confidence: 0 }]).averageConfidence).toBe(0);
      const issue: Issue = { ...ISSUE_FIXTURES[0], id: "SYNTHETIC-CONFIDENCE-CONTROL" };
      expect(getTrendPoints(issue, [missing, measured]).map((point) => point.value)).toEqual([null, 80]);
    });
  });

});
