import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { lazy, Suspense, type ComponentType } from "react";
import { OperatorConsole, operatorBootstrapRetryPolicy } from "../OperatorConsole";

vi.mock("next/dynamic", () => ({ default: (loader: () => Promise<unknown>) => {
  const Component = lazy(async () => {
    const loaded = await loader();
    return { default: ((loaded as { default?: unknown })?.default ?? loaded) as ComponentType<Record<string, unknown>> };
  });
  return (props: Record<string, unknown>) => <Suspense fallback={null}><Component {...props} /></Suspense>;
} }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams("ws=network&tab=radar&dialog=detail&selected=controlled-cold-intake"),
  usePathname: () => "/intake/controlled-cold-intake",
}));

// Declared transport controls exercise the real console and nested owners.
// These synthetic responses establish no native Principal or API permission.
const retryDelays = [...operatorBootstrapRetryPolicy.delaysMs];
type Read = { path: string; role: string | null };
let reads: Read[];
let release: (response: Response) => void;
const json = (body: unknown, status: number) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
const session = () => json({ subject: "controlled-cold-actor", expiresAt: Math.floor(Date.now() / 1000) + 3600 }, 200);
beforeEach(() => {
  window.sessionStorage.clear(); reads = [];
  operatorBootstrapRetryPolicy.delaysMs = retryDelays.map(() => 0);
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
  vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "production");
  const pending = new Promise<Response>((resolve) => { release = resolve; });
  let admitted = false;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), window.location.origin).pathname;
    reads.push({ path, role: new Headers(init?.headers).get("x-operator-role") });
    if (path === "/auth/session") {
      if (admitted) return session();
      const response = await pending;
      admitted = response.status === 200;
      return response.clone();
    }
    return json({ detail: "Controlled unavailable resource; no business JSON substitute" }, 503);
  }));
});
afterEach(() => {
  cleanup(); operatorBootstrapRetryPolicy.delaysMs = [...retryDelays];
  vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.restoreAllMocks();
});
const intakeReads = () => reads.filter((r) => r.path.includes("/network-listings/intake/"));
const protectedReads = () => reads.filter((r) => r.path.startsWith("/api/v1/operator/"));
const mount = () => render(<OperatorConsole searchParams={{ ws: "network", tab: "radar", dialog: "detail", selected: "controlled-cold-intake" }} />);

describe("Cold direct intake owner admission", () => {
  it("makes no protected owner request while the actual session response is unresolved", async () => {
    mount();
    await waitFor(() => expect(reads.some((r) => r.path === "/auth/session")).toBe(true));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 75)); });
    expect(protectedReads()).toEqual([]);
    await act(async () => { release(json({ authenticated: false }, 401)); });
  });
  it("uses the cold owner without a persona constraint after session admission and bootstrap refusal", async () => {
    mount(); await act(async () => { release(session()); });
    await waitFor(() => expect(intakeReads().length).toBeGreaterThan(0));
    expect(intakeReads().every((r) => r.role === null)).toBe(true);
    expect(reads.filter((r) => r.path.startsWith("/api/v1/operator/network-scoring"))).toEqual([]);
  });
  it("does not turn a denied session into a protected cold intake read", async () => {
    mount(); await act(async () => { release(json({ authenticated: false }, 403)); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 75)); });
    expect(protectedReads()).toEqual([]);
  });
});
