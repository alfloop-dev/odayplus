/**
 * ODP-P10-CAN-001-R3C — Govern workspace delayed shell-envelope regression.
 *
 * The Operator shell bootstrap envelope exposes `approvals` as an alias of the
 * Today decision cards (`{ id, title, meta, status, cta, tone, target }`).
 * Those records are NOT `GovernanceApproval` rows: they carry no `module`,
 * `requestor`, `submittedAt`, `priority` or `evidence`.  When the envelope
 * resolved after the Govern route was already selected, the console pushed the
 * decision cards straight into `GovernanceWorkspace`, whose module/status badge
 * helpers called `.toLowerCase()` on the missing fields and threw, taking the
 * whole route down with a Next.js route error.
 *
 * These tests pin the canonical product boundary:
 *   - the Govern workspace never crashes on foreign or malformed external rows;
 *   - foreign and malformed rows are dropped, not repaired: no module,
 *     requestor, time, risk or status value is ever invented for them;
 *   - a governance side channel that disappears clears the rows it supplied,
 *     so stale governance state cannot survive a newer envelope;
 *   - the Governance snapshot API stays the source of truth;
 *   - production keeps failing closed instead of falling back to fixtures.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GovernanceWorkspace } from "../GovernanceWorkspace";
import { OperatorConsole } from "../OperatorConsole";
import {
  normalizeGovernanceApprovals,
  normalizeGovernanceAuditRows,
  normalizeGovernanceDecisionRows,
  normalizeGovernanceEvidencePackages,
  normalizeGovernanceStatusBoard,
} from "../governance/governanceEnvelope";

/** Exactly the shape `normalizeShellEnvelope` produces for `envelope.approvals`. */
const shellDecisionCards = [
  {
    cta: "檢視",
    id: "APR-501",
    meta: "SiteScore 76 · 競品密度高",
    status: "WAIT",
    target: { workspace: "govern", entityId: "APR-501", tab: "approvals" },
    title: "SiteScore APR-501 複審",
    tone: "warning",
  },
  {
    cta: "檢視",
    id: "GRW-207",
    meta: "夜間券 20:00-23:00",
    status: "Draft",
    target: { workspace: "govern", entityId: "GRW-207", tab: "approvals" },
    title: "會員回流活動核准",
    tone: "info",
  },
];

const governanceSnapshot = {
  approvals: [
    {
      id: "APR-LIVE-9",
      module: "Network",
      title: "Approve SiteScore override",
      requestor: "Expansion Manager",
      submittedAt: "2026-07-26T02:00:00Z",
      status: "pending",
      priority: "critical",
      owner: "展店經理",
      sla: "18m",
      entityRef: "SITE-9",
      summary: "Override requested for a high-traffic corner candidate.",
      systemRecommendation: "WAIT",
      evidence: [{ id: "EV-9", label: "SiteScore v4.8", type: "model", state: "ready" }],
    },
  ],
  decisions: [
    {
      id: "DEC-LIVE-9",
      module: "Store Ops",
      item: "ISS-9 closure",
      systemRecommendation: "Approve",
      finalDecision: "Approved",
      reason: "Evidence package matched closure policy.",
      actor: "營運主管",
      decidedAt: "2026-07-25T11:00:00Z",
    },
  ],
  auditRows: [
    {
      id: "AUD-LIVE-9",
      category: "approval",
      timestamp: "2026-07-26T02:05:00Z",
      actor: "Expansion Manager",
      action: "Override requested",
      module: "Network",
      entityRef: "SITE-9",
      summary: "SiteScore WAIT to GO override requested.",
      correlationId: "corr-site-9",
    },
  ],
  evidencePackages: [],
  statusBoard: {
    dataQuality: [{ source: "Listings", status: "正常", good: true, note: "live" }],
    models: [],
    connectors: [],
    sla: [],
    users: [],
  },
  source: "operator-governance-production",
};

/**
 * A live Operator shell bootstrap envelope. `approvals` mirrors `decisions`
 * exactly as `_build_envelope` emits it, which is what made the Govern route
 * crash once the payload arrived after the route was already selected.
 */
const shellEnvelopePayload = {
  meta: {
    source: "operator-shell-live",
    dataMode: "live",
    role: {
      id: "ops-lead",
      label: "營運主管",
      subtitle: "全域監控",
      allowedWorkspaces: ["today", "store", "growth", "network", "govern"],
      heroName: "林承翰",
    },
    counts: { approvals: 2, critical: 0, notifications: 0, search: 0, taskCenter: 0 },
  },
  navigation: {
    roles: [],
    workspaces: [
      { id: "today", label: "Today 今日工作", shortLabel: "Today", description: "Today", allowed: true },
      { id: "govern", label: "治理稽核", shortLabel: "Govern", description: "Govern", allowed: true },
    ],
    allowedWorkspaces: ["today", "govern"],
  },
  header: {
    counts: { approvals: 2, critical: 0, notifications: 0, search: 0, taskCenter: 0 },
  },
  today: {
    hero: {
      name: "林承翰",
      roleLabel: "營運主管",
      scope: "全品牌",
      dateLabel: "2026-07-26",
    },
    kpis: [],
    queue: [],
    decisions: shellDecisionCards,
    riskRows: [],
    auditFeed: [],
  },
  search: { count: 0, items: [] },
  notifications: [],
  approvals: shellDecisionCards,
  workQueue: [],
  kpis: [],
  decisions: shellDecisionCards,
  riskRows: [],
  auditFeed: [],
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Govern workspace admits only governance rows", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("drops shell decision cards instead of crashing when they arrive on the approvals prop", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "down" }, 503)));

    const { rerender } = render(<GovernanceWorkspace roleId="ops-lead" />);
    expect(await screen.findByTestId("governance-workspace")).toBeInTheDocument();

    // The delayed shell envelope lands after mount and re-renders the workspace.
    rerender(
      <GovernanceWorkspace roleId="ops-lead" approvals={shellDecisionCards as never} />,
    );

    await waitFor(() =>
      expect(screen.getByTestId("governance-workspace")).toBeInTheDocument(),
    );
    // Today cards are not governance approvals: they are dropped whole, not
    // rendered under an invented module, requestor or risk.
    expect(screen.queryByText("SiteScore APR-501 複審")).not.toBeInTheDocument();
    expect(screen.queryByText("會員回流活動核准")).not.toBeInTheDocument();
    expect(screen.getAllByText("目前沒有核准請求").length).toBeGreaterThan(0);
    expect(screen.getByLabelText("核准佇列")).toHaveTextContent("0 全部");
  });

  it("drops malformed approval, decision and audit rows without throwing", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "down" }, 503)));

    render(
      <GovernanceWorkspace
        roleId="ops-lead"
        approvals={
          [
            { id: "BAD-1", title: "No module or status" },
            { id: "BAD-2", module: 42, status: null, priority: [], evidence: "nope" },
            null,
            "not-an-object",
          ] as never
        }
        auditRows={[{ id: "BAD-AUD", category: undefined }] as never}
        decisions={[{ id: "BAD-DEC", module: {} }] as never}
      />,
    );

    expect(await screen.findByTestId("governance-workspace")).toBeInTheDocument();
    // Every row above is missing a field that identifies it as governance data,
    // so none of them reaches the queue under a fabricated value.
    expect(screen.queryByText("No module or status")).not.toBeInTheDocument();
    expect(screen.getByLabelText("核准佇列")).toHaveTextContent("0 全部");
  });

  it("renders an incomplete governance row with explicit unavailable fields", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "down" }, 503)));

    // Shape emitted by the canonical Governance modules: identified and
    // classified, but with no requestor, priority or evidence.
    render(
      <GovernanceWorkspace
        roleId="ops-lead"
        approvals={
          [
            {
              id: "SITE-CANON-1",
              module: "SiteScore",
              title: "CANDIDATE-77",
              status: "pending",
              submittedAt: "2026-07-26T02:00:00Z",
            },
          ] as never
        }
      />,
    );

    expect(await screen.findByTestId("governance-workspace")).toBeInTheDocument();
    expect(screen.getAllByText("CANDIDATE-77").length).toBeGreaterThan(0);
    // Absent risk and requestor are shown as gaps, never as 中 or a stand-in name.
    expect(screen.queryByText("風險 中")).not.toBeInTheDocument();
    expect(screen.getAllByText("風險 未提供").length).toBeGreaterThan(0);
    expect(screen.getAllByText("未提供").length).toBeGreaterThan(0);
  });

  it("clears rows supplied by a governance side channel that goes away", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "down" }, 503)));

    const { rerender } = render(
      <GovernanceWorkspace
        roleId="ops-lead"
        approvals={governanceSnapshot.approvals as never}
        auditRows={governanceSnapshot.auditRows as never}
        decisions={governanceSnapshot.decisions as never}
      />,
    );

    expect(await screen.findByTestId("governance-workspace")).toBeInTheDocument();
    expect(screen.getAllByText("Approve SiteScore override").length).toBeGreaterThan(0);

    // The next envelope carries no governance side channel at all.
    rerender(<GovernanceWorkspace roleId="ops-lead" />);

    await waitFor(() =>
      expect(screen.queryByText("Approve SiteScore override")).not.toBeInTheDocument(),
    );
    expect(screen.getAllByText("目前沒有核准請求").length).toBeGreaterThan(0);
    // Withdrawal fails closed; it does not fall back to the local fixtures.
    expect(screen.queryByText("Close escalated service issue")).not.toBeInTheDocument();
  });

  it("keeps the Governance snapshot API as the source of truth over shell rows", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(governanceSnapshot)));

    render(
      <GovernanceWorkspace roleId="ops-lead" approvals={shellDecisionCards as never} />,
    );

    await waitFor(() =>
      expect(screen.getAllByText("Approve SiteScore override").length).toBeGreaterThan(0),
    );
    expect(screen.queryByText("SiteScore APR-501 複審")).not.toBeInTheDocument();
  });

  it("still fails closed in production when the Governance API is unavailable", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "down" }, 503)));

    render(
      <GovernanceWorkspace roleId="ops-lead" approvals={shellDecisionCards as never} />,
    );

    const gate = await screen.findByTestId("operator-data-unavailable");
    expect(gate).toHaveAttribute("data-status", "error");
    expect(screen.queryByTestId("governance-workspace")).not.toBeInTheDocument();
    expect(screen.queryByText("SiteScore APR-501 複審")).not.toBeInTheDocument();
  });
});

describe("normalizeGovernance* admission gate", () => {
  it("drops non-records and records that do not identify themselves as governance rows", () => {
    expect(
      normalizeGovernanceApprovals([
        null,
        "x",
        7,
        { id: "A", module: { nested: true }, status: null, priority: [] },
        { id: "B", title: "No module", status: "pending" },
        { module: "Govern", title: "No id", status: "pending" },
        { id: "C", module: "Govern", title: "No status" },
      ]),
    ).toEqual([]);

    expect(
      normalizeGovernanceDecisionRows([{ id: "D", module: "Govern" }, { module: "Govern", item: "x" }]),
    ).toEqual([]);

    // An audit entry that cannot say what happened is not an audit entry.
    expect(normalizeGovernanceAuditRows([{ id: "AUD", category: "system" }])).toEqual([]);
  });

  it("never fabricates a missing module, requestor, time, risk or status", () => {
    const [approval] = normalizeGovernanceApprovals([
      { id: "A", module: "SiteScore", title: "CANDIDATE-77", status: "PENDING_REVIEW" },
    ]);

    expect(approval).toEqual({
      id: "A",
      module: "SiteScore",
      title: "CANDIDATE-77",
      status: "PENDING_REVIEW",
    });
    expect(approval).not.toHaveProperty("requestor");
    expect(approval).not.toHaveProperty("submittedAt");
    expect(approval).not.toHaveProperty("priority");
    expect(approval).not.toHaveProperty("risk");
    expect(approval).not.toHaveProperty("evidence");
  });

  it("drops fields whose value cannot be rendered as text", () => {
    const [decision] = normalizeGovernanceDecisionRows([
      {
        id: "DEC-AVM-1",
        module: "AVM",
        item: "STORE-3",
        // The canonical AVM builder emits a structured fair-price payload here.
        systemRecommendation: { amount: 1200, currency: "TWD" },
        datasetSnapshot: ["snap-1", "snap-2"],
        finalDecision: "APPROVED",
      },
    ]);

    expect(decision).not.toHaveProperty("systemRecommendation");
    expect(decision).not.toHaveProperty("datasetSnapshot");
    expect(decision.finalDecision).toBe("APPROVED");
  });

  it("returns an empty list for non-array input", () => {
    expect(normalizeGovernanceApprovals(undefined)).toEqual([]);
    expect(normalizeGovernanceDecisionRows({ nope: true })).toEqual([]);
    expect(normalizeGovernanceAuditRows("nope")).toEqual([]);
  });

  it("keeps well-formed governance rows intact", () => {
    const [approval] = normalizeGovernanceApprovals(governanceSnapshot.approvals);
    expect(approval.module).toBe("Network");
    expect(approval.priority).toBe("critical");
    expect(approval.requestor).toBe("Expansion Manager");
    expect(approval.evidence).toEqual([
      { id: "EV-9", label: "SiteScore v4.8", type: "model", state: "ready" },
    ]);

    const [decision] = normalizeGovernanceDecisionRows(governanceSnapshot.decisions);
    expect(decision.finalDecision).toBe("Approved");

    const [audit] = normalizeGovernanceAuditRows(governanceSnapshot.auditRows);
    expect(audit.category).toBe("approval");
    expect(audit.correlationId).toBe("corr-site-9");
  });

  it("reads the canonical transition timestamp without inventing one", () => {
    const [audit] = normalizeGovernanceAuditRows([
      {
        id: "SITE-1:2026-07-26T02:00:00Z:APPROVE",
        module: "SiteScore",
        action: "APPROVE",
        actor: "expansion-manager",
        occurredAt: "2026-07-26T02:00:00Z",
      },
    ]);

    expect(audit.timestamp).toBe("2026-07-26T02:00:00Z");
    expect(audit).not.toHaveProperty("category");

    const [undated] = normalizeGovernanceAuditRows([
      { id: "AUD-2", action: "APPROVE", actor: "expansion-manager" },
    ]);
    expect(undated).not.toHaveProperty("timestamp");
  });

  it("drops status-board rows and evidence packages that are not fully reported", () => {
    const board = normalizeGovernanceStatusBoard({
      dataQuality: [
        { source: "Listings", status: "正常", good: true, note: "live" },
        { source: "Camera Events", status: "延遲", note: "missing good flag" },
        { status: "正常", good: true, note: "unnamed subject" },
      ],
      models: "nope",
    });

    expect(board?.dataQuality).toEqual([
      { source: "Listings", status: "正常", good: true, note: "live" },
    ]);
    expect(board?.models).toEqual([]);

    expect(
      normalizeGovernanceEvidencePackages([
        { id: "EVD-1", range: "2026-06", mod: "Govern", fmt: "PDF", t: "2026-07-01 10:15", by: "周明德" },
        { id: "EVD-2", range: "2026-05", mod: "Govern", fmt: "CSV", t: "2026-06-15 14:22" },
      ]),
    ).toEqual([
      { id: "EVD-1", range: "2026-06", mod: "Govern", fmt: "PDF", t: "2026-07-01 10:15", by: "周明德" },
    ]);
  });
});

describe("Operator console govern route with a delayed shell envelope", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    window.sessionStorage.clear();
  });

  /** Stub the console's two reads; the bootstrap envelope resolves only on release. */
  function stubDelayedConsoleFetch() {
    const bootstrap: { release: () => void } = { release: () => undefined };
    const bootstrapGate = new Promise<void>((resolve) => {
      bootstrap.release = resolve;
    });

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/v1/operator/bootstrap")) {
          await bootstrapGate;
          return jsonResponse(shellEnvelopePayload);
        }
        if (url.includes("/api/v1/operator/governance/snapshot")) {
          return jsonResponse(governanceSnapshot);
        }
        return jsonResponse({ detail: "not routed" }, 503);
      }),
    );

    return bootstrap;
  }

  /** The Govern route rendered without a route error and bound to the API. */
  async function expectGovernRouteHealthy() {
    expect(await screen.findByTestId("governance-workspace")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getAllByText("Approve SiteScore override").length).toBeGreaterThan(0),
    );
    // Shell decision cards are Today rows; they never become governance approvals.
    expect(screen.queryByText("SiteScore APR-501 複審")).not.toBeInTheDocument();
  }

  it("survives the delayed envelope on the direct /operator?ws=govern route", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const bootstrap = stubDelayedConsoleFetch();

    render(<OperatorConsole searchParams={{ ws: "govern" }} />);

    // Route is selected while the shell envelope is still in flight.
    expect(await screen.findByTestId("operator-console")).toBeInTheDocument();
    bootstrap.release();

    await expectGovernRouteHealthy();
  });

  it("survives the delayed envelope on a reload of the Govern route", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");

    const first = stubDelayedConsoleFetch();
    const firstRender = render(<OperatorConsole searchParams={{ ws: "govern" }} />);
    expect(await screen.findByTestId("operator-console")).toBeInTheDocument();
    first.release();
    await expectGovernRouteHealthy();
    firstRender.unmount();

    // Reload: a fresh mount replays the same deep link with the workspace
    // preference already persisted in sessionStorage.
    expect(window.sessionStorage.getItem("oday.operator.workspace")).toBe("govern");
    const second = stubDelayedConsoleFetch();
    render(<OperatorConsole searchParams={{ ws: "govern" }} />);
    expect(await screen.findByTestId("operator-console")).toBeInTheDocument();
    second.release();

    await expectGovernRouteHealthy();
  });

  it("survives the delayed envelope when Govern is opened from the workspace nav", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const bootstrap = stubDelayedConsoleFetch();

    render(<OperatorConsole searchParams={{}} />);
    expect(await screen.findByTestId("operator-console")).toBeInTheDocument();
    bootstrap.release();
    await waitFor(() => expect(screen.getByText("Live API")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /治理稽核/ }));

    await expectGovernRouteHealthy();
  });

  it("survives the delayed envelope when Govern is opened from the approval chip", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const bootstrap = stubDelayedConsoleFetch();

    render(<OperatorConsole searchParams={{}} />);
    expect(await screen.findByTestId("operator-console")).toBeInTheDocument();
    bootstrap.release();
    await waitFor(() => expect(screen.getByText("Live API")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("operator-approval-count"));

    await expectGovernRouteHealthy();
  });

  it("survives the delayed envelope when Govern is opened from the command palette", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const bootstrap = stubDelayedConsoleFetch();

    render(<OperatorConsole searchParams={{}} />);
    expect(await screen.findByTestId("operator-console")).toBeInTheDocument();
    bootstrap.release();
    await waitFor(() => expect(screen.getByText("Live API")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("operator-command-trigger"));
    const palette = await screen.findByTestId("operator-command-palette");
    fireEvent.change(screen.getByRole("combobox", { name: /Command palette search/ }), {
      target: { value: "治理稽核" },
    });
    fireEvent.click(await within(palette).findByRole("option", { name: /治理稽核/ }));

    await expectGovernRouteHealthy();
  });

  it("clears a governance side channel that the next envelope no longer carries", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");

    /** Two roles so the console can switch role and reload the shell envelope. */
    const roles = [
      {
        id: "ops-lead",
        label: "營運主管",
        subtitle: "全域監控",
        allowedWorkspaces: ["today", "store", "growth", "network", "govern"],
      },
      {
        id: "cs-lead",
        label: "客服主管",
        subtitle: "評論、客服案件與門市回覆",
        allowedWorkspaces: ["today", "store", "govern"],
      },
    ];
    const withSideChannel = {
      ...shellEnvelopePayload,
      navigation: { ...shellEnvelopePayload.navigation, roles },
      governanceApprovals: [
        {
          id: "APR-SIDE-1",
          module: "Govern",
          title: "Superseded side-channel approval",
          requestor: "PM／稽核",
          submittedAt: "2026-07-26T01:00:00Z",
          status: "pending",
        },
      ],
    };
    const withoutSideChannel = {
      ...shellEnvelopePayload,
      navigation: { ...shellEnvelopePayload.navigation, roles },
    };

    let bootstrapCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/v1/operator/bootstrap")) {
          bootstrapCalls += 1;
          return jsonResponse(bootstrapCalls === 1 ? withSideChannel : withoutSideChannel);
        }
        // The Govern API stays unavailable so the side channel is what renders.
        return jsonResponse({ detail: "down" }, 503);
      }),
    );

    render(<OperatorConsole searchParams={{ ws: "govern" }} />);
    expect(await screen.findByTestId("operator-console")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getAllByText("Superseded side-channel approval").length).toBeGreaterThan(0),
    );

    // Switching role reloads the shell envelope; the new one has no
    // `governanceApprovals`, so the rows it replaces must not survive.
    fireEvent.click(screen.getByTestId("operator-command-trigger"));
    const palette = await screen.findByTestId("operator-command-palette");
    fireEvent.change(screen.getByRole("combobox", { name: /Command palette search/ }), {
      target: { value: "切換角色：客服主管" },
    });
    fireEvent.click(await within(palette).findByRole("option", { name: /切換角色：客服主管/ }));

    await waitFor(() => expect(bootstrapCalls).toBeGreaterThan(1));
    await waitFor(() =>
      expect(screen.queryByText("Superseded side-channel approval")).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId("governance-workspace")).toBeInTheDocument();
  });
});
