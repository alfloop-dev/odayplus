import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HeatZoneMergeSplitPanel } from "../HeatZoneMergeSplitPanel";
import { NetworkFindAreasWorkspace } from "../../NetworkFindAreasWorkspace";

vi.mock("next/navigation", () => ({
  usePathname: () => "/operator",
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams("ws=network&tab=composition"),
}));
// Exercise actual workspace state and panel without Next's chunk loader.
vi.mock("next/dynamic", () => ({
  default: (loader: () => unknown) => loader.toString().includes("HeatZoneMergeSplitPanel")
    ? HeatZoneMergeSplitPanel
    : () => null,
}));

const proposal = {
  proposal_id: "p1", zone_id: "z1", tenant_id: "tenant-a", composition_kind: "MERGED", status: "PROPOSED",
  member_cell_ids: ["a", "b"], member_count: 2, ndcg_gain: 0, cannibalization_variance_reduction: 0,
  correlation_rho: 0.8, disconnect_index: 0, confidence: 0.8, model_version: "m1", policy_version_id: "v1",
  created_at: "2026-10-09T00:00:00Z", reasons: [], warnings: [],
};

describe("Spatial scoped list reads", () => {
  let reads: Array<{ resolve: (response: Response) => void; role: string }>;
  beforeEach(() => {
    reads = [];
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn((url: string, init: RequestInit) => {
      if (url === "/api/v1/heatzones/merge-split/proposals") {
        return new Promise<Response>((resolve) => {
          reads.push({ resolve, role: new Headers(init.headers).get("X-Operator-Role") ?? "" });
        });
      }
      return new Promise<Response>(() => undefined);
    }));
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("does not show an empty success for a failed read and retries in production", async () => {
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" initialTabId="composition" />);
    await waitFor(() => expect(reads).toHaveLength(1));
    expect(screen.getByTestId("loading-proposals")).toBeVisible();
    await act(async () => { reads[0].resolve(new Response("{}", { status: 403 })); });
    expect(screen.getByTestId("proposal-read-error")).toHaveTextContent("HTTP 403");
    expect(screen.queryByTestId("empty-proposals")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新載入提案" }));
    await waitFor(() => expect(reads).toHaveLength(2));
    expect(screen.getByTestId("loading-proposals")).toBeVisible();
    await act(async () => { reads[1].resolve(Response.json({ items: [] })); });
    expect(screen.getByTestId("empty-proposals")).toBeVisible();
    expect(screen.queryByTestId("proposal-read-error")).not.toBeInTheDocument();
  });

  it.each(["approve", "reject"])("keeps %s POST acknowledgement when subsequent GET fails, then reads terminal retry", async (kind) => {
    const write = vi.fn().mockResolvedValue(Response.json({ accepted: true }));
    const readFetch = fetch;
    vi.stubGlobal("fetch", vi.fn((url: string, init: RequestInit) => url.endsWith(`/${kind}`) ? write(url, init) : readFetch(url, init)));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" initialTabId="composition" />);
    await waitFor(() => expect(reads).toHaveLength(1));
    await act(async () => { reads[0].resolve(Response.json({ items: [proposal] })); });
    fireEvent.click(screen.getByTestId(`btn-open-${kind}`));
    if (kind === "reject") fireEvent.change(screen.getByRole("textbox"), { target: { value: "邊界待確認" } });
    fireEvent.click(screen.getByTestId(`btn-confirm-${kind}`));
    await waitFor(() => expect(reads).toHaveLength(2));
    expect(screen.getByRole("textbox")).toBeDisabled();
    await act(async () => { reads[1].resolve(new Response("{}", { status: 500 })); });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByTestId("feedback-message")).toHaveTextContent("請求已成功");
    expect(screen.getByTestId("feedback-message")).toHaveTextContent("勿重複提交");
    expect(screen.getByTestId("proposal-read-error")).toHaveTextContent("HTTP 500");
    fireEvent.click(screen.getByRole("button", { name: "重新載入提案" }));
    await waitFor(() => expect(reads).toHaveLength(3));
    await act(async () => { reads[2].resolve(Response.json({ items: [{ ...proposal, status: kind === "approve" ? "APPLIED" : "REJECTED" }] })); });
    expect(screen.getByTestId("proposal-detail")).toBeVisible();
    expect(screen.queryByTestId(`btn-open-${kind}`)).not.toBeInTheDocument();
    expect(write).toHaveBeenCalledTimes(1);
  });

  it("does not launch a stale-persona readback after a late write", async () => {
    let resolve!: (response: Response) => void;
    const readFetch = fetch;
    vi.stubGlobal("fetch", vi.fn((url: string, init: RequestInit) => url.endsWith("/approve")
      ? new Promise<Response>((done) => { resolve = done; }) : readFetch(url, init)));
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" initialTabId="composition" />);
    await waitFor(() => expect(reads).toHaveLength(1));
    await act(async () => { reads[0].resolve(Response.json({ items: [proposal] })); });
    fireEvent.click(screen.getByTestId("btn-open-approve"));
    fireEvent.click(screen.getByTestId("btn-confirm-approve"));
    await waitFor(() => expect(resolve).toBeDefined());
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="pm-audit" initialTabId="composition" />);
    await waitFor(() => expect(reads).toHaveLength(2));
    await act(async () => { reads[1].resolve(Response.json({ items: [] })); resolve(Response.json({ accepted: true })); });
    expect(reads).toHaveLength(2);
    expect(screen.getByTestId("empty-proposals")).toBeVisible();
    expect(screen.queryByTestId("feedback-message")).not.toBeInTheDocument();
  });

  it("ignores a late denial from a superseded persona read", async () => {
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" initialTabId="composition" />);
    await waitFor(() => expect(reads).toHaveLength(1));
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="pm-audit" initialTabId="composition" />);
    await waitFor(() => expect(reads).toHaveLength(2));
    expect(reads.map((read) => read.role)).toEqual(["expansion-manager", "pm-audit"]);
    await act(async () => { reads[1].resolve(Response.json({ items: [] })); });
    expect(screen.getByTestId("empty-proposals")).toBeVisible();
    await act(async () => { reads[0].resolve(new Response("{}", { status: 403 })); });
    expect(screen.getByTestId("empty-proposals")).toBeVisible();
    expect(screen.queryByTestId("proposal-read-error")).not.toBeInTheDocument();
  });
});
