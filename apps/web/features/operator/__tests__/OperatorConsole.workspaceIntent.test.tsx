import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { OperatorConsole } from "../OperatorConsole";

const delayedPreference = vi.hoisted(() => ({ enabled: false, mode: "fixture", status: "fixture", pending: null as (() => void) | null }));
vi.mock("react", async (original) => {
  const react = await original<typeof import("react")>();
  return { ...react, useLayoutEffect: (effect: import("react").EffectCallback, deps?: import("react").DependencyList) => {
    // Release the real component's pending preference layout effect at the
    // ordering observed in the CI trace: after the click writes Network,
    // before its React state update commits. Other hooks use their normal
    // lifecycle.
    react.useLayoutEffect(() => {
      if (delayedPreference.enabled && deps?.length === 6 && deps[1] === "today"
        && deps[2] === delayedPreference.mode && deps[4] === true && deps[5] === delayedPreference.status) {
        delayedPreference.pending = effect;
        return;
      }
      return effect();
    }, deps);
  } };
});
vi.mock("../NetworkFindAreasWorkspace", () => ({
  NetworkFindAreasWorkspace: () => <div data-testid="network-find-areas-workspace" />,
}));

afterEach(() => {
  delayedPreference.enabled = false; delayedPreference.pending = null;
  cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs();
  sessionStorage.clear(); window.history.replaceState(null, "", "/operator");
});

it.each(["fixture", "authenticated"] as const)("retains the latest Network intent after an older Today effect in %s mode", async (mode) => {
  const authenticated = mode === "authenticated";
  delayedPreference.mode = mode; delayedPreference.status = authenticated ? "ready" : "fixture";
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", authenticated ? "true" : "false");
  sessionStorage.clear(); sessionStorage.setItem("oday.operator.role", "expansion-manager");
  window.history.replaceState(null, "", "/operator");
  const role = { id: "expansion-manager", label: "Expansion", subtitle: "Component test",
    allowedWorkspaces: ["today", "network"], heroName: "Test planner" };
  const envelope = {
    meta: { source: authenticated ? "operator-shell-production" : "operator-shell-api-envelope",
      dataMode: authenticated ? "live" : "fixture", role, counts: {} },
    navigation: { roles: [role], allowedWorkspaces: role.allowedWorkspaces,
      workspaces: role.allowedWorkspaces.map(id => ({ id, label: id === "network" ? "Network" : "Today",
        shortLabel: id, description: id, allowed: true })) },
    today: { hero: { name: "Test planner", roleLabel: "Expansion", scope: "Local", dateLabel: "Test" },
      kpis: [{ label: "Component test KPI", value: "1" }], queue: [], decisions: [], riskRows: [], auditFeed: [] },
    header: { counts: {} }, approvals: [], auditFeed: [], decisions: [], kpis: [], notifications: [],
    riskRows: [], search: { count: 0, items: [] }, workQueue: [],
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input) === "/auth/session"
    ? authenticated
      ? new Response(JSON.stringify({ subject: "unit-current-planner", expiresAt: Math.floor(Date.now() / 1000) + 3600 }), { status: 200 })
      : new Response(JSON.stringify({ authenticated: false }), { status: 401 })
    : new Response(JSON.stringify(envelope), { status: 200 })));
  delayedPreference.enabled = true;
  render(<OperatorConsole searchParams={{}} />);
  await waitFor(() => expect(delayedPreference.pending).toBeTypeOf("function"));
  const olderTodayEffect = delayedPreference.pending!;
  let released = false;
  const nativeSetItem = sessionStorage.setItem;
  vi.spyOn(sessionStorage, "setItem").mockImplementation(function (this: Storage, key, value) {
    const result = Reflect.apply(nativeSetItem, this, arguments);
    if (!released && key === "oday.operator.workspace" && value === "network") {
      released = true; delayedPreference.enabled = false; olderTodayEffect();
    }
    return result;
  });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Network/ })); });
  expect(released).toBe(true);
  expect(new URL(window.location.href).searchParams.get("ws")).toBe("network");
  expect(sessionStorage.getItem("oday.operator.workspace")).toBe("network");
  expect(screen.getByRole("button", { name: /Network/ })).toHaveAttribute("aria-current", "page");
  expect(screen.getByTestId("network-find-areas-workspace")).toBeInTheDocument();
});
