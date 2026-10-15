import "@testing-library/jest-dom/vitest";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NetworkFindAreasWorkspace as GrantedReadNetworkWorkspace } from "../../NetworkFindAreasWorkspace";
import { candidateWorkflowNavigationParams, parseReviewSelection } from "../networkUrlState";

// Explicit read-grant fixture: shell projects listing and SiteScore read families.
// This is a renderer premise, not a real Principal/production permission grant.
const NETWORK_READ_FIXTURE_TABS = ["areas", "radar", "candidates", "score", "compare", "review"] as const;
function NetworkFindAreasWorkspace(props: import("../../NetworkFindAreasWorkspace").NetworkFindAreasWorkspaceProps) {
  return <GrantedReadNetworkWorkspace allowedTabIds={NETWORK_READ_FIXTURE_TABS} {...props} />;
}


const nav = vi.hoisted(() => ({ search: "", push: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/operator", useSearchParams: () => new URLSearchParams(nav.search), useRouter: () => ({ push: nav.push }) }));
vi.mock("../intake/AssistedIntakeSection", async (original) => ({ ...await original<typeof import("../intake/AssistedIntakeSection")>(), AssistedIntakeSection: () => <div /> }));
const root = resolve(process.cwd(), "../..");
const facts = JSON.parse(execFileSync(resolve(root, ".venv/bin/python"), [resolve(root, "tests/integration/test_candidate_native_workflow_readback.py")], {
  cwd: root, env: { ...process.env, PYTHONPATH: root, ODP_REQUIRE_LIVE_DATA: "false", ODP_PRODUCT_MODE: "poc", ODAY_ENV: "test" }, encoding: "utf8",
}));
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }
function prepare(name: string, tab = "candidates", reviewId?: string) {
  const value = facts[name];
  nav.search = `ws=network&tab=${tab}&candidate=${value.candidateId}${reviewId ? `&review=${encodeURIComponent(reviewId)}` : ""}`;
  window.history.replaceState(null, "", `/operator?${nav.search}`);
  const fetch = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => String(input).includes("network-listings") ? json(value.network)
    : String(input).includes("network-scoring") ? json(value.scoring) : String(input).includes("network-reviews") ? json(value.reviews) : json({}, 403));
  vi.stubGlobal("fetch", fetch);
  return { value, fetch };
}
beforeEach(() => { window.sessionStorage.clear(); nav.push.mockReset(); vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true"); });
afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("exact existing review read navigation", () => {
  it.each(["PENDING", "GO_V2", "OPENED_V3", "SECOND_PENDING"])("%s opens only the actual own decision URL without scoring", async (name) => {
    const { value, fetch } = prepare(name);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    const button = await screen.findByTestId(`candidate-view-review-${value.candidateId}`);
    expect(button).toBeEnabled(); fireEvent.click(button);
    await waitFor(() => expect(nav.push).toHaveBeenCalled());
    const url = new URL(nav.push.mock.calls.at(-1)![0], "https://synthetic.invalid");
    expect(url.searchParams.get("tab")).toBe("review");
    expect(url.searchParams.get("candidate")).toBe(value.candidateId);
    expect(url.searchParams.get("review")).toBe(value.reviewId);
    expect(fetch.mock.calls.every(([, init]) => !init || init.method !== "POST")).toBe(true);
  });
  it.each(["GO_V2", "OPENED_V3", "SECOND_HISTORY"])("cold %s history keeps the latest report expectation and the own V1 read", async (name) => {
    const { value, fetch } = prepare(name, "review", facts[name].reviewId);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId(`review-card-${value.reviewId}`);
    await waitFor(() => expect(screen.getByTestId(`review-card-${value.reviewId}`)).toHaveAttribute("data-active", "true"));
    expect(screen.getByTestId("review-historical-target-context")).toHaveTextContent("v1");
    expect(screen.getByTestId("review-historical-target-context")).toHaveTextContent(`v${value.latestReportVersion}`);
    for (const row of value.reviews.reviews) if (row.id !== value.reviewId) expect(screen.getByTestId(`review-card-${row.id}`)).toHaveAttribute("data-active", "false");
    expect(screen.queryByRole("button", { name: "GO 核准" })).not.toBeInTheDocument();
    expect(fetch.mock.calls.every(([, init]) => !init || init.method !== "POST")).toBe(true);
  });
  it("an invalid explicit target never falls back to a valid current review", async () => {
    const { value } = prepare("PENDING", "review", "unrelated-review");
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId(`review-card-${value.reviewId}`);
    expect(screen.getByTestId(`review-card-${value.reviewId}`)).toHaveAttribute("data-active", "false");
    expect(await screen.findByTestId("review-explicit-target-unavailable")).toBeVisible();
  });
  it("an actual other candidate's decision URL never selects that foreign review or falls back to the own pending review", async () => {
    const value = facts.SECOND_PENDING, foreignId = value.reviews.reviews[0].id;
    expect(foreignId).not.toBe(value.reviewId); prepare("SECOND_PENDING", "review", foreignId);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId(`review-card-${value.reviewId}`);
    for (const row of value.reviews.reviews) expect(screen.getByTestId(`review-card-${row.id}`)).toHaveAttribute("data-active", "false");
    expect(screen.getByTestId("review-explicit-target-unavailable")).toBeVisible();
  });
  it.each(["&review=", "&review=one&review=two"])("rejects malformed explicit target %s without selecting the pending row", async (suffix) => {
    const { value } = prepare("PENDING", "review"); nav.search += suffix;
    window.history.replaceState(null, "", `/operator?${nav.search}`);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId(`review-card-${value.reviewId}`);
    expect(screen.getByTestId(`review-card-${value.reviewId}`)).toHaveAttribute("data-active", "false");
    expect(screen.getByTestId("review-explicit-target-unavailable")).toBeVisible();
  });
  it("Back/Forward revalidates each explicit target and never carries the previous selection", async () => {
    const { value, fetch } = prepare("GO_V2", "review", facts.GO_V2.reviewId);
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(screen.getByTestId(`review-card-${value.reviewId}`)).toHaveAttribute("data-active", "true"));
    nav.search = `ws=network&tab=review&candidate=${value.candidateId}&review=foreign`;
    window.history.replaceState(null, "", `/operator?${nav.search}`); view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    expect(screen.getByTestId(`review-card-${value.reviewId}`)).toHaveAttribute("data-active", "false");
    expect(screen.getByTestId("review-explicit-target-unavailable")).toBeVisible();
    nav.search = `ws=network&tab=review&candidate=${value.candidateId}&review=${value.reviewId}`;
    window.history.replaceState(null, "", `/operator?${nav.search}`); view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    expect(screen.getByTestId(`review-card-${value.reviewId}`)).toHaveAttribute("data-active", "true");
    expect(fetch.mock.calls.every(([, init]) => !init || init.method !== "POST")).toBe(true);
  });
  it("a new role's denied review read removes the old stage and navigation immediately", async () => {
    const value = facts.GO_V2; prepare("GO_V2"); let calls = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).includes("network-listings") ? json(value.network)
      : String(input).includes("network-scoring") ? json(value.scoring) : String(input).includes("network-reviews")
        ? ++calls === 1 ? json(value.reviews) : json({ detail: "new role denied" }, 403) : json({}, 403)));
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId(`candidate-view-review-${value.candidateId}`);
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-staff" />);
    expect(screen.queryByTestId(`candidate-view-review-${value.candidateId}`)).not.toBeInTheDocument();
    await waitFor(() => expect(calls).toBe(2));
    await screen.findByTestId(`candidate-row-${value.candidateId}`);
    expect(screen.getByTestId("candidate-pipeline-approved")).toBeDisabled();
  });
  it("late JSON from an abandoned actor/tenant generation cannot restore its historical target", async () => {
    const value = facts.GO_V2; prepare("GO_V2"); let calls = 0, finish!: (value: unknown) => void;
    const delayed = { ok: true, json: () => new Promise<unknown>((resolve) => { finish = resolve; }) } as Response;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).includes("network-listings") ? json(value.network)
      : String(input).includes("network-scoring") ? json(value.scoring) : String(input).includes("network-reviews")
        ? ++calls === 1 ? delayed : json({ detail: "new tenant denied" }, 403) : json({}, 403)));
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(finish).toBeTypeOf("function"));
    window.sessionStorage.setItem("oday.operator.tenant", "foreign-tenant");
    window.sessionStorage.setItem("oday.operator.subject", "foreign-actor");
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(calls).toBe(2));
    await act(async () => finish(value.reviews));
    expect(screen.queryByTestId(`candidate-view-review-${value.candidateId}`)).not.toBeInTheDocument();
    expect(screen.getByTestId("candidate-pipeline-approved")).toBeDisabled();
  });
  it("unmount discards a delayed review body without a navigation or mutation", async () => {
    const value = facts.GO_V2; prepare("GO_V2"); let finish!: (value: unknown) => void;
    const fetch = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => String(input).includes("network-listings") ? json(value.network)
      : String(input).includes("network-scoring") ? json(value.scoring) : String(input).includes("network-reviews")
        ? { ok: true, json: () => new Promise<unknown>((resolve) => { finish = resolve; }) } as Response : json({}, 403));
    vi.stubGlobal("fetch", fetch); const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(finish).toBeTypeOf("function")); view.unmount();
    await act(async () => finish(value.reviews)); expect(nav.push).not.toHaveBeenCalled();
    expect(fetch.mock.calls.every(([, init]) => !init || init.method !== "POST")).toBe(true);
  });
  it("clears the review owner when the candidate/tab changes and rejects conflicting URL values", () => {
    const original = new URLSearchParams("tab=review&candidate=one&review=own-one");
    expect(candidateWorkflowNavigationParams(2, original, "one").has("review")).toBe(false);
    expect(candidateWorkflowNavigationParams(5, original, "one", { candidate: "two" }).has("review")).toBe(false);
    expect(candidateWorkflowNavigationParams(0, original, "one").has("review")).toBe(false);
    expect(parseReviewSelection(new URLSearchParams("review=one&review=two"))).toEqual({ id: null, invalid: true });
    expect(parseReviewSelection(new URLSearchParams("review=one&review=one"))).toEqual({ id: "one", invalid: false });
  });
});
