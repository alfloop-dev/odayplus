import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GovernanceWorkspace, inspectGovernanceSnapshot } from "../GovernanceWorkspace";
import { OperatorConsole } from "../OperatorConsole";
import { normalizeGovernanceApprovals, normalizeGovernanceStatusBoard } from "../governance/governanceEnvelope";
import { readGovernanceViewState, type GovernanceViewState } from "../governance/governanceUrlState";

// Preserve the existing protected-read premise while the shell now rechecks
// the canonical session. This is unit transport data, not a grant/principal.
function stubAuthenticatedFetch(read: typeof fetch) {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => String(input) === "/auth/session"
    ? Promise.resolve(new Response(JSON.stringify({ subject: "unit-shell-account", expiresAt: Math.floor(Date.now() / 1000) + 3600 }), {
      status: 200, headers: { "Content-Type": "application/json" },
    })) : read(input, init)));
}

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

describe("Governance URL target and decision ownership", () => {
  const second = { ...snapshot.approvals[0], id:"APR-LIVE-2", title:"Second scoped approval", evidence:[{ id:"EV-SECOND", label:"Second scoped evidence" }] };
  const twoRecords = { ...decidingSnapshot, approvals:[snapshot.approvals[0], second] };
  const target: GovernanceViewState = { tab:"approvals", approvalId:second.id, evidenceId:"EV-SECOND", auditCategory:"all" };
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllEnvs(); });

  function reads() {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE","true");
    const fetchMock=vi.fn(async (input:RequestInfo|URL,_init?:RequestInit) => new Response(JSON.stringify(String(input).endsWith("/snapshot") ? twoRecords : {items:[]}),{status:200}));
    stubAuthenticatedFetch(fetchMock);return fetchMock;
  }

  it("keeps the requested second approval and evidence after asynchronous snapshot loading", async () => {
    reads();
    render(<GovernanceWorkspace roleId="ops-lead" viewState={target} />);
    await screen.findByRole("heading",{name:second.title});
    expect(screen.getByTestId("governance-selected-evidence")).toHaveTextContent("EV-SECOND");
    expect(screen.getByTestId("governance-selected-evidence")).toHaveTextContent("未提供");
    expect(within(screen.getByRole("article",{name:"Approval detail"})).queryByRole("heading",{name:snapshot.approvals[0].title})).not.toBeInTheDocument();
  });

  it("never substitutes a first approval or evidence for a missing URL target", async () => {
    const fetchMock=reads();
    const view=render(<GovernanceWorkspace roleId="ops-lead" viewState={{...target,approvalId:"APR-NOT-IN-SCOPE"}} />);
    expect(await screen.findByTestId("governance-approval-unavailable")).toHaveTextContent("指定核准項目未取得");
    expect(within(screen.getByRole("article",{name:"Approval detail"})).queryByRole("button",{name:"核准"})).not.toBeInTheDocument();
    view.rerender(<GovernanceWorkspace roleId="ops-lead" viewState={{...target,evidenceId:"EV-NOT-IN-SCOPE"}} />);
    expect(await screen.findByTestId("governance-evidence-unavailable")).toHaveTextContent("指定證據未取得");
    expect(screen.queryByTestId("governance-selected-evidence")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([,init])=>!init || (init as RequestInit).method!=="POST")).toBe(true);
  });

  it("retains the record-bound reason when changing evidence or tabs", async () => {
    reads();const navigate=vi.fn();
    const view=render(<GovernanceWorkspace roleId="ops-lead" viewState={target} onViewStateChange={navigate} />);
    await screen.findByRole("heading",{name:second.title});
    fireEvent.change(screen.getByLabelText(/決策理由/),{target:{value:"Reason belongs only to the second approval"}});
    view.rerender(<GovernanceWorkspace roleId="ops-lead" viewState={{...target,evidenceId:null}} onViewStateChange={navigate} />);
    expect(screen.getByLabelText(/決策理由/)).toHaveValue("Reason belongs only to the second approval");
    fireEvent.click(screen.getByTestId("governance-tab-audit"));
    expect(navigate).toHaveBeenCalledWith(expect.objectContaining({tab:"audit",approvalId:second.id}));
    view.rerender(<GovernanceWorkspace roleId="ops-lead" viewState={{...target,approvalId:snapshot.approvals[0].id,evidenceId:null}} />);
    expect(screen.getByLabelText(/決策理由/)).toHaveValue("");
  });

  it("freezes navigation while a decision is pending and never copies its reason into the next target", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE","true");
    let settle:(response:Response)=>void=()=>{};
    const fetchMock=vi.fn((input:RequestInfo|URL,init?:RequestInit)=>init?.method==="POST"
      ? new Promise<Response>(resolve=>{settle=resolve;})
      : Promise.resolve(new Response(JSON.stringify(String(input).endsWith("/snapshot")?twoRecords:{items:[]}),{status:200})));
    stubAuthenticatedFetch(fetchMock);
    const view=render(<GovernanceWorkspace roleId="ops-lead" viewState={target} />);
    await screen.findByRole("heading",{name:second.title});
    fireEvent.change(screen.getByLabelText(/決策理由/),{target:{value:"Reason belongs only to the second approval"}});
    fireEvent.click(screen.getByRole("button",{name:"退回修改"}));
    expect(screen.getByLabelText(/決策理由/)).toHaveAttribute("readonly");
    expect(screen.getByTestId("governance-tab-audit")).toBeDisabled();
    view.rerender(<GovernanceWorkspace roleId="ops-lead" viewState={{...target,approvalId:snapshot.approvals[0].id,evidenceId:null}} />);
    expect(screen.getByRole("heading",{name:second.title})).toBeInTheDocument();
    const post=fetchMock.mock.calls.find(([,init])=>init?.method==="POST")!;
    expect(JSON.parse(post[1]!.body as string)).toEqual(expect.objectContaining({approvalId:second.id,reason:"Reason belongs only to the second approval"}));
    settle(new Response("Synthetic unconfirmed result",{status:503}));
    await screen.findByRole("heading",{name:snapshot.approvals[0].title});
    expect(screen.getByLabelText(/決策理由/)).toHaveValue("");
  });
});

describe("GovernanceWorkspace high-risk failures", () => {
  it("labels an unsubmitted canonical PriceOps row without claiming a completed decision", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const unsubmitted = {
      ...decidingSnapshot,
      source: "canonical",
      approvals: [{
        ...snapshot.approvals[0], id: "PRICEOPS-UNSUBMITTED", module: "PriceOps",
        title: "Canonical pricing revision", status: "not_submitted",
      }],
      decisions: [],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => new Response(
      JSON.stringify(String(input).endsWith("/snapshot") ? unsubmitted : { items: [] }),
      { status: 200 },
    ));
    stubAuthenticatedFetch(fetchMock);
    render(<GovernanceWorkspace roleId="ops-lead" />);
    await screen.findByRole("heading", { name: "Canonical pricing revision" });
    expect(within(screen.getByLabelText("核准佇列")).getByText("尚未送審")).toBeInTheDocument();
    expect(screen.getByLabelText("Governance state")).toHaveTextContent("0 件待核准");
    const detail = within(screen.getByRole("article", { name: "Approval detail" }));
    expect(detail.getByText("尚無本輪決策。")).toBeInTheDocument();
    expect(detail.queryByText(/已完成決策/)).not.toBeInTheDocument();
    expect(detail.queryByRole("button", { name: "核准" })).not.toBeInTheDocument();
    expect(detail.queryByRole("button", { name: "退回修改" })).not.toBeInTheDocument();
    expect(detail.queryByRole("button", { name: "駁回" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([, init]) => !(init as RequestInit | undefined)?.method || (init as RequestInit).method !== "POST")).toBe(true);
  });
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
    stubAuthenticatedFetch( vi.fn(async () => new Response(JSON.stringify(canonical), { status: 200 })));
    render(<GovernanceWorkspace roleId="pm-audit" canDecide={false} viewState={readGovernanceViewState(new URLSearchParams("ws=govern&tab=statusBoard"))} />);
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
    stubAuthenticatedFetch( fetchMock);

    const view = render(<GovernanceWorkspace roleId="ops-lead" />);
    expect(await screen.findByTestId("governance-workspace")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "核准" }));
    expect(await screen.findByText("尚未確認決策結果，請重試相同內容以核對。")).toBeInTheDocument();
    expect(screen.queryByText(/已核准決策/)).not.toBeInTheDocument();

    view.rerender(<GovernanceWorkspace roleId="ops-lead" viewState={readGovernanceViewState(new URLSearchParams("ws=govern&tab=evidencePackage"))} />);
    fireEvent.click(screen.getByTestId("governance-tab-evidencePackage"));
    fireEvent.click(screen.getByTestId("governance-export-button"));
    await waitFor(() =>
      expect(screen.queryByTestId("evidence-package-result")).not.toBeInTheDocument(),
    );
  });

  it("never falls back to local approval after an uncertain API result, even in fixture mode", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    let attempts = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        attempts += 1;
        if (attempts === 1) throw new Error("Synthetic lost acknowledgement");
        const body = JSON.parse(init.body as string);
        return new Response(JSON.stringify({ approvalId: body.approvalId, action: body.action, status: "approved", finalDecision: "Approved" }));
      }
      return new Response(JSON.stringify(decidingSnapshot));
    });
    stubAuthenticatedFetch( fetchMock);
    const onApprove = vi.fn();
    const onDecisionConfirmed = vi.fn();
    render(<GovernanceWorkspace roleId="ops-lead" callbacks={{ onApprove }} onDecisionConfirmed={onDecisionConfirmed} />);
    await screen.findByRole("heading", { name: snapshot.approvals[0].title });
    fireEvent.click(screen.getByRole("button", { name: "核准" }));
    await screen.findByText("尚未確認決策結果，請重試相同內容以核對。");
    expect(onApprove).not.toHaveBeenCalled();
    expect(onDecisionConfirmed).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "核准" }));
    await waitFor(() => expect(onApprove).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(onDecisionConfirmed).toHaveBeenCalledTimes(1));
    const writes = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(new Headers(writes[0][1]!.headers).get("Idempotency-Key")).toBe(new Headers(writes[1][1]!.headers).get("Idempotency-Key"));
  });

  it("does not accept another approval's acknowledgement", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    stubAuthenticatedFetch( vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => new Response(JSON.stringify(init?.method === "POST"
      ? { approvalId: "wrong-approval", action: "approve", status: "approved", finalDecision: "Approved" }
      : decidingSnapshot))));
    const onApprove = vi.fn();
    const onDecisionConfirmed = vi.fn();
    render(<GovernanceWorkspace roleId="ops-lead" callbacks={{ onApprove }} onDecisionConfirmed={onDecisionConfirmed} />);
    await screen.findByRole("heading", { name: snapshot.approvals[0].title });
    fireEvent.click(screen.getByRole("button", { name: "核准" }));
    await screen.findByText("尚未確認決策結果，請重試相同內容以核對。");
    expect(onApprove).not.toHaveBeenCalled();
    expect(onDecisionConfirmed).not.toHaveBeenCalled();
  });

  it("does not report an offline fixture decision as a confirmed API write", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    stubAuthenticatedFetch(vi.fn().mockRejectedValue(new Error("Explicit offline test transport")));
    const onApprove = vi.fn();
    const onDecisionConfirmed = vi.fn();
    render(<GovernanceWorkspace roleId="ops-lead" callbacks={{ onApprove }} onDecisionConfirmed={onDecisionConfirmed} />);
    await screen.findByRole("heading", { name: "Close escalated service issue" });
    fireEvent.click(screen.getByRole("button", { name: "核准" }));
    await waitFor(() => expect(onApprove).toHaveBeenCalledTimes(1));
    expect(onDecisionConfirmed).not.toHaveBeenCalled();
  });

  it("retains export scope/key after failure and creates no local package", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    let settle!: (value: Response) => void;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => init?.method === "POST"
      ? new Promise(resolve => { settle = resolve; })
      : Promise.resolve(new Response(JSON.stringify(decidingSnapshot))));
    stubAuthenticatedFetch( fetchMock);
    const view = render(<GovernanceWorkspace roleId="ops-lead" />);
    await screen.findByRole("heading", { name: snapshot.approvals[0].title });
    view.rerender(<GovernanceWorkspace roleId="ops-lead" viewState={readGovernanceViewState(new URLSearchParams("ws=govern&tab=evidencePackage"))} />);
    fireEvent.click(screen.getByTestId("governance-tab-evidencePackage"));
    fireEvent.click(screen.getByTestId("governance-export-button"));
    expect(screen.getByLabelText("起始日")).toBeDisabled();
    expect(screen.getByLabelText("格式")).toBeDisabled();
    settle(new Response("Synthetic unavailable", { status: 503 }));
    await screen.findByText("尚未確認匯出結果，請重試相同內容；尚無可下載證據包。");
    expect(screen.queryByTestId("evidence-package-result")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("governance-export-button"));
    const writes = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(writes).toHaveLength(2);
    expect(new Headers(writes[0][1]!.headers).get("Idempotency-Key")).toBe(new Headers(writes[1][1]!.headers).get("Idempotency-Key"));
    settle(new Response("Synthetic still unavailable", { status: 503 }));
  });

  it("blocks seed governance payloads instead of rendering local approvals", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    stubAuthenticatedFetch( vi.fn().mockResolvedValue(
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
    stubAuthenticatedFetch( vi.fn().mockRejectedValue(new Error("offline")));

    render(<GovernanceWorkspace roleId="ops-lead" />);

    expect(await screen.findByTestId("governance-workspace")).toBeInTheDocument();
    expect(screen.getAllByText("Close escalated service issue").length).toBeGreaterThan(0);
  });

  it("keeps the R7 default approvals structure and expands evidence only after explicit selection", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    stubAuthenticatedFetch( vi.fn().mockResolvedValue(
      new Response(JSON.stringify(snapshot), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ));

    render(<GovernanceWorkspace roleId="ops-lead" />);

    expect(await screen.findByText("治理稽核")).toBeInTheDocument();
    expect(screen.getByText("核准、決策、稽核與證據 — 所有處置的可追溯層")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "核准佇列" })).not.toBeInTheDocument();
    for (const heading of ["申請摘要", "系統建議", "風險說明", "決策邊界"]) {
      expect(within(screen.getByRole("article", { name: "Approval detail" })).queryByRole("heading", { name: heading })).not.toBeInTheDocument();
    }
    expect(screen.getByText("關聯：SITE-LIVE-1")).toBeInTheDocument();
    expect(screen.getByLabelText(/決策理由/)).toHaveAttribute("rows", "2");
    expect(within(screen.getByRole("navigation", { name: "Governance tabs" })).getAllByRole("button")).toHaveLength(3);
    expect(screen.queryByTestId("governance-tab-evidencePackage")).not.toBeInTheDocument();
    expect(screen.getAllByText("風險 高")).toHaveLength(2);
    expect(screen.getAllByText("待核准")).toHaveLength(2);
    expect(screen.queryByTestId("governance-selected-evidence")).not.toBeInTheDocument();
    expect(screen.queryByTestId("decision-comments-panel")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /SiteScore v4.8/ }));
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
    stubAuthenticatedFetch( fetchMock);

    render(<GovernanceWorkspace roleId="ops-lead" />);
    await screen.findByTestId("governance-workspace");

    fireEvent.click(screen.getByRole("button", { name: "退回修改" }));

    expect(screen.getByRole("alert")).toHaveTextContent("退回或駁回理由需至少 10 個字");
    // The R7 default does not mount the optional comments extension. The
    // invalid decision stays client-side and does not issue a write.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("renders dense decision, audit, evidence package and status surfaces", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    stubAuthenticatedFetch( vi.fn().mockResolvedValue(
      new Response(JSON.stringify(snapshot), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ));

    const view = render(<GovernanceWorkspace roleId="ops-lead" />);
    await screen.findByTestId("governance-workspace");

    fireEvent.click(screen.getByTestId("governance-tab-decisions"));
    expect(screen.getByText("系統建議、最終決策與採用證據的不可分割紀錄")).toBeInTheDocument();
    expect(screen.getByText("APR-LIVE-0")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("governance-tab-audit"));
    expect(screen.getByText("隱私敏感")).toBeInTheDocument();
    expect(screen.getByText("corr-live-1")).toBeInTheDocument();

    view.rerender(<GovernanceWorkspace roleId="ops-lead" viewState={readGovernanceViewState(new URLSearchParams("ws=govern&tab=evidencePackage"))} />);
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
  stubAuthenticatedFetch( fetchMock);
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
    const view = render(<OperatorConsole searchParams={{ ws: "today" }} />);

    const navigation = await screen.findByRole("navigation", { name: "Operator workspaces" });
    await waitFor(() => expect(within(navigation).getByRole("button", { name: /Today/ })).toHaveAttribute("aria-current", "page"));
    expect(screen.queryByTestId("governance-workspace")).toBeNull();
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/governance/snapshot"))).toBe(false);

    fireEvent.click(within(navigation).getByRole("button", { name: /Govern/ }));
    const authority = await screen.findByTestId("governance-decision-authority", {}, { timeout: 5000 });
    expect(authority).toHaveAttribute("data-authority", "denied");
    expect(screen.getAllByText("Review candidate").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "核准" })).toBeNull();
    nav.search = "ws=govern&tab=userManagement";
    view.rerender(<OperatorConsole searchParams={{ ws: "govern", tab: "userManagement" }} />);
    await screen.findByTestId("governance-tab-featureFlags");
    expect(screen.getByTestId("governance-tab-userManagement")).toBeEnabled();
    expect(screen.getByTestId("governance-tab-featureFlags")).toBeEnabled();
    expect(writes).toEqual([]);
  });

  it("keeps the 營運主管 persona from claiming decisions for a server-verified auditor+platform_admin", async () => {
    const { writes } = stubConsoleGovernance(readAdminSnapshot);

    const view = render(<OperatorConsole searchParams={{ ws: "govern" }} />);

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

    nav.search = "ws=govern&tab=evidencePackage";
    view.rerender(<OperatorConsole searchParams={{ ws: "govern", tab: "evidencePackage" }} />);
    await screen.findByTestId("governance-export-button");
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

    const view = render(<OperatorConsole searchParams={{ ws: "govern" }} />);

    const authority = await screen.findByTestId("governance-decision-authority", {}, { timeout: 5000 });
    expect(authority).toHaveAttribute("data-authority", "granted");
    expect(authority).toHaveTextContent("可決策");
    expect(screen.getByRole("button", { name: "核准" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "退回修改" })).toBeEnabled();
    expect(screen.queryByTestId("governance-decision-readonly")).toBeNull();
    nav.search = "ws=govern&tab=evidencePackage";
    view.rerender(<OperatorConsole searchParams={{ ws: "govern", tab: "evidencePackage" }} />);
    await screen.findByTestId("governance-export-button");
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


describe("Governance native owner review handoff", () => {
  const intervention = {
    id: "INT-NATIVE-1", module: "Store Ops", title: "Native intervention review", status: "pending",
    entityRef: "INT-NATIVE-1",
    nativeTarget: { workspace: "today", tab: "interventions", entityId: "INT-NATIVE-1" },
    actionAuthority: { decide: false, reason: "NATIVE_OWNER_REVIEW_REQUIRED" },
  };
  const signedSiteScore = {
    id: "SIGNATURE-NATIVE-1", module: "SiteScore", title: "Signed site review", status: "pending",
    entityRef: "CANDIDATE-NATIVE-1", decisionId: "DECISION-NATIVE-1",
    nativeTarget: { workspace: "network", tab: "review", entityId: "CANDIDATE-NATIVE-1",
      candidateId: "CANDIDATE-NATIVE-1", reviewId: "DECISION-NATIVE-1" },
    actionAuthority: { decide: false, reason: "NATIVE_OWNER_REVIEW_REQUIRED" },
  };
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

  function reads(approvals: unknown[]) {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => new Response(
      JSON.stringify(String(input).endsWith("/snapshot") ? { ...decidingSnapshot, source: "canonical", approvals } : { items: [] }),
      { status: 200 },
    ));
    stubAuthenticatedFetch(fetchMock);
    return fetchMock;
  }
  function noGenericControls() {
    const detail = within(screen.getByRole("article", { name: "Approval detail" }));
    for (const name of ["核准", "退回修改", "駁回"]) expect(detail.queryByRole("button", { name: new RegExp(`^${name}$`) })).toBeNull();
    expect(detail.queryByLabelText(/決策理由/)).toBeNull();
  }

  it("counts the native Intervention row and opens its full owner review without a generic decision", async () => {
    const fetchMock = reads([intervention]);
    const onOpenNativeTarget = vi.fn(), onApprove = vi.fn(), onReturn = vi.fn(), onReject = vi.fn();
    render(<GovernanceWorkspace roleId="ops-lead" callbacks={{ onOpenNativeTarget, onApprove, onReturn, onReject }} />);
    await screen.findByRole("heading", { name: intervention.title });
    expect(screen.getByLabelText("Governance state")).toHaveTextContent("1 件待核准");
    expect(within(screen.getByLabelText("核准佇列")).getByText(intervention.title)).toBeInTheDocument();
    noGenericControls();
    fireEvent.click(screen.getByRole("button", { name: "開啟完整干預審查" }));
    expect(onOpenNativeTarget).toHaveBeenCalledWith(intervention.nativeTarget, expect.objectContaining({ id: intervention.id }));
    expect(onApprove).not.toHaveBeenCalled(); expect(onReturn).not.toHaveBeenCalled(); expect(onReject).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.every(([, init]) => init?.method !== "POST")).toBe(true);
  });

  it("opens the exact signed SiteScore candidate and decision tuple without a generic decision", async () => {
    const fetchMock = reads([signedSiteScore]), onOpenNativeTarget = vi.fn(), onApprove = vi.fn();
    render(<GovernanceWorkspace roleId="ops-lead" callbacks={{ onOpenNativeTarget, onApprove }} />);
    await screen.findByRole("heading", { name: signedSiteScore.title });
    noGenericControls();
    fireEvent.click(screen.getByRole("button", { name: /^開啟完整審查$/ }));
    expect(onOpenNativeTarget).toHaveBeenCalledWith(signedSiteScore.nativeTarget, expect.objectContaining({ id: signedSiteScore.id, decisionId: signedSiteScore.decisionId }));
    expect(onApprove).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.every(([, init]) => init?.method !== "POST")).toBe(true);
  });

  it("retains native refusal for malformed, foreign or mismatched targets without a fallback", async () => {
    const malformedTargets = [
      { ...intervention.nativeTarget, href: "https://untrusted.invalid/review" },
      { ...intervention.nativeTarget, entityId: "INT-OTHER" },
      { workspace: "govern", tab: "approvals", entityId: intervention.id },
      { ...signedSiteScore.nativeTarget, reviewId: "DECISION-OTHER" },
      { ...signedSiteScore.nativeTarget, candidateId: "CANDIDATE-OTHER" },
    ];
    for (const [index, nativeTarget] of malformedTargets.entries()) {
      const raw = { ...(index < 3 ? intervention : signedSiteScore), nativeTarget };
      const normalized = normalizeGovernanceApprovals([raw])[0];
      expect(normalized.nativeTarget).toBeNull();
      expect(normalized.actionAuthority).toEqual({ decide: false, reason: "NATIVE_OWNER_REVIEW_REQUIRED" });
    }
    const fetchMock = reads([{ ...intervention, nativeTarget: malformedTargets[0] }]), onOpenNativeTarget = vi.fn();
    render(<GovernanceWorkspace roleId="ops-lead" callbacks={{ onOpenNativeTarget }} />);
    await screen.findByRole("heading", { name: intervention.title });
    noGenericControls();
    expect(screen.getByTestId("governance-native-target-unavailable")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /開啟完整/ })).toBeNull();
    expect(onOpenNativeTarget).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.every(([, init]) => init?.method !== "POST")).toBe(true);
  });

  it("preserves a native row refusal even when no target was supplied", async () => {
    const { nativeTarget: _nativeTarget, ...withoutTarget } = intervention;
    const fetchMock = reads([withoutTarget]), onOpenNativeTarget = vi.fn();
    render(<GovernanceWorkspace roleId="ops-lead" callbacks={{ onOpenNativeTarget }} />);
    await screen.findByRole("heading", { name: intervention.title });
    expect(screen.getByTestId("governance-native-target-unavailable")).toBeInTheDocument();
    noGenericControls();
    expect(onOpenNativeTarget).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.every(([, init]) => init?.method !== "POST")).toBe(true);
  });

  it("keeps ordinary canonical rows and their original three decision controls alongside native rows", async () => {
    const fetchMock = reads([intervention, snapshot.approvals[0]]);
    render(<GovernanceWorkspace roleId="ops-lead" callbacks={{ onOpenNativeTarget: vi.fn() }} />);
    await screen.findByRole("heading", { name: intervention.title });
    expect(screen.getByLabelText("Governance state")).toHaveTextContent("2 件待核准");
    noGenericControls();
    fireEvent.click(within(screen.getByLabelText("核准佇列")).getByRole("button", { name: new RegExp(snapshot.approvals[0].id) }));
    await screen.findByRole("heading", { name: snapshot.approvals[0].title });
    for (const name of ["核准", "退回修改", "駁回"]) expect(screen.getByRole("button", { name: new RegExp(`^${name}$`) })).toBeEnabled();
    expect(screen.getByLabelText(/決策理由/)).toBeInTheDocument();
    expect(screen.queryByTestId("governance-native-owner-review")).toBeNull();
    expect(fetchMock.mock.calls.every(([, init]) => init?.method !== "POST")).toBe(true);
  });

  it("retains the Console workspace denial for a signed review instead of granting a Network persona", async () => {
    window.sessionStorage.clear(); window.sessionStorage.setItem("oday.operator.role", "ops-lead");
    nav.search = "ws=govern"; vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const { fetchMock, writes } = stubConsoleGovernance({ ...decidingSnapshot, source: "canonical", approvals: [signedSiteScore] });
    render(<OperatorConsole searchParams={{ ws: "govern" }} />);
    await screen.findByRole("heading", { name: signedSiteScore.title });
    const before = window.location.href;
    fireEvent.click(screen.getByRole("button", { name: /^開啟完整審查$/ }));
    expect(await screen.findByText(/營運主管 暫無.*權限/)).toBeInTheDocument();
    expect(window.location.href).toBe(before);
    expect(screen.getByTestId("governance-workspace")).toBeInTheDocument();
    noGenericControls();
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/network-reviews"))).toBe(false);
    expect(writes).toEqual([]);
  });
});
