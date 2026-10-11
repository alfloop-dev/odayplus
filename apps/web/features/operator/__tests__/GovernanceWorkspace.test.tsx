import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GovernanceWorkspace, inspectGovernanceSnapshot } from "../GovernanceWorkspace";
import { OperatorConsole } from "../OperatorConsole";
import { normalizeGovernanceStatusBoard } from "../governance/governanceEnvelope";

const snapshot = {
  approvals: [{
    id: "APR-LIVE-1",
    module: "Network",
    title: "Review candidate",
    requestor: "Expansion",
    submittedAt: "2026-07-24T00:00:00Z",
    status: "pending",
    priority: "high",
    owner: "展店經理",
    sla: "18m",
    entityRef: "SITE-LIVE-1",
    summary: "Review the submitted site override.",
    systemRecommendation: "WAIT",
    evidence: [
      { id: "EV-LIVE-1", label: "SiteScore v4.8", type: "model", state: "ready" },
      { id: "EV-LIVE-2", label: "Dataset 2026-W30", type: "dataset", state: "stale" },
    ],
  }],
  decisions: [{
    id: "DEC-LIVE-1",
    module: "Network",
    item: "SITE-LIVE-0 GO override",
    systemRecommendation: "WAIT",
    finalDecision: "Approved",
    reason: "Lease evidence was independently reviewed.",
    actor: "展店經理",
    decidedAt: "2026-07-23T09:00:00Z",
    model: "sitescore-v4.8",
    datasetSnapshot: "network-2026-W30",
    approvalId: "APR-LIVE-0",
  }],
  auditRows: [{
    id: "AUD-LIVE-1",
    category: "camera",
    timestamp: "2026-07-24T00:10:00Z",
    actor: "Evidence service",
    action: "Evidence opened",
    module: "Network",
    entityRef: "SITE-LIVE-1",
    summary: "Restricted evidence opened for review.",
    correlationId: "corr-live-1",
  }],
  evidencePackages: [],
  statusBoard: {
    dataQuality: [{ source: "Listings", status: "ready", good: true, note: "live" }],
    models: [],
    connectors: [],
    sla: [],
    users: [],
    runbooks: [],
  },
  source: "operator-governance-production",
};

// actionAuthority as the API derives it for an operations_manager principal,
// the only canonical role holding intervention APPROVE/CREATE.
const decidingSnapshot = {
  ...snapshot,
  actionAuthority: { verified: true, systemRoles: ["operations_manager"], decide: true, exportEvidence: true },
};

// The deployed dev account on 2026-10-10: auditor + platform_admin, no
// business decision grant, viewing the console through the 營運主管 persona.
const readAdminSnapshot = {
  ...snapshot,
  actionAuthority: { verified: true, systemRoles: ["auditor", "platform_admin"], decide: false, exportEvidence: false },
};

describe("GovernanceWorkspace high-risk failures", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("renders the real canonical producer's repository counts without claiming model readiness", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const canonical = {
      approvals: [], decisions: [], auditRows: [], evidencePackages: [], source: "canonical",
      // GovernanceService._refresh_from_canonical, including a true zero state.
      statusBoard: [
        { name: "SiteScore decisions", status: "live", count: 0 },
        { name: "AVM cases", status: "live", count: 0 },
        { name: "NetPlan scenarios", status: "live", count: 0 },
        { name: "PriceOps plans", status: "live", count: 0 },
      ],
    };
    expect(inspectGovernanceSnapshot(canonical)).toBe("ready");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(canonical), { status: 200 })));
    render(<GovernanceWorkspace roleId="pm-audit" canDecide={false} />);
    await screen.findByTestId("governance-workspace");
    fireEvent.click(screen.getByTestId("governance-tab-statusBoard"));
    expect(screen.getByTestId("governance-record-counts")).toHaveTextContent("非模型／來源就緒證明");
    expect(screen.getByText("SiteScore decisions")).toBeInTheDocument();
    expect(screen.getAllByText("0 筆")).toHaveLength(4);
    expect(screen.queryByText("sitescore-v4.8")).toBeNull();
  });

  it.each([null, {}, { models: [] }, [{ name: "SiteScore decisions", status: "live", count: -1 }],
    [{ name: "SiteScore decisions", status: "live", count: "1" }],
    { ...snapshot.statusBoard, models: [{ name: "model", status: "ready" }] },
  ].map((statusBoard) => [statusBoard]))("fails closed on malformed or incomplete status DTO %j without throwing", (statusBoard) => {
    expect(inspectGovernanceSnapshot({ ...snapshot, statusBoard } as any)).toBe("empty");
  });

  it("does not inspect missing row arrays before validating the envelope", () => {
    expect(inspectGovernanceSnapshot({ source: "canonical", statusBoard: snapshot.statusBoard } as any)).toBe("empty");
    expect(inspectGovernanceSnapshot({ source: "canonical", approvals: [], decisions: [], auditRows: [], evidencePackages: [], statusBoard: [] })).toBe("empty");
    expect(normalizeGovernanceStatusBoard([{ name: "x", status: "live", count: Number.NaN }])).toBeNull();
    expect(inspectGovernanceSnapshot({ ...snapshot, source: "fixture", statusBoard: [] })).toBe("seed");
  });

  it("does not create a local decision or evidence package after API failure", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/snapshot") && (!init?.method || init.method === "GET")) {
        return new Response(JSON.stringify(decidingSnapshot), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ detail: "failed" }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<GovernanceWorkspace roleId="ops-lead" />);
    expect(await screen.findByTestId("governance-workspace")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "核准" }));
    expect(await screen.findByText("決策未送出（API 無法連線）")).toBeInTheDocument();
    expect(screen.queryByText(/已核准決策/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("governance-tab-evidencePackage"));
    fireEvent.click(screen.getByTestId("governance-export-button"));
    await waitFor(() =>
      expect(screen.queryByTestId("evidence-package-result")).not.toBeInTheDocument(),
    );
  });

  it("blocks seed governance payloads instead of rendering local approvals", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        ...snapshot,
        source: "fixture-governance-replay",
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ));

    render(<GovernanceWorkspace roleId="ops-lead" />);

    const gate = await screen.findByTestId("operator-data-unavailable");
    await waitFor(() => expect(gate).toHaveAttribute("data-status", "seed"));
    expect(screen.queryByTestId("governance-workspace")).not.toBeInTheDocument();
    expect(screen.queryByText("Close escalated service issue")).not.toBeInTheDocument();
  });

  it("retains governance fixtures in local mode when the API is unavailable", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    render(<GovernanceWorkspace roleId="ops-lead" />);

    expect(await screen.findByTestId("governance-workspace")).toBeInTheDocument();
    expect(screen.getAllByText("Close escalated service issue").length).toBeGreaterThan(0);
  });

  it("matches the Package 10 approval center and switches selected evidence", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify(snapshot), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ));

    render(<GovernanceWorkspace roleId="ops-lead" />);

    expect(await screen.findByText("治理稽核")).toBeInTheDocument();
    expect(screen.getByText("核准、決策、稽核與證據 — 所有處置的可追溯層")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "核准佇列" })).toBeInTheDocument();
    expect(screen.getAllByText("風險 高")).toHaveLength(2);
    expect(screen.getAllByText("待核准")).toHaveLength(2);
    expect(screen.getByTestId("governance-selected-evidence")).toHaveTextContent("SiteScore v4.8");

    fireEvent.click(screen.getByRole("button", { name: /Dataset 2026-W30/ }));

    expect(screen.getByTestId("governance-selected-evidence")).toHaveTextContent("EV-LIVE-2");
    expect(screen.getByTestId("governance-selected-evidence")).toHaveTextContent("stale");
  });

  it("requires a durable reason before return or reject", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(decidingSnapshot), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<GovernanceWorkspace roleId="ops-lead" />);
    await screen.findByTestId("governance-workspace");

    fireEvent.click(screen.getByRole("button", { name: "退回修改" }));

    expect(screen.getByRole("alert")).toHaveTextContent("退回或駁回理由需至少 10 個字");
    // Governance now also reads the attached comments sidecar for the
    // selected approval.
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("renders dense decision, audit, evidence package and status surfaces", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify(snapshot), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ));

    render(<GovernanceWorkspace roleId="ops-lead" />);
    await screen.findByTestId("governance-workspace");

    fireEvent.click(screen.getByTestId("governance-tab-decisions"));
    expect(screen.getByText("系統建議、最終決策與採用證據的不可分割紀錄")).toBeInTheDocument();
    expect(screen.getByText("APR-LIVE-0")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("governance-tab-audit"));
    expect(screen.getByText("隱私敏感")).toBeInTheDocument();
    expect(screen.getByText("corr-live-1")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("governance-tab-evidencePackage"));
    expect(screen.getByRole("button", { name: "產生 Evidence Package" })).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("governance-tab-statusBoard"));
    expect(screen.getByText("Data Quality 監控")).toBeInTheDocument();
    expect(screen.getByTestId("governance-sla-card")).toBeInTheDocument();
    expect(screen.getByTestId("governance-users-card")).toBeInTheDocument();
  });
});

const govRole = {
  id: "ops-lead",
  label: "營運主管",
  subtitle: "Workspace persona",
  allowedWorkspaces: ["today", "govern"],
  heroName: "Read Admin",
};

const governEnvelope = {
  meta: {
    source: "operator-shell-production",
    dataMode: "live",
    role: govRole,
    counts: { approvals: 0, critical: 0, notifications: 0, search: 0, taskCenter: 0 },
  },
  navigation: {
    roles: [govRole],
    workspaces: [
      { id: "today", label: "Today", shortLabel: "Today", description: "Live queue", allowed: true },
      { id: "govern", label: "Govern", shortLabel: "Govern", description: "Govern", allowed: true },
    ],
    allowedWorkspaces: ["today", "govern"],
  },
  header: { counts: { approvals: 0, critical: 0, notifications: 0, search: 0, taskCenter: 0 } },
  today: {
    hero: { name: "Read Admin", roleLabel: "營運主管", scope: "Tenant scope", dateLabel: "2026-10-10" },
    kpis: [{ label: "Live unresolved", value: "0", tone: "info" }],
    queue: [],
    decisions: [],
    riskRows: [],
    auditFeed: [],
  },
  notifications: [],
  search: { count: 0, items: [] },
};

const nav = vi.hoisted(() => ({ search: "ws=govern" }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/operator",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(nav.search),
}));

function stubConsoleGovernance(governanceSnapshot: unknown) {
  const writes: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input instanceof Request ? input.url : input), "http://localhost");
    if ((init?.method ?? "GET") !== "GET") writes.push(url.pathname);
    if (url.pathname === "/api/v1/operator/bootstrap") {
      return new Response(JSON.stringify(governEnvelope), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url.pathname === "/api/v1/operator/governance/snapshot") {
      return new Response(JSON.stringify(governanceSnapshot), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    return new Response(JSON.stringify({ detail: "not routed" }), { status: 503, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, writes };
}

describe("Governance decision authority in the production Operator Console mount", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    // The client-selected persona claims 營運主管; only the server decides authority.
    window.sessionStorage.setItem("oday.operator.role", "ops-lead");
    nav.search = "ws=govern";
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("defers the dormant workspace until navigation, then preserves read-admin authority and config access", async () => {
    nav.search = "ws=today";
    const { fetchMock, writes } = stubConsoleGovernance(readAdminSnapshot);
    render(<OperatorConsole searchParams={{ ws: "today" }} />);

    const navigation = await screen.findByRole("navigation", { name: "Operator workspaces" });
    await waitFor(() => expect(within(navigation).getByRole("button", { name: /Today/ })).toHaveAttribute("aria-current", "page"));
    expect(screen.queryByTestId("governance-workspace")).toBeNull();
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/governance/snapshot"))).toBe(false);

    fireEvent.click(within(navigation).getByRole("button", { name: /Govern/ }));
    const authority = await screen.findByTestId("governance-decision-authority", {}, { timeout: 5000 });
    expect(authority).toHaveAttribute("data-authority", "denied");
    expect(screen.getAllByText("Review candidate").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "核准" })).toBeNull();
    expect(screen.getByTestId("governance-tab-userManagement")).toBeEnabled();
    expect(screen.getByTestId("governance-tab-featureFlags")).toBeEnabled();
    expect(writes).toEqual([]);
  });

  it("keeps the 營運主管 persona from claiming decisions for a server-verified auditor+platform_admin", async () => {
    const { writes } = stubConsoleGovernance(readAdminSnapshot);

    render(<OperatorConsole searchParams={{ ws: "govern" }} />);

    const authority = await screen.findByTestId("governance-decision-authority", {}, { timeout: 5000 });
    expect(authority).toHaveAttribute("data-authority", "denied");
    expect(authority).toHaveTextContent("僅可查看");
    expect(authority).not.toHaveTextContent("可決策");
    expect(screen.getByTestId("governance-workspace-persona")).toHaveTextContent("視角：營運主管");
    expect(screen.getByLabelText("Governance state")).not.toHaveTextContent(/(^|[^僅])可決策/);
    // The legitimate scoped rows stay visible; only the decision controls go.
    expect(screen.getAllByText("Review candidate").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "核准" })).toBeNull();
    expect(screen.queryByRole("button", { name: "退回修改" })).toBeNull();
    expect(screen.queryByRole("button", { name: "駁回" })).toBeNull();
    const readOnly = screen.getByTestId("governance-decision-readonly");
    expect(readOnly).toHaveTextContent("auditor、platform_admin");
    expect(readOnly).toHaveTextContent("不代表決策權限");

    fireEvent.click(screen.getByTestId("governance-tab-evidencePackage"));
    expect(screen.getByTestId("governance-export-button")).toBeDisabled();
    expect(screen.getByTestId("governance-export-readonly")).toBeInTheDocument();

    // Platform-admin account/config surfaces remain reachable.
    expect(screen.getByTestId("governance-tab-userManagement")).toBeEnabled();
    expect(screen.getByTestId("governance-tab-featureFlags")).toBeEnabled();
    expect(writes).toEqual([]);
  });

  it("presents decision controls for a server-verified decision actor", async () => {
    stubConsoleGovernance(decidingSnapshot);

    render(<OperatorConsole searchParams={{ ws: "govern" }} />);

    const authority = await screen.findByTestId("governance-decision-authority", {}, { timeout: 5000 });
    expect(authority).toHaveAttribute("data-authority", "granted");
    expect(authority).toHaveTextContent("可決策");
    expect(screen.getByRole("button", { name: "核准" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "退回修改" })).toBeEnabled();
    expect(screen.queryByTestId("governance-decision-readonly")).toBeNull();
    fireEvent.click(screen.getByTestId("governance-tab-evidencePackage"));
    expect(screen.getByTestId("governance-export-button")).toBeEnabled();
  });

  it("confirms no decision authority when the server did not verify one", async () => {
    stubConsoleGovernance(snapshot);

    render(<OperatorConsole searchParams={{ ws: "govern" }} />);

    const authority = await screen.findByTestId("governance-decision-authority", {}, { timeout: 5000 });
    expect(authority).toHaveAttribute("data-authority", "unverified");
    expect(authority).toHaveTextContent("決策權限未確認");
    expect(screen.queryByRole("button", { name: "核准" })).toBeNull();
    expect(screen.getByTestId("governance-decision-readonly")).toHaveTextContent("尚未取得伺服器驗證的決策權限");
  });
});
