import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NetworkFindAreasWorkspace } from "../NetworkFindAreasWorkspace";
import type { ReviewItem } from "../network/networkReviewTypes";

const nav = vi.hoisted(() => ({ search: "ws=network&tab=review", push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/operator", useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: nav.push }),
}));

// These responses test client lifetime, not real grants or production approval.
// The api contract reaches the existing baseline before canonical admission was fixed.
function snapshot(title = "Synthetic tenant A review") {
  const row: ReviewItem = {
    id: "synthetic-lifetime-decision", candidateId: "synthetic-lifetime-candidate", candidateTitle: title,
    zoneLabel: "synthetic-zone", recommendation: "GO", score: null, risk: "", status: "pending", statusLabel: "待審核",
    requestedBy: "synthetic-author", reviewerRole: "Site Reviewer", submittedAt: "2026-10-13",
    dueAt: null, payback: null, m12P50: null, rentReasonableness: null, cannibalization: null,
    sourceListingId: null, fieldVisit: null, brokerContact: null, notes: null, modelVersion: null,
    datasetSnapshotId: null, compareText: null, eventChips: [], history: [], decision: null,
  };
  return { source: "api", reviews: [row] };
}
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status,
  headers: { "Content-Type": "application/json" } });
function reads(reviewRead: (init?: RequestInit) => Response | Promise<Response>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    String(input).includes("network-reviews") ? reviewRead(init) : json({ detail: "Synthetic unrelated read unavailable" }, 403));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
beforeEach(() => {
  window.sessionStorage.clear(); nav.push.mockReset();
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
});
afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("review reads follow the observed current principal generation", () => {
  it("removes cached reviews immediately while the new role read is still pending", async () => {
    let count = 0, finish!: (response: Response) => void;
    reads(() => ++count === 1 ? json(snapshot()) : new Promise<Response>((resolve) => { finish = resolve; }));
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("review-card-synthetic-lifetime-decision");
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-staff" />);
    await waitFor(() => expect(count).toBe(2));
    expect(screen.queryByTestId("review-card-synthetic-lifetime-decision")).not.toBeInTheDocument();
    await act(async () => finish(json({ detail: "new role read denied" }, 403)));
    expect(await screen.findByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error");
  });

  it("re-reads the observed tenant context and keeps production identity session-bound", async () => {
    let count = 0, finish!: (response: Response) => void;
    const tenants: (string | null)[] = [];
    reads((init) => {
      tenants.push(new Headers(init?.headers).get("X-Tenant-Id"));
      return ++count === 1 ? json(snapshot()) : new Promise<Response>((resolve) => { finish = resolve; });
    });
    window.sessionStorage.setItem("oday.operator.tenant", "synthetic-tenant-a");
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("review-card-synthetic-lifetime-decision");
    window.sessionStorage.setItem("oday.operator.tenant", "synthetic-tenant-b");
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(count).toBe(2));
    // Production identity belongs to the authenticated session, not a forged
    // client tenant header. The hint invalidates the cached read only.
    expect(tenants).toEqual([null, null]);
    expect(screen.queryByTestId("review-card-synthetic-lifetime-decision")).not.toBeInTheDocument();
    await act(async () => finish(json(snapshot("Synthetic tenant B review"))));
    expect(await screen.findByTestId("review-card-synthetic-lifetime-decision")).toHaveTextContent("Synthetic tenant B review");
  });

  it("does not let the old tenant success replace a newer authenticated denial", async () => {
    let count = 0, finish!: (response: Response) => void;
    reads(() => ++count === 1 ? new Promise<Response>((resolve) => { finish = resolve; })
      : json({ detail: "current tenant read denied" }, 403));
    window.sessionStorage.setItem("oday.operator.tenant", "synthetic-tenant-a");
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(count).toBe(1));
    window.sessionStorage.setItem("oday.operator.tenant", "synthetic-tenant-b");
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(count).toBe(2));
    expect(await screen.findByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error");
    await act(async () => finish(json(snapshot())));
    expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error");
    expect(screen.queryByTestId("review-card-synthetic-lifetime-decision")).not.toBeInTheDocument();
  });
});
