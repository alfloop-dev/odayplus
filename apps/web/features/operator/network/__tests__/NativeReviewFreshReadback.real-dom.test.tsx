import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NetworkFindAreasWorkspace } from "../../NetworkFindAreasWorkspace";
import samples from "./fixtures/canonicalReviewDecision.json";
import freshListings from "./fixtures/canonicalReviewDecisionFreshListings.json";

const nav = vi.hoisted(() => ({ search: "", push: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/operator", useSearchParams: () => new URLSearchParams(nav.search), useRouter: () => ({ push: nav.push }) }));
vi.mock("../HeatZoneMap", () => ({ HeatZoneMap: () => null }));
vi.mock("../intake/AssistedIntakeSection", async (original) => ({ ...await original<typeof import("../intake/AssistedIntakeSection")>(), AssistedIntakeSection: () => <div /> }));
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
function deferred() { let resolve!: () => void; const promise = new Promise<void>((done) => { resolve = done; }); return { promise, resolve }; }
type Phase = "listings-http" | "listings-json" | "reviews-http" | "reviews-json";
// These controls execute the real workspace transport and DOM with declared
// native producer samples. Mocked HTTP200 is not claimed as native acceptance.
function mount(options: { returned?: boolean; hold?: Phase; listingsFailure?: boolean; staleListings?: boolean;
  wrongTenant?: boolean; wrongVersion?: boolean; firstUnknown?: boolean; noStorageTenant?: boolean } = {}) {
  const name = options.returned ? "RETURN_RESUBMITTED_GO" : "GO";
  const sample = samples[name];
  nav.search = `ws=network&tab=review&candidate=${sample.candidateId}&review=${sample.decisionId}`;
  window.history.replaceState(null, "", `/operator?${nav.search}`);
  if (!options.noStorageTenant) window.sessionStorage.setItem("oday.operator.tenant", sample.context.tenantId);
  vi.spyOn(crypto, "randomUUID").mockReturnValueOnce(sample.idempotencyKey as ReturnType<Crypto["randomUUID"]>)
    .mockReturnValueOnce(sample.correlationId as ReturnType<Crypto["randomUUID"]>);
  let subject = sample.context.actorSubjectId, accepted = false, posts = 0, held = false;
  const gate = deferred(), events: string[] = [];
  const transport = async (body: unknown, phase: "listings" | "reviews", status = 200) => {
    events.push(`${phase}-http`);
    if (options.hold === `${phase}-http`) { held = true; await gate.promise; }
    const response = json(body, status), read = response.json.bind(response);
    response.json = async () => {
      if (options.hold === `${phase}-json`) { held = true; await gate.promise; }
      const value = await read(); events.push(`${phase}-json`); return value;
    };
    return response;
  };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/auth/session") return json({ subject, expiresAt: Math.floor(Date.now() / 1000) + 3600 });
    if (url === "/auth/operator-context") return json({ actorSubjectId: subject, tenantId: sample.context.tenantId, expiresAt: Math.floor(Date.now() / 1000) + 3600 });
    if (url.includes("/decision-context?")) return json(sample.context);
    if (url.endsWith("/decision") && init?.method === "POST") {
      posts += 1; events.push("post");
      if (options.firstUnknown && posts === 1) throw new TypeError("Declared unknown transport outcome");
      accepted = true; return json(posts > 1 ? { ...sample.receipt, idempotentReplay: true } : sample.receipt);
    }
    if (url.includes("network-listings")) {
      if (!accepted) return json(sample.network);
      const network = structuredClone(options.staleListings ? sample.network : freshListings[name]);
      if (options.wrongTenant) network.listings[0].tenantId = "other-native-tenant-same-account";
      if (options.wrongVersion && "reviewWorkflow" in network.candidates[0]) network.candidates[0].reviewWorkflow.decisionVersion += 1;
      return transport(network, "listings", options.listingsFailure ? 503 : 200);
    }
    if (url.includes("network-reviews")) return accepted ? transport(sample.after, "reviews") : json(sample.before);
    if (url.includes("network-scoring")) return json(sample.scoring);
    if (url.includes("network-rebalance")) return json({ source: "api", stores: [] });
    if (url.includes("heatzone-proposals")) return json({ source: "api", proposals: [] });
    return json({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  const confirmed = vi.fn(() => { events.push("confirmed"); });
  const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" initialTabId="review" onReviewDecisionConfirmed={confirmed} />);
  return { sample, confirmed, fetchMock, events, view, gate, held: () => held,
    rotateActor: () => { subject = "other-authenticated-account"; },
    rotateTenant: () => { window.sessionStorage.setItem("oday.operator.tenant", "other-tenant-same-account"); },
    writes: () => fetchMock.mock.calls.filter(([, init]) => init?.method === "POST") };
}
async function submit(sample: { request: { reason: string } }) {
  await waitFor(() => expect(screen.getByRole("button", { name: "核准 GO" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "核准 GO" }));
  await screen.findByTestId("review-decision-reason");
  fireEvent.change(screen.getByTestId("review-decision-reason"), { target: { value: sample.request.reason } });
  fireEvent.click(screen.getByTestId("review-decision-submit"));
}
beforeEach(() => { window.sessionStorage.clear(); vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true"); nav.push.mockReset(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.restoreAllMocks(); });

describe("current native decision needs coherent fresh listings and reviews", () => {
  it.each([false, true])("reads real listings GET and reviews GET before one Today callback; frozenV1 latestV2=%s", async (returned) => {
    const run = mount({ returned }); await submit(run.sample);
    await waitFor(() => expect(run.confirmed).toHaveBeenCalledTimes(1));
    expect(run.events).toEqual(["post", "listings-http", "listings-json", "reviews-http", "reviews-json", "confirmed"]);
    expect(run.writes()).toHaveLength(1);
    expect(JSON.parse(run.writes()[0][1]!.body as string)).toEqual(run.sample.request);
    expect(run.writes()[0][1]!.headers).toMatchObject({ "Idempotency-Key": run.sample.idempotencyKey, "X-Correlation-Id": run.sample.correlationId });
    expect(screen.queryByTestId("review-decision-dialog")).not.toBeInTheDocument();
    expect(run.sample.after.decisions[0].history.length).toBeGreaterThan(run.sample.before.decisions[0].history.length);
  });
  it.each(["listingsFailure", "staleListings", "wrongVersion", "wrongTenant"] as const)("POST200 followed by %s never confirms", async (fault) => {
    const run = mount({ [fault]: true }); await submit(run.sample);
    await screen.findByTestId("review-decision-error");
    expect(run.confirmed).not.toHaveBeenCalled(); expect(run.writes()).toHaveLength(1);
    expect(screen.getByTestId("review-decision-reason")).toHaveValue(run.sample.request.reason);
  });
  it("same authenticated account and absent storage tenant still require actual listing tenant", async () => {
    const run = mount({ wrongTenant: true, noStorageTenant: true }); await submit(run.sample);
    await screen.findByTestId("review-decision-error"); expect(run.confirmed).not.toHaveBeenCalled();
    expect(window.sessionStorage.getItem("oday.operator.tenant")).toBeNull(); expect(run.writes()).toHaveLength(1);
  });
  it.each(["listings-http", "listings-json", "reviews-http", "reviews-json"] as const)("waits for %s and refuses late actor or tenant replacement", async (hold) => {
    for (const rotation of ["actor", "tenant"] as const) {
      const run = mount({ hold }); await submit(run.sample); await waitFor(() => expect(run.held()).toBe(true));
      expect(run.confirmed).not.toHaveBeenCalled();
      if (rotation === "actor") run.rotateActor(); else run.rotateTenant();
      run.gate.resolve();
      await waitFor(() => expect(screen.queryByTestId(`review-card-${run.sample.decisionId}`)).not.toBeInTheDocument());
      expect(run.confirmed).not.toHaveBeenCalled(); expect(run.writes()).toHaveLength(1);
      expect(screen.queryByTestId("review-decision-dialog")).not.toBeInTheDocument();
      cleanup(); vi.restoreAllMocks();
    }
  });
  it("reuses original immutable unknown request after form change is refused, then confirms both fresh reads", async () => {
    const run = mount({ firstUnknown: true }); await submit(run.sample); await screen.findByTestId("review-decision-error");
    const first = run.writes()[0][1]!;
    fireEvent.change(screen.getByTestId("review-decision-reason"), { target: { value: "Different form cannot replace original" } });
    fireEvent.click(screen.getByTestId("review-decision-submit"));
    await waitFor(() => expect(screen.getByTestId("review-decision-error")).toHaveTextContent("原核決結果尚未確認"));
    expect(run.writes()).toHaveLength(1);
    fireEvent.change(screen.getByTestId("review-decision-reason"), { target: { value: run.sample.request.reason } });
    fireEvent.click(screen.getByTestId("review-decision-submit"));
    await waitFor(() => expect(run.confirmed).toHaveBeenCalledTimes(1));
    expect(run.writes()).toHaveLength(2);
    expect(run.writes()[1][1]!.body).toBe(first.body); expect(run.writes()[1][1]!.headers).toEqual(first.headers);
  });
});
