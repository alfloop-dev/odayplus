import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NetworkFindAreasWorkspace } from "../../NetworkFindAreasWorkspace";
import { ReviewPanel } from "../ReviewPanel";
import type { ReviewItem } from "../networkReviewTypes";
import samples from "./fixtures/canonicalReviewDecision.json";
import freshListings from "./fixtures/canonicalReviewDecisionFreshListings.json";

const navigation = vi.hoisted(() => ({ search: "", push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/operator",
  useSearchParams: () => new URLSearchParams(navigation.search),
  useRouter: () => ({ push: navigation.push }),
}));
vi.mock("../HeatZoneMap", () => ({ HeatZoneMap: () => null }));
vi.mock("../intake/AssistedIntakeSection", async (original) => ({
  ...await original<typeof import("../intake/AssistedIntakeSection")>(),
  AssistedIntakeSection: () => <div />,
}));

type Phase = "decision-http" | "decision-json" | "listings-http" | "listings-json" | "reviews-http" | "reviews-json";
const phases: Phase[] = ["decision-http", "decision-json", "listings-http", "listings-json", "reviews-http", "reviews-json"];
const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
function selectRoute(sample: { candidateId: string; decisionId: string }) {
  navigation.search = `ws=network&tab=review&candidate=${sample.candidateId}&review=${sample.decisionId}`;
  window.history.replaceState(null, "", `/operator?${navigation.search}`);
}

// Declared mock HTTP / happy-dom controls execute the real workspace transport
// and ReviewPanel. They do not establish a B cookie, native Principal, accepted
// business write, production grants, or a real browser. Original producer JSON
// remains unchanged. All scope changes below come from /auth/operator-context;
// the session adapter and absent storage tenant cannot supply that authority.
function mount(options: { hold?: Phase; firstUnknown?: boolean } = {}) {
  const c = samples.GO, b = samples.RETURN_RESUBMITTED_GO;
  selectRoute(c);
  let actor = c.context.actorSubjectId, tenant = c.context.tenantId;
  let expiresAt = Math.floor(Date.now() / 1000) + 3600;
  let accepted = false, posts = 0, held = false, released = false, currentView: "C" | "B" = "C";
  const gate = deferred<void>(), events: string[] = [];
  const identityReads: { actorSubjectId: string; tenantId: string; expiresAt: number }[] = [];
  vi.spyOn(crypto, "randomUUID")
    .mockReturnValueOnce(c.idempotencyKey as ReturnType<Crypto["randomUUID"]>)
    .mockReturnValueOnce(c.correlationId as ReturnType<Crypto["randomUUID"]>);
  const transport = async (body: unknown, stage: "decision" | "listings" | "reviews") => {
    events.push(`${stage}-http`);
    if (options.hold === `${stage}-http`) { held = true; await gate.promise; released = true; }
    const response = json(body), read = response.json.bind(response);
    response.json = async () => {
      if (options.hold === `${stage}-json`) { held = true; await gate.promise; released = true; }
      const value = await read(); events.push(`${stage}-json`); return value;
    };
    return response;
  };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/auth/session") return json({ subject: c.context.actorSubjectId, expiresAt });
    if (url === "/auth/operator-context") {
      const identity = { actorSubjectId: actor, tenantId: tenant, expiresAt };
      identityReads.push(identity); return json(identity);
    }
    if (url.includes("/decision-context?")) return json(currentView === "B" ? b.context : c.context);
    if (url.endsWith("/decision") && init?.method === "POST") {
      posts += 1;
      if (options.firstUnknown && posts === 1) throw new TypeError("Declared unknown C transport outcome");
      accepted = true;
      return transport(posts > 1 ? { ...c.receipt, idempotentReplay: true } : c.receipt, "decision");
    }
    if (url.includes("network-listings")) {
      if (currentView === "B") return json(b.network);
      return accepted ? transport(freshListings.GO, "listings") : json(c.network);
    }
    if (url.includes("network-reviews")) {
      if (currentView === "B") return json(b.before);
      return accepted ? transport(c.after, "reviews") : json(c.before);
    }
    if (url.includes("network-scoring")) return json(currentView === "B" ? b.scoring : c.scoring);
    if (url.includes("network-rebalance")) return json({ source: "api", stores: [] });
    if (url.includes("heatzone-proposals")) return json({ source: "api", proposals: [] });
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  const confirmed = vi.fn();
  const element = () => <NetworkFindAreasWorkspace activeRoleId="expansion-manager" initialTabId="review" onReviewDecisionConfirmed={confirmed} />;
  const view = render(element());
  return {
    c, b, gate, view, confirmed, events, identityReads, held: () => held, released: () => released,
    changeTrustedActor: () => { actor = "declared-other-authenticated-account"; },
    changeTrustedTenant: () => { tenant = "declared-foreign-tenant-same-authenticated-account"; },
    renewExpiry: () => { expiresAt += 3600; },
    // Explicit observed-context + route replacement, not the hidden-cookie
    // same-navigation three-frame scenario. Both samples use the same tenant.
    replacePublicRouteWithB: () => {
      currentView = "B"; selectRoute(b);
      window.sessionStorage.setItem("oday.operator.tenant", b.context.tenantId);
      view.rerender(element());
    },
    writes: () => fetchMock.mock.calls.filter(([, init]) => init?.method === "POST"),
  };
}
async function openDecision(reviewId: string) {
  await waitFor(() => expect(screen.getByTestId(`review-btn-go-${reviewId}`)).toBeEnabled());
  fireEvent.click(screen.getByTestId(`review-btn-go-${reviewId}`));
  await screen.findByTestId("review-decision-reason");
}
async function submitC(run: ReturnType<typeof mount>) {
  await openDecision(run.c.decisionId);
  fireEvent.change(screen.getByTestId("review-decision-reason"), { target: { value: run.c.request.reason } });
  fireEvent.click(screen.getByTestId("review-decision-submit"));
}
beforeEach(() => {
  window.sessionStorage.clear(); navigation.push.mockReset();
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.restoreAllMocks(); });

describe("old native review request owns only its original trusted scope", () => {
  it.each(phases.flatMap((phase) => [
    [phase, "actor"] as const, [phase, "tenant"] as const,
  ]))("discards old C %s completion after trusted %s changes with no storage tenant", async (hold, rotation) => {
    const run = mount({ hold }); await submitC(run);
    await waitFor(() => expect(run.held()).toBe(true));
    expect(window.sessionStorage.getItem("oday.operator.tenant")).toBeNull();
    expect(screen.getByTestId("review-decision-reason")).toHaveValue(run.c.request.reason);
    const readsBeforeReplacement = run.identityReads.length;
    if (rotation === "actor") run.changeTrustedActor(); else run.changeTrustedTenant();
    await act(async () => { run.gate.resolve(); });
    await waitFor(() => expect(screen.queryByTestId("review-decision-dialog")).not.toBeInTheDocument());
    expect(run.identityReads.length).toBeGreaterThan(readsBeforeReplacement);
    const replacement = run.identityReads[run.identityReads.length - 1];
    expect(rotation === "actor" ? replacement.actorSubjectId : replacement.tenantId)
      .not.toBe(rotation === "actor" ? run.c.context.actorSubjectId : run.c.context.tenantId);
    if (rotation === "tenant") expect(replacement.actorSubjectId).toBe(run.c.context.actorSubjectId);
    expect(screen.queryByTestId("review-decision-reason")).not.toBeInTheDocument();
    expect(screen.queryByTestId(`review-card-${run.c.decisionId}`)).not.toBeInTheDocument();
    expect(run.confirmed).not.toHaveBeenCalled(); expect(run.writes()).toHaveLength(1);
    expect(JSON.parse(run.writes()[0][1]!.body as string)).toEqual(run.c.request);
    expect(window.sessionStorage.getItem("oday.operator.tenant")).toBeNull();
  });

  it("accepts expiry renewal for the same trusted actor / tenant and replays the exact unknown request", async () => {
    const run = mount({ firstUnknown: true }); await submitC(run);
    await screen.findByTestId("review-decision-error");
    expect(run.confirmed).not.toHaveBeenCalled(); expect(run.writes()).toHaveLength(1);
    const first = run.writes()[0][1]!, firstExpiry = run.identityReads[0].expiresAt;
    run.renewExpiry();
    fireEvent.click(screen.getByTestId("review-decision-submit"));
    await waitFor(() => expect(run.confirmed).toHaveBeenCalledTimes(1));
    expect(run.writes()).toHaveLength(2);
    expect(run.writes()[1][1]!.body).toBe(first.body);
    expect(run.writes()[1][1]!.headers).toEqual(first.headers);
    expect(run.identityReads[run.identityReads.length - 1]).toEqual({
      actorSubjectId: run.c.context.actorSubjectId, tenantId: run.c.context.tenantId, expiresAt: firstExpiry + 3600,
    });
    expect(window.sessionStorage.getItem("oday.operator.tenant")).toBeNull();
    expect(screen.queryByTestId("review-decision-dialog")).not.toBeInTheDocument();
  });

  it.each(["listings-http", "listings-json", "reviews-http", "reviews-json"] as const)(
    "public observed-context / route B retains its data and dialog reason after old C %s completion", async (hold) => {
      const run = mount({ hold }); await submitC(run);
      await waitFor(() => expect(run.held()).toBe(true));
      run.replacePublicRouteWithB();
      await openDecision(run.b.decisionId);
      expect(screen.getByTestId("review-decision-reason")).toHaveValue("");
      const bReason = "Declared new B intent remains owned by the replacement panel";
      fireEvent.change(screen.getByTestId("review-decision-reason"), { target: { value: bReason } });
      expect(screen.queryByTestId(`review-card-${run.c.decisionId}`)).not.toBeInTheDocument();
      await act(async () => { run.gate.resolve(); });
      await waitFor(() => expect(run.released()).toBe(true));
      expect(screen.getByTestId(`review-card-${run.b.decisionId}`)).toBeInTheDocument();
      expect(screen.getByTestId("review-decision-dialog")).toBeInTheDocument();
      expect(screen.getByTestId("review-decision-reason")).toHaveValue(bReason);
      expect(screen.getByTestId("review-decision-submit")).toBeEnabled();
      expect(run.confirmed).not.toHaveBeenCalled(); expect(run.writes()).toHaveLength(1);
    },
  );
});

describe("real ReviewPanel remount isolates pending C callbacks from new B intent", () => {
  it.each(["request", "submit"] as const)("old C %s callback cannot reopen or close the replacement B dialog", async (phase) => {
    const c = samples.GO, b = samples.RETURN_RESUBMITTED_GO, oldCompletion = deferred<boolean>();
    const cRequest = vi.fn(() => phase === "request" ? oldCompletion.promise : true);
    const cDecide = vi.fn(() => oldCompletion.promise);
    const bRequest = vi.fn(() => true), bDecide = vi.fn(() => false);
    const view = render(<ReviewPanel key="declared-C-owned-intent" reviews={c.before.reviews as ReviewItem[]} fallbackRows={[]}
      onRequestDecision={cRequest} onDecide={cDecide} />);
    fireEvent.click(screen.getByTestId(`review-btn-go-${c.decisionId}`));
    await waitFor(() => expect(cRequest).toHaveBeenCalledTimes(1));
    if (phase === "submit") {
      await screen.findByTestId("review-decision-reason");
      fireEvent.change(screen.getByTestId("review-decision-reason"), { target: { value: "Declared old C decision reason" } });
      fireEvent.click(screen.getByTestId("review-decision-submit"));
      await waitFor(() => expect(cDecide).toHaveBeenCalledTimes(1));
    }
    view.rerender(<ReviewPanel key="declared-B-owned-intent" reviews={b.before.reviews as ReviewItem[]} fallbackRows={[]}
      onRequestDecision={bRequest} onDecide={bDecide} />);
    expect(screen.queryByTestId("review-decision-dialog")).not.toBeInTheDocument();
    await openDecision(b.decisionId);
    expect(screen.getByTestId("review-decision-reason")).toHaveValue("");
    const bReason = "Declared new B reason survives the old C callback";
    fireEvent.change(screen.getByTestId("review-decision-reason"), { target: { value: bReason } });
    await act(async () => { oldCompletion.resolve(true); });
    expect(screen.getByTestId(`review-card-${b.decisionId}`)).toBeInTheDocument();
    expect(screen.queryByTestId(`review-card-${c.decisionId}`)).not.toBeInTheDocument();
    expect(screen.getByTestId("review-decision-reason")).toHaveValue(bReason);
    expect(bRequest).toHaveBeenCalledTimes(1);
    expect(bRequest).toHaveBeenCalledWith(b.decisionId, "GO");
    expect(bDecide).not.toHaveBeenCalled();
    expect(cDecide).toHaveBeenCalledTimes(phase === "submit" ? 1 : 0);
  });
});

// Material coverage limit: no public DOM trigger renews listings, scoring and
// reviews together while keeping the SAME navigation / observed context. The
// B-route tests above exercise declared public replacement; they do not claim
// the hidden-cookie, same-navigation three-frame counterexample is reachable.
// The parent separately reviews that case using the whole actual caller source.
