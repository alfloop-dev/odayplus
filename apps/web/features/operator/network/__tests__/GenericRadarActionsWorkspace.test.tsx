import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NetworkFindAreasWorkspace } from "../../NetworkFindAreasWorkspace";
import { buildNetworkFindAreasViewModel } from "../../networkFindAreasViewModel";
import type { Listing } from "../../types";
import { ListingRadarPanel } from "../ListingRadarPanel";
import { buildMergeRiskSummary } from "../ListingMergeDialog";

vi.mock("../intake/AssistedIntakeSection", () => ({ AssistedIntakeSection: () => <div /> }));
const nav = vi.hoisted(() => ({ pathname: "/operator", search: "ws=network&tab=listings", push: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(nav.search), useRouter: () => ({ push: nav.push }) }));
beforeEach(() => {
  nav.pathname = "/operator"; nav.search = "ws=network&tab=listings"; nav.push.mockReset();
  window.history.replaceState(null, "", `/operator?${nav.search}`); window.sessionStorage.clear();
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
const source: Listing = { id: "source-uuid", sourceId: "source", heatZoneId: "HZ-01", address: "Recorded source",
  status: "duplicate", rentPerMonth: 30000, areaPing: 25, geocodeConfidence: 0.9, hardRuleFailures: [], duplicateOfId: "target-uuid" };
const target: Listing = { ...source, id: "target-uuid", status: "new", duplicateOfId: undefined };
const archived: Listing = { ...source, id: "archive-uuid", status: "hardfail", duplicateOfId: undefined,
  hardRuleFailures: ["recorded_failure"] };

describe("Generic recorded Radar actions", () => {
  it("opens a generic explicit duplicate with exact source and target IDs", () => {
    const onMerge = vi.fn();
    const view = buildNetworkFindAreasViewModel({ heatZones: [], candidates: [], listings: [source, target] });
    render(<ListingRadarPanel activeRoleId="expansion-manager" listings={[source, target]} rows={view.listingRadar}
      sources={[]} onMerge={onMerge} writeContext={{ ready: true, fixturesAllowed: true, candidates: [] }} />);
    fireEvent.click(screen.getByTestId(`merge-${source.id}`));
    expect(onMerge).toHaveBeenCalledWith(source.id, target.id);
  });
  it("opens archive confirmation for a generic measured failing record", () => {
    const onArchive = vi.fn();
    const view = buildNetworkFindAreasViewModel({ heatZones: [], candidates: [], listings: [archived] });
    render(<ListingRadarPanel activeRoleId="expansion-manager" listings={[archived]} rows={view.listingRadar}
      sources={[]} onArchive={onArchive} writeContext={{ ready: true, fixturesAllowed: true, candidates: [] }} />);
    fireEvent.click(screen.getByTestId(`archive-${archived.id}`));
    expect(onArchive).toHaveBeenCalledWith(archived.id);
  });
});

function snapshot() {
  return { source: "api", heatZones: [], listingSources: [], candidates: [], siteReviews: [], assistedIntakes: [], expansionSteps: [],
    listings: [{ ...source, version: 7, hasLegalHold: false }, { ...target, version: 9, hasLegalHold: false },
      { ...archived, version: 13, hasLegalHold: false }] };
}
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status }); }
const CURRENT_ACTOR = "00000000-0000-0000-0000-000000000101";
function mergeReceipt(reason: string, idempotencyKey: string) {
  return { source: { ...source, version: 7, hasLegalHold: false, mergedIntoId: target.id, mergeReason: reason },
    target: { ...target, version: 9, hasLegalHold: false }, actorSubjectId: CURRENT_ACTOR, actorRoleId: "site_reviewer", idempotencyKey,
    auditEvent: { id: "persisted-audit", action: "listing.merge", targetId: target.id, actorName: CURRENT_ACTOR, actorRoleId: "site_reviewer",
      metadata: { reason, sourceListingId: source.id, targetListingId: target.id, riskSummary: buildMergeRiskSummary(source.id, target.id),
        riskAcknowledged: true, before: { sourceVersion: 7, targetVersion: 9 }, after: { sourceVersion: 7, targetVersion: 9 } } } };
}
function wire(read?: () => Response | Promise<Response>, post?: (init: RequestInit) => Response | Promise<Response>, session?: () => Response | Promise<Response>) {
  const mock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => init?.method === "POST"
    ? post ? post(init) : json({ listing: { ...archived, version: 13, status: "archived", archivedReason: JSON.parse(String(init.body)).reason },
      actorSubjectId: CURRENT_ACTOR, actorRoleId: "site_reviewer", idempotencyKey: new Headers(init.headers).get("idempotency-key"),
      auditEvent: { id: "actual-synthetic-audit", action: "listing.archive", targetId: archived.id,
        actorName: CURRENT_ACTOR, actorRoleId: "site_reviewer", metadata: { reason: JSON.parse(String(init.body)).reason,
          before: { version: 13 }, after: { version: 13 } } }, correlationId: "actual-test-receipt" })
    : String(url) === "/auth/session" ? session ? session() : json({ subject: CURRENT_ACTOR, expiresAt: 2000000000 })
      : String(url).includes("network-listings?") ? read ? read() : json(snapshot()) : json({ detail: "Not supplied by test" }, 403));
  vi.stubGlobal("fetch", mock); return mock;
}
function writes(mock: ReturnType<typeof wire>) { return mock.mock.calls.filter(([, init]) => init?.method === "POST"); }
async function openMerge() {
  fireEvent.click(await screen.findByTestId(`merge-${source.id}`));
  fireEvent.change(screen.getByTestId("listing-merge-reason"), { target: { value: "Recorded relation independently verified" } });
  fireEvent.click(screen.getByTestId("listing-merge-risk-ack"));
}

describe("actual Workspace confirmation write lifetime", () => {
  it("preserves explicit nonproduction fixture API merge and archive confirmation without inventing versions", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const legacy = { ...snapshot(), source: "fixture", listings: snapshot().listings.map((row) => {
      const { version: _version, hasLegalHold: _hold, ...record } = row; return record;
    }) };
    const reason = "Actual local fixture operator reason";
    const mock = wire(() => json(legacy), (init) => json({ listing: { ...legacy.listings[2], status: "archived", archivedReason: reason },
      actorSubjectId: CURRENT_ACTOR, actorRoleId: "site_reviewer", idempotencyKey: new Headers(init.headers).get("idempotency-key"), auditEvent: { id: "actual-fixture-audit", action: "listing.archive",
        targetId: archived.id, actorName: CURRENT_ACTOR, actorRoleId: "site_reviewer", metadata: { reason, before: { version: null }, after: { version: null } } } }));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    fireEvent.click(await screen.findByTestId(`merge-${source.id}`));
    expect(screen.getByTestId("listing-merge-dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("listing-merge-cancel"));
    fireEvent.click(screen.getByTestId(`archive-${archived.id}`));
    expect(screen.getByTestId("listing-archive-dialog")).toBeInTheDocument();
    expect(writes(mock)).toHaveLength(0);
    fireEvent.change(screen.getByTestId("listing-archive-reason"), { target: { value: "Actual local fixture operator reason" } });
    fireEvent.click(screen.getByTestId("listing-archive-submit"));
    await waitFor(() => expect(writes(mock)).toHaveLength(1));
    expect(new Headers(writes(mock)[0][1]?.headers).get("if-match")).toBeNull();
    expect(JSON.parse(String(writes(mock)[0][1]?.body)).reason).toBe("Actual local fixture operator reason");
    await waitFor(() => expect(screen.queryByTestId("listing-archive-dialog")).not.toBeInTheDocument());
  });
  it("archive open/cancel writes nothing; confirms exact entered reason and actual revision", async () => {
    const mock = wire(); render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    fireEvent.click(await screen.findByTestId(`archive-${archived.id}`));
    expect(screen.getByTestId("listing-archive-reason")).toHaveValue(""); expect(writes(mock)).toHaveLength(0);
    fireEvent.click(screen.getByTestId("listing-archive-cancel")); expect(writes(mock)).toHaveLength(0);
    fireEvent.click(screen.getByTestId(`archive-${archived.id}`));
    fireEvent.change(screen.getByTestId("listing-archive-reason"), { target: { value: "Actual entered archive reason" } });
    fireEvent.click(screen.getByTestId("listing-archive-submit"));
    await waitFor(() => expect(writes(mock)).toHaveLength(1));
    expect(JSON.parse(String(writes(mock)[0][1]?.body))).toMatchObject({ reason: "Actual entered archive reason" });
    expect(writes(mock)[0][1]?.headers).toMatchObject({ "If-Match": 'W/"13"' });
    expect(writes(mock)[0][1]?.headers).toMatchObject({ "If-Actor-Match": CURRENT_ACTOR });
    await waitFor(() => expect(screen.queryByTestId("listing-archive-dialog")).not.toBeInTheDocument());
  });

  it.each(["denied", "malformed"])("production cannot POST with %s authoritative auth session", async (mode) => {
    const mock = wire(undefined, undefined, () => mode === "denied" ? json({ error: "Denied" }, 401) : json({ subject: null }));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />); await openMerge();
    fireEvent.click(screen.getByTestId("listing-merge-submit"));
    await waitFor(() => expect(screen.getByTestId("listing-merge-error")).toHaveTextContent("未送出寫入"));
    expect(writes(mock)).toHaveLength(0);
  });

  it("uses the authoritative session actor rather than the display storage subject", async () => {
    window.sessionStorage.setItem("oday.operator.subject", "spoofed-display-subject");
    const mock = wire(); render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    fireEvent.click(await screen.findByTestId(`archive-${archived.id}`));
    fireEvent.change(screen.getByTestId("listing-archive-reason"), { target: { value: "Actual entered archive reason" } });
    fireEvent.click(screen.getByTestId("listing-archive-submit"));
    await waitFor(() => expect(screen.queryByTestId("listing-archive-dialog")).not.toBeInTheDocument());
    expect(writes(mock)).toHaveLength(1);
    expect(new Headers(writes(mock)[0][1]?.headers).get("if-actor-match")).toBe(CURRENT_ACTOR);
  });

  it("does not confirm a successful response after the authoritative session changes", async () => {
    let probes = 0; const mock = wire(undefined, (init) => json(mergeReceipt("Recorded relation independently verified", new Headers(init.headers).get("idempotency-key")!)),
      () => json({ subject: ++probes === 1 ? CURRENT_ACTOR : "00000000-0000-0000-0000-000000000103" }));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />); await openMerge();
    fireEvent.click(screen.getByTestId("listing-merge-submit"));
    await waitFor(() => expect(screen.getByTestId("listing-merge-error")).toHaveTextContent("尚無法確認結果"));
    expect(screen.getByTestId("listing-merge-dialog")).toBeInTheDocument();
    expect(new Headers(writes(mock)[0][1]?.headers).get("if-actor-match")).toBe(CURRENT_ACTOR);
  });

  it("double submit uses one exact merge with source/target version and server failure preserves same logical key", async () => {
    const mock = wire(undefined, () => json({ detail: "Synthetic retryable transport boundary" }, 503));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />); await openMerge();
    fireEvent.click(screen.getByTestId("listing-merge-submit")); fireEvent.click(screen.getByTestId("listing-merge-submit"));
    await waitFor(() => expect(writes(mock)).toHaveLength(1));
    await waitFor(() => expect(screen.getByTestId("listing-merge-submit")).toBeEnabled());
    fireEvent.click(screen.getByTestId("listing-merge-submit")); await waitFor(() => expect(writes(mock)).toHaveLength(2));
    const attempts = writes(mock);
    expect(String(attempts[0][0])).toContain(`/${source.id}/merge`);
    expect(JSON.parse(String(attempts[0][1]?.body))).toMatchObject({ targetListingId: target.id, targetIfMatch: 'W/"9"' });
    expect(attempts[0][1]?.headers).toMatchObject({ "If-Match": 'W/"7"' });
    expect((attempts[0][1]?.headers as Record<string, string>)["Idempotency-Key"])
      .toBe((attempts[1][1]?.headers as Record<string, string>)["Idempotency-Key"]);
    expect(new Headers(attempts[0][1]?.headers).get("x-correlation-id"))
      .not.toBe(new Headers(attempts[1][1]?.headers).get("x-correlation-id"));
    await waitFor(() => expect(screen.getByTestId("listing-merge-submit")).toBeEnabled());
  });

  it("fresh changed target revision blocks POST and preserves operator reason", async () => {
    let current = snapshot(); const mock = wire(() => json(current));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />); await openMerge();
    current = snapshot(); current.listings[1].version = 10;
    fireEvent.click(screen.getByTestId("listing-merge-submit"));
    await waitFor(() => expect(screen.getByTestId("listing-merge-error")).toHaveTextContent("尚未送出寫入"));
    expect(writes(mock)).toHaveLength(0);
    expect(screen.getByTestId("listing-merge-reason")).toHaveValue("Recorded relation independently verified");
  });

  it("a mismatched successful receipt preserves unknown outcome and the logical request", async () => {
    const mock = wire(undefined, () => json({ source: { id: "unrelated-source" }, target: { id: "unrelated-target" } }));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />); await openMerge();
    fireEvent.click(screen.getByTestId("listing-merge-submit"));
    await waitFor(() => expect(screen.getByTestId("listing-merge-error")).toHaveTextContent("尚無法確認結果"));
    expect(writes(mock)).toHaveLength(1); expect(screen.getByTestId("listing-merge-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("listing-merge-reason")).toHaveValue("Recorded relation independently verified");
  });

  it("lost committed merge response retries the original key and versions to retrieve the terminal receipt", async () => {
    const reason = "Recorded relation independently verified";
    let receipt = mergeReceipt(reason, "not-yet-submitted");
    let current = snapshot(); let attempts = 0;
    const mock = wire(() => json(current), (init) => {
      receipt = mergeReceipt(reason, new Headers(init.headers).get("idempotency-key")!);
      attempts += 1;
      if (attempts === 1) {
        current = { ...current, listings: [{ ...current.listings[0], ...receipt.source }, ...current.listings.slice(1)] };
        throw new DOMException("Response lost after synthetic commit", "AbortError");
      }
      return json(receipt);
    });
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />); await openMerge();
    fireEvent.click(screen.getByTestId("listing-merge-submit"));
    await waitFor(() => expect(screen.getByTestId("listing-merge-submit")).toBeEnabled());
    expect(screen.getByTestId("listing-merge-error")).toHaveTextContent("尚無法確認");
    fireEvent.click(screen.getByTestId("listing-merge-submit"));
    await waitFor(() => expect(screen.queryByTestId("listing-merge-dialog")).not.toBeInTheDocument());
    expect(writes(mock)).toHaveLength(2);
    expect(writes(mock)[0][1]?.headers).toMatchObject({ "If-Match": 'W/"7"' });
    expect(writes(mock)[1][1]?.headers).toMatchObject({ "If-Match": 'W/"7"',
      "Idempotency-Key": (writes(mock)[0][1]?.headers as Record<string, string>)["Idempotency-Key"] });
  });

  it.each(["oday.operator.subject", "oday.operator.tenant"])("rechecks %s at the await boundary without needing a rerender", async (key) => {
    let pending = false; let release!: (response: Response) => void;
    const deferred = new Promise<Response>((resolve) => { release = resolve; });
    const mock = wire(() => pending ? deferred : json(snapshot()));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />); await openMerge();
    pending = true; fireEvent.click(screen.getByTestId("listing-merge-submit"));
    await waitFor(() => expect(screen.getByTestId("listing-merge-submit")).toBeDisabled());
    window.sessionStorage.setItem(key, "changed-without-render");
    await act(async () => { release(json(snapshot())); await deferred; });
    expect(writes(mock)).toHaveLength(0);
  });

  it.each(["role-return", "subject-return", "tenant-return", "location-return", "unmount"])
    ("pending fresh authorization read cannot POST after %s", async (change) => {
      let pending = false; let release!: (response: Response) => void;
      const deferred = new Promise<Response>((resolve) => { release = resolve; });
      const mock = wire(() => pending ? deferred : json(snapshot()));
      const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />); await openMerge();
      pending = true; fireEvent.click(screen.getByTestId("listing-merge-submit"));
      await waitFor(() => expect(screen.getByTestId("listing-merge-submit")).toBeDisabled());
      pending = false;
      if (change === "role-return") {
        view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-staff" />);
        view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
      } else if (change === "subject-return" || change === "tenant-return") {
        const key = change === "subject-return" ? "oday.operator.subject" : "oday.operator.tenant";
        window.sessionStorage.setItem(key, "different-recorded-context");
        view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
        window.sessionStorage.removeItem(key); view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
      } else if (change === "location-return") {
        nav.search = "ws=network&tab=review"; view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
        nav.search = "ws=network&tab=listings"; view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
      } else { view.unmount(); }
      await act(async () => { release(json(snapshot())); await deferred; });
      expect(writes(mock)).toHaveLength(0);
    });
});
