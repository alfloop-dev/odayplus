import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GrowthWorkspace } from "../GrowthWorkspace";
import {
  FIXTURE_FRESHNESS,
  buildGrowthViewModel,
  GROWTH_ITEMS,
  PRICEOPS_RECOMMENDATIONS,
  SEGMENTS,
} from "../growthViewModel";

const routerPush = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: routerPush }) }));

describe("Growth URL record ownership", () => {
  const data = { availability: "fixture" as const, freshness: FIXTURE_FRESHNESS, fromApi: false,
    segments: SEGMENTS, recommendations: PRICEOPS_RECOMMENDATIONS, items: GROWTH_ITEMS };
  afterEach(() => { cleanup(); vi.unstubAllEnvs(); routerPush.mockReset(); });

  it("never substitutes another action for an explicit missing URL target", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const vm = buildGrowthViewModel({ itemId: "missing-action" }, data);
    expect(vm.selectedItem).toBeNull();
    expect(vm.selectedItemGate).toBeNull();
    render(<GrowthWorkspace searchParams={{ item: "missing-action" }} apiData={data} />);
    expect(screen.getByTestId("growth-action-unavailable")).toHaveTextContent("指定活動未取得");
    expect(screen.queryByTestId("growth-item-detail")).not.toBeInTheDocument();
    expect(screen.queryByTestId("growth-transition-action")).not.toBeInTheDocument();
    expect(screen.queryByTestId("growth-submit-approval")).not.toBeInTheDocument();
  });

  it("keeps empty segment selections empty instead of showing an action in another segment", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const emptySegment = { ...SEGMENTS[0], id: "synthetic-empty-segment", name: "Synthetic empty segment" };
    const scoped = { ...data, segments: [...SEGMENTS, emptySegment] };
    const vm = buildGrowthViewModel({ segmentId: emptySegment.id, itemId: GROWTH_ITEMS[0].id }, scoped);
    expect(vm.items).toEqual([]);
    expect(vm.selectedItem).toBeNull();
    render(<GrowthWorkspace searchParams={{ segment: emptySegment.id }} apiData={scoped} />);
    expect(screen.getByTestId("growth-action-unavailable")).toHaveTextContent("沒有符合目前條件");
    expect(screen.queryByTestId("growth-item-detail")).not.toBeInTheDocument();
  });

  it("preserves a missing segment in navigation and does not widen its record scope", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const vm = buildGrowthViewModel({ segmentId: "missing-segment" }, data);
    expect(vm.items).toEqual([]);
    expect(vm.recommendations).toEqual([]);
    render(<GrowthWorkspace searchParams={{ segment: "missing-segment", campaign_ref: ["handoff", "audit"], empty_marker: "" }} apiData={data} />);
    expect(screen.getByTestId("growth-segment-unavailable")).toBeInTheDocument();
    const href = new URL(screen.getByTestId("growth-tab-segments").getAttribute("href")!, "http://localhost");
    expect(href.searchParams.get("segment")).toBe("missing-segment");
    expect(href.searchParams.getAll("campaign_ref")).toEqual(["handoff", "audit"]);
    expect(href.searchParams.has("empty_marker")).toBe(true);
  });

  it("chooses a default only from the visible filtered actions", () => {
    const vm = buildGrowthViewModel({ statusFilter: "out" }, data);
    expect(vm.selectedItem?.status).toMatch(/OUTCOME_READY|INEFFECTIVE|CLOSED/);
    const empty = buildGrowthViewModel({ kindFilter: "no-such-kind" }, data);
    expect(empty.selectedItem).toBeNull();
    expect(empty.selectedItemGate).toBeNull();
  });

  it("does not replace a missing or differently scoped draft recommendation", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    render(<GrowthWorkspace searchParams={{ draft: "missing-recommendation" }} apiData={data} />);
    expect(screen.getByTestId("growth-draft-unavailable")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const outside = PRICEOPS_RECOMMENDATIONS.find(row => row.segmentId !== SEGMENTS[0].id)!;
    expect(buildGrowthViewModel({ segmentId: SEGMENTS[0].id, draftId: outside.id }, data).draftRecommendation).toBeNull();
  });

  it("removes missing detail when the requested row disappears and restores its exact return", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const item = GROWTH_ITEMS[0];
    const { rerender } = render(<GrowthWorkspace searchParams={{ item: item.id }} apiData={data} />);
    expect(screen.getByTestId("growth-item-detail")).toHaveTextContent(item.id);
    rerender(<GrowthWorkspace searchParams={{ item: item.id }} apiData={{ ...data, items: GROWTH_ITEMS.filter(row => row.id !== item.id) }} />);
    expect(screen.getByTestId("growth-action-unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("growth-item-detail")).not.toBeInTheDocument();
    rerender(<GrowthWorkspace searchParams={{ item: item.id }} apiData={data} />);
    expect(screen.getByTestId("growth-item-detail")).toHaveTextContent(item.id);
  });
});

describe("GrowthWorkspace API loading", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    routerPush.mockReset();
  });

  const canonicalSegment = {
    id: "priceops:current-plan:current-item",
    name: "current-store / washer-20kg",
    storeId: "current-store",
    machineType: "washer-20kg",
    planId: "current-plan",
    planStatus: "draft",
    baselineDemand: 10,
    elasticity: { elasticity_value: -1.2, confidence: 0.8 },
    evaluation: null,
    source: "priceops",
  };

  function mockCanonicalReads() {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.endsWith("/freshness")
        ? { status: "LIVE", updatedAt: "2026-10-15T05:00:00+00:00", modelVersion: null,
            policyVersion: [], featureSnapshotTime: "2026-10-15T05:00:00+00:00",
            sourceSnapshotId: ["current-plan"], solverVersions: [], planCount: 1 }
        : url.endsWith("/segments")
          ? { items: [canonicalSegment] }
          : url.includes("/recommendations")
            ? { items: [{ id: "priceops-rec:current-plan:current-item", segmentId: canonicalSegment.id,
                planId: "current-plan", itemId: "current-item", storeId: "current-store",
                currentPrice: 100, recommendedPrice: 105, constraintStatus: "SAFE",
                bindingConstraints: [], riskLevel: "low", source: "priceops" }] }
            : { items: [{ ...GROWTH_ITEMS[0], id: "current-action", segmentId: canonicalSegment.id }] };
      return new Response(JSON.stringify(payload), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    }));
  }

  it("keeps unrecorded canonical segment statistics explicit without inventing a trend", async () => {
    mockCanonicalReads();
    render(<GrowthWorkspace searchParams={{ gtab: "segments" }} />);
    const segments = await screen.findByTestId("growth-segment-table");
    expect(segments).toHaveTextContent(canonicalSegment.name);
    expect(segments).toHaveTextContent("門市數未提供");
    expect(segments).toHaveTextContent("營收占比未提供");
    expect(segments).toHaveTextContent("分群規則未提供");
    expect(segments).toHaveTextContent("建議打法：未提供");
    expect(segments).toHaveTextContent("趨勢未提供");
    expect(within(segments).queryByText("持平")).not.toBeInTheDocument();
    expect(within(segments).getByRole("link", { name: "建立活動草稿" }).getAttribute("href"))
      .toContain(encodeURIComponent(canonicalSegment.id));
  });

  it("does not claim a model version when the canonical freshness response has none", async () => {
    mockCanonicalReads();
    render(<GrowthWorkspace searchParams={{ gtab: "segments" }} />);
    await screen.findByTestId("growth-segment-table");
    expect(screen.getByText(/模型版本未提供/)).toBeInTheDocument();
    expect(screen.queryByText(/分群與模型版本來自 live API/)).not.toBeInTheDocument();
  });

  it("preserves an explicitly recorded zero store count and supplied segment statistics", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    render(<GrowthWorkspace searchParams={{ gtab: "segments" }} apiData={{
      availability: "ready", freshness: FIXTURE_FRESHNESS, fromApi: true,
      segments: [{ ...SEGMENTS[0], storeCount: 0 }],
      recommendations: PRICEOPS_RECOMMENDATIONS, items: GROWTH_ITEMS,
    }} />);
    const segments = screen.getByTestId("growth-segment-table");
    expect(segments).toHaveTextContent("0 店");
    expect(segments).toHaveTextContent(SEGMENTS[0].revenueShare!);
    expect(segments).toHaveTextContent(SEGMENTS[0].definition!);
    expect(segments).toHaveTextContent(SEGMENTS[0].opportunity!);
    expect(segments).not.toHaveTextContent("未提供");
  });

  it.each([
    { lift: null, expected: "未提供百分比" },
    { lift: 0, expected: "0.0%" },
  ])("keeps forecast lift $lift separate from an action's target", ({ lift, expected }) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const recommendation = { ...PRICEOPS_RECOMMENDATIONS[0], expectedRevenueLift: lift, expectedMarginLift: lift };
    const item = { ...GROWTH_ITEMS[0], segmentId: recommendation.segmentId,
      sourceRecommendationId: recommendation.id, targetLift: 12.5 };
    render(<GrowthWorkspace apiData={{
      availability: "ready", freshness: FIXTURE_FRESHNESS, fromApi: true,
      segments: SEGMENTS, recommendations: [recommendation], items: [item],
    }} />);
    const card = screen.getByTestId(`growth-item-${item.id}`);
    expect(within(card).getByText("預估增額營收").parentElement).toHaveTextContent(expected);
    expect(within(card).getByText("毛利影響").parentElement).toHaveTextContent(expected);
    expect(within(card).getByText("預估增額營收").parentElement).not.toHaveTextContent("12.5%");
    expect(screen.getByTestId("growth-item-detail")).toHaveTextContent("12.5%");
  });

  const canonicalBuilderData = {
    availability: "ready" as const, freshness: FIXTURE_FRESHNESS, fromApi: true,
    segments: [canonicalSegment],
    recommendations: [{ ...PRICEOPS_RECOMMENDATIONS[0], segmentId: canonicalSegment.id }],
    items: [{ ...GROWTH_ITEMS[0], segmentId: canonicalSegment.id }],
  };

  it.each(["true", "false"])("prefills the actual selected API segment and store when production mode is %s", (production) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", production);
    render(<GrowthWorkspace apiData={canonicalBuilderData}
      searchParams={{ builder: "offpeak", segment: canonicalSegment.id }} />);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByLabelText("門市")).toHaveValue(canonicalSegment.storeId);
    fireEvent.click(within(dialog).getByRole("button", { name: "下一步 →" }));
    expect(within(dialog).getByLabelText("客群")).toHaveValue(canonicalSegment.id);
  });

  it("keeps an unselected API draft scope empty instead of borrowing the demo store or segment", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    render(<GrowthWorkspace apiData={canonicalBuilderData} searchParams={{ builder: "offpeak" }} />);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByLabelText("門市")).toHaveValue("");
    fireEvent.click(within(dialog).getByRole("button", { name: "下一步 →" }));
    expect(within(dialog).getByLabelText("客群")).toHaveValue("");
  });

  it("retains the approved local fixture builder defaults when its data is actually a fixture", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    render(<GrowthWorkspace apiData={{ availability: "fixture", freshness: FIXTURE_FRESHNESS,
      fromApi: false, segments: SEGMENTS, recommendations: PRICEOPS_RECOMMENDATIONS, items: GROWTH_ITEMS }}
      searchParams={{ builder: "offpeak" }} />);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByLabelText("門市")).toHaveValue("Oday 信義松仁店");
    fireEvent.click(within(dialog).getByRole("button", { name: "下一步 →" }));
    expect(within(dialog).getByLabelText("客群")).toHaveValue("seg-metro-dinner");
  });

  it("replaces the initial fixture builder scope when the deferred API loader resolves", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    let release!: () => void;
    const ready = new Promise<void>((resolve) => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      await ready;
      const url = String(input);
      const payload = url.endsWith("/freshness") ? FIXTURE_FRESHNESS
        : url.endsWith("/segments") ? { items: canonicalBuilderData.segments }
          : url.includes("/recommendations") ? { items: canonicalBuilderData.recommendations }
            : { items: canonicalBuilderData.items };
      return new Response(JSON.stringify(payload), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    }));
    render(<GrowthWorkspace searchParams={{ builder: "offpeak", segment: canonicalSegment.id }} />);
    expect(within(screen.getByRole("dialog")).getByLabelText("門市")).toHaveValue("Oday 信義松仁店");
    release();
    await waitFor(() => expect(within(screen.getByRole("dialog")).getByLabelText("門市"))
      .toHaveValue(canonicalSegment.storeId));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "下一步 →" }));
    expect(within(screen.getByRole("dialog")).getByLabelText("客群")).toHaveValue(canonicalSegment.id);
  });

  it("preserves an edited draft when the same API record scope refreshes", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const searchParams = { builder: "offpeak", segment: canonicalSegment.id };
    const { rerender } = render(<GrowthWorkspace apiData={canonicalBuilderData} searchParams={searchParams} />);
    fireEvent.change(within(screen.getByRole("dialog")).getByLabelText("活動名稱"), {
      target: { value: "My current draft" },
    });
    rerender(<GrowthWorkspace apiData={{ ...canonicalBuilderData,
      freshness: { ...FIXTURE_FRESHNESS, updatedAt: "2026-10-15T06:15:00+00:00" } }} searchParams={searchParams} />);
    expect(within(screen.getByRole("dialog")).getByLabelText("活動名稱")).toHaveValue("My current draft");
    expect(within(screen.getByRole("dialog")).getByLabelText("門市")).toHaveValue(canonicalSegment.storeId);
  });

  it("mounts the existing Growth API loader in production", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const liveFreshness = {
      ...FIXTURE_FRESHNESS,
      modelVersion: "growth-production-v2",
      sourceSnapshotId: "live-growth-snapshot-24",
    };
    const liveSegments = [{
      ...SEGMENTS[0],
      id: "live-segment-1",
      name: "Live production segment",
    }];
    const liveRecommendations = [{
      ...PRICEOPS_RECOMMENDATIONS[0],
      id: "live-recommendation-1",
      segmentId: "live-segment-1",
      title: "Live production recommendation",
    }];
    const liveItems = [{
      ...GROWTH_ITEMS[0],
      id: "live-growth-action-1",
      segmentId: "live-segment-1",
      sourceRecommendationId: "live-recommendation-1",
      name: "Live production action",
    }];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.endsWith("/freshness")
        ? liveFreshness
        : url.endsWith("/segments")
          ? { items: liveSegments }
          : url.includes("/recommendations")
            ? { items: liveRecommendations }
            : { items: liveItems };
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<GrowthWorkspace basePath="/operator" searchParams={{}} />);

    expect(await screen.findByTestId("growth-workspace")).toBeInTheDocument();
    const requestedUrls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(requestedUrls).toEqual(expect.arrayContaining([
      "/api/v1/operator/growth/freshness",
      "/api/v1/operator/growth/segments",
      "/api/v1/operator/growth/recommendations",
      "/api/v1/operator/growth/actions",
    ]));
    expect(screen.getByTestId("growth-data-status")).not.toHaveTextContent("fixture");
    expect(screen.getAllByText("Live production action").length).toBeGreaterThan(0);
    expect(screen.queryByText(GROWTH_ITEMS[0].name)).not.toBeInTheDocument();
  });

  it("never labels live production model output as mock", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    render(
      <GrowthWorkspace
        apiData={{
          availability: "ready",
          freshness: {
            ...FIXTURE_FRESHNESS,
            modelVersion: "growth-production-v2",
            sourceSnapshotId: "live-growth-snapshot-24",
          },
          fromApi: true,
          items: [{
            ...GROWTH_ITEMS[0],
            id: "live-growth-action-1",
            segmentId: "live-segment-1",
          }],
          recommendations: [{
            ...PRICEOPS_RECOMMENDATIONS[0],
            id: "live-recommendation-1",
            segmentId: "live-segment-1",
          }],
          segments: [{
            ...SEGMENTS[0],
            id: "live-segment-1",
            name: "Live production segment",
          }],
        }}
        basePath="/operator"
        searchParams={{ gtab: "segments" }}
      />,
    );

    expect(screen.getByText(/分群與模型版本來自 live API/)).toBeInTheDocument();
    expect(screen.queryByText(/mock/i)).not.toBeInTheDocument();
  });

  it("fails closed when a Growth read is unavailable", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    render(<GrowthWorkspace basePath="/operator" searchParams={{}} />);

    await waitFor(() =>
      expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error"),
    );
    expect(screen.queryByTestId("growth-workspace")).not.toBeInTheDocument();
  });

  it("blocks seed and partial Growth API responses in production", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.endsWith("/freshness")
        ? { ...FIXTURE_FRESHNESS, source: "fixture-growth" }
        : { items: [] };
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }));

    render(<GrowthWorkspace basePath="/operator" searchParams={{}} />);

    // The gate first renders in its loading state; wait for the seed verdict.
    const gate = await screen.findByTestId("operator-data-unavailable");
    await waitFor(() => expect(gate).toHaveAttribute("data-status", "seed"));
    expect(screen.queryByTestId("growth-workspace")).not.toBeInTheDocument();
    expect(screen.queryByText(GROWTH_ITEMS[0].name)).not.toBeInTheDocument();
  });

  it("retains the embedded fixture workspace in local mode", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    render(<GrowthWorkspace basePath="/operator" searchParams={{}} />);

    expect(await screen.findByTestId("growth-workspace")).toHaveAttribute(
      "data-source",
      "fixture",
    );
    expect(screen.getAllByText(GROWTH_ITEMS[0].name).length).toBeGreaterThan(0);
  });

  it("renders the Package 10 Growth composition and lifecycle labels", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");

    render(
      <GrowthWorkspace
        apiData={{
          availability: "fixture",
          freshness: FIXTURE_FRESHNESS,
          fromApi: false,
          items: GROWTH_ITEMS,
          recommendations: PRICEOPS_RECOMMENDATIONS,
          segments: SEGMENTS,
        }}
        basePath="/operator"
        searchParams={{}}
      />,
    );

    const entryCards = screen.getByTestId("growth-entry-cards");
    expect(entryCards).toHaveAttribute("data-screen-label", "Growth 建立入口");
    expect(within(entryCards).getAllByRole("link")).toHaveLength(3);
    expect(screen.getByRole("link", { name: "活動與機會" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "會員分群" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "PriceOps 定價" })).toBeInTheDocument();

    const stepper = screen.getByTestId("growth-lifecycle-stepper");
    for (const label of ["機會", "草稿", "核准", "排程", "執行", "觀察", "成效", "結案"]) {
      expect(within(stepper).getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText("優惠券")).toBeInTheDocument();
    expect(screen.getAllByText("AdLift").length).toBeGreaterThan(0);
  });

  it("renders the Package 10 PriceOps nine-column workbench", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");

    render(
      <GrowthWorkspace
        apiData={{
          availability: "fixture",
          freshness: FIXTURE_FRESHNESS,
          fromApi: false,
          items: GROWTH_ITEMS,
          recommendations: PRICEOPS_RECOMMENDATIONS,
          segments: SEGMENTS,
        }}
        basePath="/operator"
        searchParams={{ gtab: "priceops" }}
      />,
    );

    const table = screen.getByTestId("growth-recommendation-table");
    for (const heading of [
      "門市／分群",
      "時窗",
      "目前價",
      "建議價",
      "預期利用率",
      "預期營收",
      "毛利風險",
      "回滾條件",
    ]) {
      expect(within(table).getByRole("columnheader", { name: heading })).toBeInTheDocument();
    }
    expect(within(table).getAllByText("建立定價草稿")).toHaveLength(3);
  });

  it("opens the actual outcome dialog without a write and requires the operator's reason", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _options?: RequestInit) => new Response(JSON.stringify({
      id: GROWTH_ITEMS[0].id, growth_outcome: "EFFECTIVE", correlation_id: "synthetic-confirmation",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    render(<GrowthWorkspace basePath="/operator" searchParams={{ item: GROWTH_ITEMS[0].id }} apiData={{
      availability: "fixture", freshness: FIXTURE_FRESHNESS, fromApi: false,
      items: GROWTH_ITEMS, recommendations: PRICEOPS_RECOMMENDATIONS, segments: SEGMENTS,
    }} />);
    expect(screen.queryByRole("dialog", { name: "活動成效判斷" })).not.toBeInTheDocument();
    const trigger = screen.getByTestId("growth-outcome-open"); trigger.focus(); fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "活動成效判斷" });
    expect(dialog.closest('[data-screen-label="Dialog Growth Outcome"]')).toBeInTheDocument();
    expect(within(dialog).getByLabelText("判斷理由（寫入 Decision Log）")).toHaveFocus();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByTestId("growth-close-button")).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("判斷理由（寫入 Decision Log）"), { target: { value: "Synthetic operator evidence review" } });
    fireEvent.click(screen.getByTestId("growth-close-button"));
    await waitFor(() => expect(screen.getByTestId("growth-closeout-success")).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const request = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(request.body as string).rationale).toBe("Synthetic operator evidence review");
    fireEvent.click(within(dialog).getByRole("button", { name: "關閉活動成效判斷" }));
    expect(trigger).toHaveFocus();
  });

  it("keeps draft creation visible and keyboard accessible while the API result is uncertain", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    let settle: (response: Response) => void = () => {};
    const fetchMock = vi.fn((input: RequestInfo | URL) => String(input).endsWith("/conflicts/check")
      ? Promise.resolve(new Response(JSON.stringify({ checks: [], blocked: false, reasons: [] }), { status: 200 }))
      : new Promise<Response>(resolve => { settle = resolve; }));
    vi.stubGlobal("fetch", fetchMock);
    render(<GrowthWorkspace basePath="/operator" searchParams={{ builder: "offpeak" }} apiData={{
      availability: "fixture", freshness: FIXTURE_FRESHNESS, fromApi: false,
      items: GROWTH_ITEMS, recommendations: PRICEOPS_RECOMMENDATIONS, segments: SEGMENTS,
    }} />);
    const dialog = screen.getByRole("dialog", { name: "建立 Growth Action 草稿" });
    expect(within(dialog).getByLabelText("活動名稱")).toHaveFocus();
    fireEvent.change(within(dialog).getByLabelText("活動名稱"), { target: { value: "Synthetic draft" } });
    fireEvent.click(screen.getByTestId("growth-builder-next"));
    expect(within(dialog).getByLabelText("客群")).toHaveFocus();
    fireEvent.click(screen.getByTestId("growth-builder-next"));
    fireEvent.click(screen.getByTestId("growth-builder-next"));
    await screen.findByTestId("growth-builder-step-4");
    fireEvent.click(screen.getByTestId("growth-builder-next"));
    fireEvent.click(screen.getByTestId("growth-draft-submit"));
    expect(screen.getByTestId("growth-builder-submit-status")).toHaveFocus();
    expect(screen.getByTestId("growth-draft-close")).toBeDisabled();
    expect(screen.getByTestId("growth-builder-prev")).toBeDisabled();
    expect(screen.getByLabelText("關閉建立草稿視窗")).toBeDisabled();
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(routerPush).not.toHaveBeenCalled();
    settle(new Response("Synthetic unavailable", { status: 503 }));
    await screen.findByTestId("growth-draft-api-error");
    expect(screen.getByTestId("growth-builder-step-5")).toHaveFocus();
    expect(screen.getByTestId("growth-builder-prev")).toBeEnabled();
    expect(routerPush).not.toHaveBeenCalled();
  });

  async function openSyntheticDraft() {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    render(<GrowthWorkspace basePath="/operator" searchParams={{ builder: "offpeak" }} apiData={{
      availability: "fixture", freshness: FIXTURE_FRESHNESS, fromApi: false,
      items: GROWTH_ITEMS, recommendations: PRICEOPS_RECOMMENDATIONS, segments: SEGMENTS,
    }} />);
    fireEvent.change(screen.getByRole("dialog").querySelector('input[name="name"]')!, { target: { value: "Synthetic recovery draft" } });
    fireEvent.click(screen.getByTestId("growth-builder-next"));
    fireEvent.click(screen.getByTestId("growth-builder-next"));
    fireEvent.click(screen.getByTestId("growth-builder-next"));
    await screen.findByTestId("growth-builder-step-4");
    fireEvent.click(screen.getByTestId("growth-builder-next"));
  }

  it("reconciles a lost create acknowledgement, then retries only submission of the confirmed draft", async () => {
    let creates = 0;
    let submits = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _options?: RequestInit) => {
      const path = String(input);
      if (path.endsWith("/conflicts/check")) return new Response(JSON.stringify({ checks: [], blocked: false, reasons: [] }));
      if (path.endsWith("/actions")) {
        creates += 1;
        if (creates === 1) throw new Error("Synthetic lost create acknowledgement");
        return new Response(JSON.stringify({ id: "synthetic-created", status: "DRAFT", kind: "offpeak", correlation_id: "synthetic-create" }));
      }
      submits += 1;
      if (submits === 1) throw new Error("Synthetic lost submit acknowledgement");
      return new Response(JSON.stringify({ id: "synthetic-created", status: "PENDING_APPROVAL", approval: { id: "synthetic-approval", ref: "synthetic-created", module: "Growth", status: "pending" } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    await openSyntheticDraft();
    fireEvent.click(screen.getByTestId("growth-draft-submit-approval"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("尚未確認草稿建立結果"));
    expect(routerPush).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("growth-draft-submit-approval"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("synthetic-created 已建立"));
    expect(screen.getByTestId("growth-builder-prev")).toBeDisabled();
    expect(screen.getByTestId("growth-draft-submit")).toHaveTextContent("保留草稿");
    expect(screen.getByTestId("growth-draft-submit-approval")).toHaveTextContent("重試送核准");
    fireEvent.click(screen.getByTestId("growth-draft-submit-approval"));
    await waitFor(() => expect(routerPush).toHaveBeenCalledTimes(1));
    expect(creates).toBe(2);
    expect(submits).toBe(2);
    const calls = fetchMock.mock.calls.filter(([url]) => !String(url).endsWith("/conflicts/check"));
    const options = calls.map(call => (call as unknown as [RequestInfo, RequestInit])[1]);
    expect(new Headers(options[0].headers).get("Idempotency-Key")).toBe(new Headers(options[1].headers).get("Idempotency-Key"));
    expect(new Headers(options[2].headers).get("Idempotency-Key")).toBe(new Headers(options[3].headers).get("Idempotency-Key"));
    expect(new Headers(options[1].headers).get("Idempotency-Key")).not.toBe(new Headers(options[2].headers).get("Idempotency-Key"));
  });

  it("lets the operator retain a confirmed draft without creating or submitting it again", async () => {
    const writes: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/conflicts/check")) return new Response(JSON.stringify({ checks: [], blocked: false, reasons: [] }));
      writes.push(path);
      return path.endsWith("/actions")
        ? new Response(JSON.stringify({ id: "synthetic-kept", status: "DRAFT", kind: "offpeak" }))
        : new Response("Synthetic unavailable", { status: 503 });
    }));
    await openSyntheticDraft();
    fireEvent.click(screen.getByTestId("growth-draft-submit-approval"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("synthetic-kept 已建立"));
    fireEvent.click(screen.getByTestId("growth-draft-submit"));
    expect(routerPush).toHaveBeenCalledTimes(1);
    expect(writes).toEqual(["/api/v1/operator/growth/actions", "/api/v1/operator/growth/actions/synthetic-kept/submit"]);
  });

  it("does not accept an approval acknowledgement for another draft", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/conflicts/check")) return new Response(JSON.stringify({ checks: [], blocked: false, reasons: [] }));
      return new Response(JSON.stringify(path.endsWith("/actions")
        ? { id: "synthetic-requested", status: "DRAFT", kind: "offpeak" }
        : { id: "other", status: "PENDING_APPROVAL", approval: { id: "other-approval", ref: "other", module: "Growth", status: "pending" } }));
    }));
    await openSyntheticDraft();
    fireEvent.click(screen.getByTestId("growth-draft-submit-approval"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("synthetic-requested 已建立"));
    expect(routerPush).not.toHaveBeenCalled();
  });

  it("preserves the reason and idempotency key after an uncertain response, and keeps an in-flight dialog visible", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    let settle: (response: Response) => void = () => {};
    let attempts = 0;
    const fetchMock = vi.fn((_input: RequestInfo | URL, _options?: RequestInit) => {
      if (++attempts === 1) return Promise.resolve(new Response("synthetic unavailable", { status: 503 }));
      return new Promise<Response>((resolve) => { settle = resolve; });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<GrowthWorkspace basePath="/operator" searchParams={{ item: GROWTH_ITEMS[0].id }} apiData={{
      availability: "fixture", freshness: FIXTURE_FRESHNESS, fromApi: false,
      items: GROWTH_ITEMS, recommendations: PRICEOPS_RECOMMENDATIONS, segments: SEGMENTS,
    }} />);
    fireEvent.click(screen.getByTestId("growth-outcome-open"));
    const dialog = screen.getByRole("dialog", { name: "活動成效判斷" });
    const reason = within(dialog).getByLabelText("判斷理由（寫入 Decision Log）");
    fireEvent.change(reason, { target: { value: "Synthetic preserved reason" } });
    fireEvent.click(screen.getByTestId("growth-close-button"));
    await screen.findByRole("alert");
    expect(reason).toHaveValue("Synthetic preserved reason");
    fireEvent.click(screen.getByTestId("growth-close-button"));
    expect(reason).toHaveAttribute("readonly");
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(dialog).toBeInTheDocument();
    const first = fetchMock.mock.calls[0][1] as RequestInit;
    const second = fetchMock.mock.calls[1][1] as RequestInit;
    expect((second.headers as Record<string,string>)["Idempotency-Key"]).toBe((first.headers as Record<string,string>)["Idempotency-Key"]);
    expect(second.body).toBe(first.body);
    settle(new Response(JSON.stringify({ id: GROWTH_ITEMS[0].id, growth_outcome: "EFFECTIVE", correlation_id: "synthetic-retry" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    await screen.findByTestId("growth-closeout-success");
  });

  it("does not claim submission success for a different activity receipt", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      id: "synthetic-other-activity", growth_outcome: "EFFECTIVE", correlation_id: "synthetic-mismatch",
    }), { status: 200, headers: { "Content-Type": "application/json" } })));
    render(<GrowthWorkspace basePath="/operator" searchParams={{ item: GROWTH_ITEMS[0].id }} apiData={{
      availability: "fixture", freshness: FIXTURE_FRESHNESS, fromApi: false,
      items: GROWTH_ITEMS, recommendations: PRICEOPS_RECOMMENDATIONS, segments: SEGMENTS,
    }} />);
    fireEvent.click(screen.getByTestId("growth-outcome-open"));
    fireEvent.change(screen.getByLabelText("判斷理由（寫入 Decision Log）"), { target: { value: "Synthetic review" } });
    fireEvent.click(screen.getByTestId("growth-close-button"));
    expect(await screen.findByRole("alert")).toHaveTextContent("提交回應與目前活動判斷不符");
    expect(screen.queryByTestId("growth-closeout-success")).not.toBeInTheDocument();
  });

  it("keeps the R7 PriceOps default table without the invented simulation panel", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    render(<GrowthWorkspace searchParams={{ gtab: "priceops" }} apiData={{
      availability: "fixture", freshness: FIXTURE_FRESHNESS, fromApi: false,
      items: GROWTH_ITEMS, recommendations: PRICEOPS_RECOMMENDATIONS, segments: SEGMENTS,
    }} />);
    expect(screen.getByTestId("growth-recommendation-table")).toBeInTheDocument();
    expect(screen.queryByTestId("priceops-scenario-workbench")).not.toBeInTheDocument();
    expect(screen.queryByTestId("priceops-writeback-btn")).not.toBeInTheDocument();
  });

  it("does not substitute a different pricing recommendation for a missing explicit target", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    render(<GrowthWorkspace searchParams={{ gtab: "priceops", prec: "missing-rec" }} apiData={{
      availability: "fixture", freshness: FIXTURE_FRESHNESS, fromApi: false,
      items: GROWTH_ITEMS, recommendations: PRICEOPS_RECOMMENDATIONS, segments: SEGMENTS,
    }} />);
    expect(screen.getByTestId("priceops-selection-unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("priceops-scenario-workbench")).not.toBeInTheDocument();
  });

  it("keeps Growth approval decisions in Govern", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const pending = {
      ...GROWTH_ITEMS[4],
      status: "PENDING_APPROVAL" as const,
      approvalId: "APR-601",
    };

    render(
      <GrowthWorkspace
        apiData={{
          availability: "fixture",
          freshness: FIXTURE_FRESHNESS,
          fromApi: false,
          items: [pending],
          recommendations: PRICEOPS_RECOMMENDATIONS,
          segments: SEGMENTS,
        }}
        basePath="/operator"
        searchParams={{ item: pending.id }}
      />,
    );

    const panel = screen.getByTestId("growth-approval-panel");
    expect(within(panel).getByText("APR-601")).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "查看 →" })).toHaveAttribute(
      "href",
      "/operator?ws=govern",
    );
    expect(screen.queryByTestId("growth-approve")).not.toBeInTheDocument();
    expect(screen.queryByTestId("growth-reject")).not.toBeInTheDocument();
  });

  it("retains submit intent across an uncertain result without claiming approval", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const draft = { ...GROWTH_ITEMS[4], status: "DRAFT" as const };
    const fetchMock = vi.fn((_input: RequestInfo | URL, _options?: RequestInit): Promise<Response> => fetchMock.mock.calls.length === 1
      ? Promise.reject(new Error("Synthetic lost acknowledgement"))
      : Promise.resolve(new Response(JSON.stringify({ id: draft.id, status: "PENDING_APPROVAL", approval: { id: "synthetic-approval", ref: draft.id, module: "Growth", status: "pending" } }))));
    vi.stubGlobal("fetch", fetchMock);
    render(<GrowthWorkspace basePath="/operator" searchParams={{ item: draft.id }} apiData={{ availability: "fixture", freshness: FIXTURE_FRESHNESS, fromApi: false, items: [draft], recommendations: PRICEOPS_RECOMMENDATIONS, segments: SEGMENTS }} />);
    fireEvent.click(screen.getByTestId("growth-submit-approval"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("尚未確認送審"));
    expect(screen.queryByTestId("growth-approval-created")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("growth-submit-approval"));
    await screen.findByTestId("growth-approval-created");
    const first = fetchMock.mock.calls[0][1]!;
    const second = fetchMock.mock.calls[1][1]!;
    expect(new Headers(first.headers).get("Idempotency-Key")).toBe(new Headers(second.headers).get("Idempotency-Key"));
    expect(screen.getByTestId("growth-approval-created")).toHaveTextContent("synthetic-approval");
  });

  it("retains transition intent and rejects a mismatched acknowledgement", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const item = { ...GROWTH_ITEMS[4], status: "APPROVED" as const };
    let settle!: (value: Response) => void;
    const fetchMock = vi.fn((_input: RequestInfo | URL, _options?: RequestInit): Promise<Response> => fetchMock.mock.calls.length === 1
      ? Promise.resolve(new Response(JSON.stringify({ id: "wrong-action", status: "SCHEDULED" })))
      : new Promise<Response>(resolve => { settle = resolve; }));
    vi.stubGlobal("fetch", fetchMock);
    render(<GrowthWorkspace basePath="/operator" searchParams={{ item: item.id }} apiData={{ availability: "fixture", freshness: FIXTURE_FRESHNESS, fromApi: false, items: [item], recommendations: PRICEOPS_RECOMMENDATIONS, segments: SEGMENTS }} />);
    fireEvent.click(screen.getByTestId("growth-transition-action"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("尚未確認生命週期"));
    expect(screen.getByTestId("growth-item-detail")).toHaveTextContent("已核准");
    fireEvent.click(screen.getByTestId("growth-transition-action"));
    expect(screen.getByTestId("growth-transition-action")).toBeDisabled();
    fireEvent.click(screen.getByTestId("growth-transition-action"));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(new Headers(fetchMock.mock.calls[0][1]!.headers).get("Idempotency-Key")).toBe(new Headers(fetchMock.mock.calls[1][1]!.headers).get("Idempotency-Key"));
    settle(new Response(JSON.stringify({ id: item.id, status: "SCHEDULED" })));
    await waitFor(() => expect(screen.getByTestId("growth-item-detail")).toHaveTextContent("已排程"));
  });

  it("does not carry an old record response into a newly selected action", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const first = { ...GROWTH_ITEMS[4], id: "synthetic-first", status: "APPROVED" as const };
    const second = { ...GROWTH_ITEMS[4], id: "synthetic-second", status: "APPROVED" as const };
    let settle!: (value: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { settle = resolve; })));
    const apiData = { availability: "fixture" as const, freshness: FIXTURE_FRESHNESS, fromApi: false, items: [first, second], recommendations: PRICEOPS_RECOMMENDATIONS, segments: SEGMENTS };
    const { rerender } = render(<GrowthWorkspace basePath="/operator" searchParams={{ item: first.id }} apiData={apiData} />);
    fireEvent.click(screen.getByTestId("growth-transition-action"));
    rerender(<GrowthWorkspace basePath="/operator" searchParams={{ item: second.id }} apiData={apiData} />);
    settle(new Response(JSON.stringify({ id: first.id, status: "SCHEDULED" })));
    await waitFor(() => expect(screen.getByTestId("growth-item-detail")).toHaveTextContent(second.id));
    expect(screen.getByTestId("growth-item-detail")).toHaveTextContent("已核准");
    expect(screen.getByTestId("growth-transition-action")).toHaveTextContent("排程上線");
  });

  it("advances lifecycle only after the transition API succeeds", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const approved = {
      ...GROWTH_ITEMS[4],
      status: "APPROVED" as const,
    };
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({
        id: approved.id,
        status: "SCHEDULED",
        correlation_id: "corr-transition-1",
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <GrowthWorkspace
        apiData={{
          availability: "fixture",
          freshness: FIXTURE_FRESHNESS,
          fromApi: false,
          items: [approved],
          recommendations: PRICEOPS_RECOMMENDATIONS,
          segments: SEGMENTS,
        }}
        basePath="/operator"
        searchParams={{ item: approved.id }}
      />,
    );

    fireEvent.click(screen.getByTestId("growth-transition-action"));

    await waitFor(() =>
      expect(screen.getByTestId("growth-item-detail")).toHaveTextContent("已排程"),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/operator/growth/actions/${approved.id}/transition`,
      expect.objectContaining({ method: "POST" }),
    );
  });
});
