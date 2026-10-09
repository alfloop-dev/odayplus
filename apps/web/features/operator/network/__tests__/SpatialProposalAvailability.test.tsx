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
