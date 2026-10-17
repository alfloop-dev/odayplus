import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NetPlanScenarioBuilder } from "../NetPlanScenarioBuilder";
import { createNetPlanBuilderApi, isCanonicalBuilder, type CanonicalBuilder, type NetPlanBuilderApi, type BuilderResult } from "../netplanBuilderClient";

// Isolated DTO/interaction fixture, never a production owner or approval.
function builder(overrides: Partial<CanonicalBuilder> = {}): CanonicalBuilder {
  return { scenarioId: "scenario-owned", scenarioName: "Scoped scenario", planningHorizon: "2026-H2",
    status: "draft", problemHash: "a".repeat(64), editable: true, canSolve: true,
    policyAvailable: false, disclosurePolicyVersionId: null,
    requiresNewDraft: false, isStale: false, solve: null, statusHistory: [],
    modelVersion: "netplan-v1", featureVersion: "features-v1", solverVersion: "solver-v1", policyVersion: "policy-v1",
    constraints: { max_budget: 1200, min_expected_gross_margin: null, min_capacity_delta: null,
      max_average_risk: 0.5, min_action_counts: {}, max_action_counts: {} },
    optionsByEntity: { "store-owned": [{ entity_id: "store-owned", action: "KEEP", expected_gross_margin: 100,
      budget_cost: 0, risk_score: 0.1, capacity_delta: 0, source_snapshot_ids: ["source-owned"], notes: ["canonical option"] }] },
    ...overrides };
}
function api(value = builder()) {
  return { read: vi.fn<NetPlanBuilderApi["read"]>().mockResolvedValue({ ok: true, builder: value }),
    command: vi.fn<NetPlanBuilderApi["command"]>().mockResolvedValue({ ok: true, builder: value, auditEventId: "audit-owned" }) };
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("canonical NetPlan scenario builder", () => {
  it("reads real constraints/options and never starts a mutation on load", async () => {
    const client = api(); const dirty = vi.fn();
    render(<NetPlanScenarioBuilder storeId="store-owned" api={client} onDirtyChange={dirty} />);
    expect(await screen.findByDisplayValue("1200")).toBeVisible();
    expect(screen.getByText("canonical option")).toBeVisible();
    expect(screen.getByText(/source-owned/)).toBeVisible();
    expect(client.command).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "儲存限制" })).toBeDisabled();
  });
  it("blocks the old solution immediately on edits, saves only assumptions, then permits explicit re-solve", async () => {
    const client = api(builder({ status: "solved" })); const dirty = vi.fn();
    client.command.mockResolvedValue({ ok: true, builder: builder({ problemHash: "b".repeat(64),
      constraints: { ...builder().constraints, max_budget: 800 } }), auditEventId: "audit-owned" });
    render(<NetPlanScenarioBuilder storeId="store-owned" api={client} onDirtyChange={dirty} />);
    await screen.findByDisplayValue("1200");
    fireEvent.change(screen.getByLabelText("預算上限"), { target: { value: "800" } });
    expect(dirty).toHaveBeenLastCalledWith(true);
    expect(screen.getByText(/舊解不可選取或送審/)).toBeVisible();
    fireEvent.change(screen.getByLabelText("變更／解算理由"), { target: { value: "Change approved planning assumptions" } });
    expect(screen.getByRole("button", { name: "重新解算" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "儲存限制" }));
    await waitFor(() => expect(client.command).toHaveBeenCalledTimes(1));
    const request = client.command.mock.calls[0];
    expect(request[1]).toBe("save");
    expect(request[5]).toEqual({ max_budget: 800, min_expected_gross_margin: null,
      min_capacity_delta: null, max_average_risk: 0.5, min_action_counts: {}, max_action_counts: {} });
    expect(request[5]).not.toHaveProperty("policy_version");
    await waitFor(() => expect(screen.getByLabelText("變更／解算理由")).toHaveValue(""));
    expect(screen.getByText(/audit-owned/)).toBeVisible();
    expect(screen.getByRole("button", { name: "重新解算" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("變更／解算理由"), { target: { value: "Explicit re-solve changed draft" } });
    fireEvent.click(screen.getByRole("button", { name: "重新解算" }));
    await waitFor(() => expect(client.command).toHaveBeenCalledTimes(2));
    expect(client.command.mock.calls[1][1]).toBe("solve");
  });
  it("keeps inputs and the same intent key after a refused command; locks while pending", async () => {
    const value = builder();
    let finish: (result: BuilderResult) => void = () => undefined;
    const command = vi.fn<NetPlanBuilderApi["command"]>().mockResolvedValue({ ok: false, message: "NetPlan 409 current version changed" });
    command.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const client: NetPlanBuilderApi = { read: async () => ({ ok: true, builder: value }), command };
    render(<NetPlanScenarioBuilder storeId="store-owned" api={client} onDirtyChange={vi.fn()} />);
    await screen.findByDisplayValue("1200");
    fireEvent.change(screen.getByLabelText("預算上限"), { target: { value: "900" } });
    fireEvent.change(screen.getByLabelText("變更／解算理由"), { target: { value: "Retain this reason after refusal" } });
    fireEvent.click(screen.getByRole("button", { name: "儲存限制" }));
    expect(screen.getByLabelText("預算上限")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "儲存限制" }));
    expect(command).toHaveBeenCalledTimes(1);
    await act(async () => finish({ ok: false, message: "NetPlan 409 current version changed" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("409");
    expect(screen.getByLabelText("預算上限")).toHaveValue("900");
    expect(screen.getByLabelText("變更／解算理由")).toHaveValue("Retain this reason after refusal");
    fireEvent.click(screen.getByRole("button", { name: "儲存限制" }));
    await waitFor(() => expect(command).toHaveBeenCalledTimes(2));
    expect(command.mock.calls[0][4]).toBe(command.mock.calls[1][4]);
  });
  it("shows options but offers no editing to a canonical read-only principal", async () => {
    render(<NetPlanScenarioBuilder storeId="store-owned" api={api(builder({ editable: false, canSolve: false }))} onDirtyChange={vi.fn()} />);
    await screen.findByDisplayValue("1200");
    expect(screen.getByLabelText("預算上限")).toBeDisabled();
    expect(screen.getByRole("button", { name: "儲存限制" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "重新解算" })).toBeDisabled();
    expect(screen.getByText("canonical option")).toBeVisible();
  });
  it("does not invent values when the scoped owner is unavailable", async () => {
    const client: NetPlanBuilderApi = { read: async () => ({ ok: false, message: "NetPlan 503" }), command: vi.fn() };
    render(<NetPlanScenarioBuilder storeId="store-owned" api={client} onDirtyChange={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("503");
    expect(screen.queryByLabelText("預算上限")).not.toBeInTheDocument();
    expect(client.command).not.toHaveBeenCalled();
  });
  it("refuses fractional counts and never sends a fake constraint", async () => {
    const client = api();
    render(<NetPlanScenarioBuilder storeId="store-owned" api={client} onDirtyChange={vi.fn()} />);
    await screen.findByDisplayValue("1200");
    fireEvent.change(screen.getByLabelText("MOVE max"), { target: { value: "1.5" } });
    fireEvent.change(screen.getByLabelText("變更／解算理由"), { target: { value: "Meaningful reason" } });
    fireEvent.click(screen.getByRole("button", { name: "儲存限制" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("非負整數");
    expect(client.command).not.toHaveBeenCalled();
  });
});

describe("builder transport keeps the authenticated read owner", () => {
  it("cannot issue a request without an authenticated session lease", async () => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    const client = createNetPlanBuilderApi({}, () => undefined, vi.fn());
    expect((await client.read("store-owned")).ok).toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("discards a stale success after JSON parsing without applying the old response", async () => {
    let active = true;
    const fetcher = vi.fn(async (_url: string, _init?: RequestInit) => ({ ok: true, status: 200, json: async () => { active = false; return builder(); } }));
    vi.stubGlobal("fetch", fetcher); const changed = vi.fn();
    const client = createNetPlanBuilderApi({}, () => async () => active, changed);
    expect(await client.read("store-owned")).toEqual({ ok: false, superseded: true });
    expect(changed).not.toHaveBeenCalled();
  });
  it("sends expected canonical hash and reason with no body actor/model/source authority", async () => {
    const fetcher = vi.fn(async (_url: string, _init?: RequestInit) => ({ ok: true, status: 200, json: async () => ({ builder: builder(), audit: { event_id: "actual-audit-id" } }) }));
    vi.stubGlobal("fetch", fetcher); const changed = vi.fn(async () => undefined);
    const client = createNetPlanBuilderApi({ "X-Operator-Role": "expansion-manager" }, () => async () => true, changed);
    expect((await client.command("store-owned", "solve", builder(), "Explicit solve", "same-intent")).ok).toBe(true);
    const init = fetcher.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({ scenarioId: "scenario-owned", expectedProblemHash: "a".repeat(64), reason: "Explicit solve" });
    expect(changed).toHaveBeenCalledTimes(1);
  });
  it.each([{}, { ...builder(), optionsByEntity: { "store-owned": [{}] } }, { ...builder(), problemHash: "unverified" }])(
    "does not use an incomplete canonical builder response", value => expect(isCanonicalBuilder(value)).toBe(false),
  );
});

describe("scenario builder affected entity disclosure", () => {
  it("shows every canonical affected ID before save and adopts only the returned scenario entity set", async () => {
    const first = builder();
    const option = first.optionsByEntity["store-owned"][0];
    const value = builder({ optionsByEntity: { ...first.optionsByEntity,
      "store-other-owned": [{ ...option, entity_id: "store-other-owned" }] } });
    const returned = builder({ problemHash: "c".repeat(64),
      constraints: { ...value.constraints, max_budget: 900 },
      optionsByEntity: { "store-returned-owned": [{ ...option, entity_id: "store-returned-owned" }] } });
    const client = api(value);
    client.command.mockResolvedValue({ ok: true, builder: returned, auditEventId: "audit-returned-owned" });
    render(<NetPlanScenarioBuilder storeId="store-owned" api={client} onDirtyChange={vi.fn()} />);
    const affected = await screen.findByRole("region", { name: "即將影響的實體" });
    expect(within(affected).getAllByRole("listitem").map(item => item.textContent)).toEqual(Object.keys(value.optionsByEntity));
    expect(client.command).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("預算上限"), { target: { value: "900" } });
    fireEvent.change(screen.getByLabelText("變更／解算理由"), { target: { value: "Update the scoped planning budget" } });
    expect(within(affected).getAllByRole("listitem").map(item => item.textContent)).toEqual(Object.keys(value.optionsByEntity));
    fireEvent.click(screen.getByRole("button", { name: "儲存限制" }));
    await waitFor(() => expect(client.command).toHaveBeenCalledTimes(1));
    expect(client.command.mock.calls[0][2]).toBe(value);
    expect(client.command.mock.calls[0][5]).not.toHaveProperty("optionsByEntity");
    await waitFor(() => expect(within(affected).getAllByRole("listitem").map(item => item.textContent)).toEqual(Object.keys(returned.optionsByEntity)));
    expect(within(affected).queryByText("store-owned")).not.toBeInTheDocument();
    expect(within(affected).queryByText("store-other-owned")).not.toBeInTheDocument();
  });
});
