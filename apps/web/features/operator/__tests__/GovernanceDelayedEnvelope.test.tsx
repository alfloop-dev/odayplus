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
 *   - the Governance snapshot API stays the source of truth;
 *   - shell decision cards never masquerade as governance approvals;
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

describe("Govern workspace tolerates foreign and malformed external rows", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("renders instead of crashing when shell decision cards arrive on the approvals prop", async () => {
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
    // Degraded but renderable: the card keeps its title instead of throwing.
    expect(screen.getAllByText("SiteScore APR-501 複審").length).toBeGreaterThan(0);
  });

  it("renders malformed approval, decision and audit fields without throwing", async () => {
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
    expect(screen.getAllByText("No module or status").length).toBeGreaterThan(0);
    // The unusable rows are dropped rather than rendered as anonymous entries.
    expect(screen.getByLabelText("核准佇列")).toHaveTextContent("2 全部");
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

describe("normalizeGovernance* adapters", () => {
  it("drops non-records and coerces every required field to a safe string", () => {
    const rows = normalizeGovernanceApprovals([
      null,
      "x",
      7,
      { id: "A", module: { nested: true }, status: null, priority: [], submittedAt: undefined },
    ]);

    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ id: "A", module: "Govern", status: "pending", priority: "medium" });
    expect(typeof rows[0].requestor).toBe("string");
    expect(typeof rows[0].submittedAt).toBe("string");
    expect(rows[0].evidence).toEqual([]);
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
    expect(approval.evidence).toEqual([
      { id: "EV-9", label: "SiteScore v4.8", type: "model", state: "ready" },
    ]);

    const [decision] = normalizeGovernanceDecisionRows(governanceSnapshot.decisions);
    expect(decision.finalDecision).toBe("Approved");

    const [audit] = normalizeGovernanceAuditRows(governanceSnapshot.auditRows);
    expect(audit.category).toBe("approval");
    expect(audit.correlationId).toBe("corr-site-9");
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
});
