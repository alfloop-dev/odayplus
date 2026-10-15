import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeatureFlagsAdminWorkspace } from "../FeatureFlagsAdminWorkspace";
import { approveFeatureFlag, DEFAULT_FEATURE_FLAGS, enableFeatureFlag, type FeatureFlagDto } from "../featureFlagsAdapter";

// Explicit transport fixtures for this unit suite, not production flag grants.
function flag(overrides: Partial<FeatureFlagDto> = {}): FeatureFlagDto {
  return {
    key: "unit.actual.flag", owner: "unit-owner", enabled: true, readiness: "beta",
    high_risk: false, expires_on: null, description: "Actual API response in this test",
    approved_by: [], is_active: true, is_expired: false, ...overrides,
  };
}

function response(flags: FeatureFlagDto[]): Response {
  return new Response(JSON.stringify({ status: "ok", count: flags.length, flags }), {
    status: 200, headers: { "Content-Type": "application/json" },
  });
}

function deferredRead() {
  let resolve!: (value: Response) => void;
  const promise = new Promise<Response>((done) => { resolve = done; });
  return { promise, resolve };
}

function expectNoFlagFacts() {
  expect(screen.queryByText("Total Flags")).not.toBeInTheDocument();
  expect(screen.queryByText("Disabled (Kill-Switch)")).not.toBeInTheDocument();
  expect(screen.queryByText(/^[●○] (ACTIVE|DISABLED)$/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /啟用功能|緊急停用|簽核明細|正式啟用|以目前登入帳號核准/ })).not.toBeInTheDocument();
  for (const item of DEFAULT_FEATURE_FLAGS) expect(screen.queryByText(item.key)).not.toBeInTheDocument();
}

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("Feature Flags production read states", () => {
  it("waits for the API without displaying passed fixture rows or disabled counts", async () => {
    const pending = deferredRead();
    const read = vi.fn(() => pending.promise);
    vi.stubGlobal("fetch", read);
    render(<FeatureFlagsAdminWorkspace initialFlags={DEFAULT_FEATURE_FLAGS} baseUrl="/actual" />);

    expect(screen.getByRole("status")).toHaveTextContent("正在讀取");
    expectNoFlagFacts();
    expect(read).toHaveBeenCalledWith("/actual/api/v1/admin/feature-flags", {
      headers: { Accept: "application/json" }, cache: "no-store",
    });
    await act(async () => { pending.resolve(response([])); });
    expect(await screen.findByText("目前沒有 Feature Flag 設定。")).toBeVisible();
    expectNoFlagFacts();
  });

  it.each([
    { label: "HTTP 503", read: () => Promise.resolve(new Response("unavailable", { status: 503 })) },
    { label: "HTTP 403", read: () => Promise.resolve(new Response("forbidden", { status: 403 })) },
    { label: "network failure", read: () => Promise.reject(new Error("offline")) },
    { label: "malformed JSON", read: () => Promise.resolve(new Response("{")) },
    { label: "missing flags", read: () => Promise.resolve(new Response(JSON.stringify({ status: "ok", count: 0 }))) },
    { label: "non-array flags", read: () => Promise.resolve(new Response(JSON.stringify({ status: "ok", count: 0, flags: {} }))) },
    { label: "inconsistent count", read: () => Promise.resolve(new Response(JSON.stringify({ status: "ok", count: 1, flags: [] }))) },
    { label: "error envelope", read: () => Promise.resolve(new Response(JSON.stringify({ status: "error", count: 0, flags: [] }))) },
    { label: "invalid readiness", read: () => Promise.resolve(new Response(JSON.stringify({ status: "ok", count: 1, flags: [{ ...flag(), readiness: "invented" }] }))) },
    { label: "non-boolean activity", read: () => Promise.resolve(new Response(JSON.stringify({ status: "ok", count: 1, flags: [{ ...flag(), is_active: "false" }] }))) },
    { label: "invalid approvals", read: () => Promise.resolve(new Response(JSON.stringify({ status: "ok", count: 1, flags: [{ ...flag(), approved_by: [1] }] }))) },
    { label: "duplicate keys", read: () => Promise.resolve(response([flag(), flag()])) },
    { label: "root seed source", read: () => Promise.resolve(new Response(JSON.stringify({ status: "ok", count: 1, flags: [flag()], source: "seed-r4" }))) },
    { label: "root fixture metadata", read: () => Promise.resolve(new Response(JSON.stringify({ status: "ok", count: 1, flags: [flag()], metadata: { fixture: true } }))) },
  ])("shows an error rather than invented flag state for $label", async ({ read }) => {
    vi.stubGlobal("fetch", vi.fn(read));
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByText("目前沒有 Feature Flag 設定。")).not.toBeInTheDocument();
    expectNoFlagFacts();
    expect(screen.getByRole("button", { name: /重新整理/ })).toBeEnabled();
  });

  it("renders a successful empty registry as empty rather than five disabled flags", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response([]))));
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText("目前沒有 Feature Flag 設定。")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expectNoFlagFacts();
  });

  it("accepts the actual API registry's disabled policy defaults without inventing a read", async () => {
    const read = vi.fn(() => Promise.resolve(response(DEFAULT_FEATURE_FLAGS)));
    vi.stubGlobal("fetch", read);
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText(DEFAULT_FEATURE_FLAGS[0].key)).toBeVisible();
    expect(screen.getAllByText("○ DISABLED")).toHaveLength(DEFAULT_FEATURE_FLAGS.length);
    expect(read).toHaveBeenCalledWith("/api/v1/admin/feature-flags", {
      headers: { Accept: "application/json" }, cache: "no-store",
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders actual active and disabled rows and preserves the disable command", async () => {
    const active = flag();
    const disabled = flag({ key: "unit.other.flag", enabled: false, is_active: false });
    const read = vi.fn((input: RequestInfo | URL) => Promise.resolve(
      String(input).endsWith("/disable")
        ? new Response(JSON.stringify({ status: "ok", flag: { ...active, enabled: false, is_active: false } }))
        : response([active, disabled]),
    ));
    vi.stubGlobal("fetch", read);
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText(active.key)).toBeVisible();
    expect(screen.getByText(disabled.key)).toBeVisible();
    expect(screen.getByText("● ACTIVE")).toBeVisible();
    expect(screen.getByText("○ DISABLED")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /緊急停用/ }));
    await waitFor(() => expect(screen.getAllByText("○ DISABLED")).toHaveLength(2));
    expect(read).toHaveBeenLastCalledWith("/api/v1/admin/feature-flags/unit.actual.flag/disable", {
      method: "POST", headers: { Accept: "application/json" },
    });
  });

  it("clears old rows and the approval modal while refreshing, then allows a genuine retry", async () => {
    const pending = deferredRead();
    const read = vi.fn()
      .mockImplementationOnce(() => Promise.resolve(response([flag({ high_risk: true, approved_by: ["unit-first", "unit-second"] })])))
      .mockImplementationOnce(() => pending.promise)
      .mockImplementationOnce(() => Promise.resolve(response([])));
    vi.stubGlobal("fetch", read);
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText("unit.actual.flag")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /簽核明細/ }));
    expect(screen.getByRole("heading", { name: /高風險 Feature Flag 雙人核准/ })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /重新整理/ }));
    expect(screen.getByRole("status")).toHaveTextContent("正在讀取");
    expect(screen.queryByText("unit.actual.flag")).not.toBeInTheDocument();
    expectNoFlagFacts();
    await act(async () => { pending.resolve(new Response("unavailable", { status: 503 })); });
    expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 503");
    expectNoFlagFacts();
    fireEvent.click(screen.getByRole("button", { name: /重新整理/ }));
    expect(await screen.findByText("目前沒有 Feature Flag 設定。")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not let an older base URL response replace the latest registry", async () => {
    const oldRead = deferredRead();
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => String(input).startsWith("/old/")
      ? oldRead.promise : Promise.resolve(response([flag({ key: "unit.current.flag" })]))));
    const view = render(<FeatureFlagsAdminWorkspace baseUrl="/old" />);
    view.rerender(<FeatureFlagsAdminWorkspace baseUrl="/new" />);
    expect(await screen.findByText("unit.current.flag")).toBeVisible();
    await act(async () => { oldRead.resolve(response([flag({ key: "unit.old.flag" })])); });
    expect(screen.getByText("unit.current.flag")).toBeVisible();
    expect(screen.queryByText("unit.old.flag")).not.toBeInTheDocument();
  });
});

describe("explicit POC fixture policy", () => {
  beforeEach(() => {
    vi.stubEnv("NODE_ENV", "test");
    vi.stubEnv("ODP_DEPLOY_ENV", "test");
    vi.stubEnv("ODP_PRODUCT_MODE", "poc");
    vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
    vi.stubEnv("ODP_REQUIRE_LIVE_DATA", "false");
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
  });

  it("preserves explicitly passed POC rows without an API read", () => {
    const read = vi.fn();
    vi.stubGlobal("fetch", read);
    render(<FeatureFlagsAdminWorkspace initialFlags={[flag({ key: "unit.poc.flag" })]} />);
    expect(screen.getByText("unit.poc.flag")).toBeVisible();
    expect(read).not.toHaveBeenCalled();
  });

  it("retains the existing default fixture fallback only in explicit POC", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText(DEFAULT_FEATURE_FLAGS[0].key)).toBeVisible();
    expect(screen.getAllByText("○ DISABLED")).toHaveLength(DEFAULT_FEATURE_FLAGS.length);
  });

  it("preserves a real empty response even in POC", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response([]))));
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText("目前沒有 Feature Flag 設定。")).toBeVisible();
    expectNoFlagFacts();
  });
});


// The server authorizes real actors; these unit HTTP doubles only verify that
// production commands do not manufacture caller-supplied approval identities.
describe("Feature Flags trusted production command payloads", () => {
  it("uses the server-owned approval without requesting a handwritten identity", async () => {
    const initial = flag({ high_risk: true, enabled: false, is_active: false });
    const approved = { ...initial, approved_by: ["unit-server-actor"] };
    const read = vi.fn((input: RequestInfo | URL) => Promise.resolve(String(input).endsWith("/approve")
      ? new Response(JSON.stringify({ status: "ok", flag: approved })) : response([initial])));
    vi.stubGlobal("fetch", read);
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText(initial.key)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /簽核明細/ }));
    expect(screen.queryByPlaceholderText(/輸入核准者/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "以目前登入帳號核准" }));
    expect(await screen.findByText("unit-server-actor")).toBeVisible();
    expect(screen.getByText("已記錄目前登入帳號的核准。")).toBeVisible();
    expect(read).toHaveBeenLastCalledWith("/api/v1/admin/feature-flags/unit.actual.flag/approve", {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: "{}",
    });
  });

  it("enables using the server's stored approvals rather than posting their names", async () => {
    const initial = flag({ high_risk: true, enabled: false, is_active: false,
      approved_by: ["unit-server-first", "unit-server-second"] });
    const read = vi.fn((input: RequestInfo | URL) => Promise.resolve(String(input).endsWith("/enable")
      ? new Response(JSON.stringify({ status: "ok", flag: { ...initial, enabled: true, is_active: true } }))
      : response([initial])));
    vi.stubGlobal("fetch", read);
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText(initial.key)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /啟用功能/ }));
    expect(await screen.findByText("● ACTIVE")).toBeVisible();
    expect(read).toHaveBeenLastCalledWith("/api/v1/admin/feature-flags/unit.actual.flag/enable", {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: "{}",
    });
  });

  it("does not claim approval when the API refuses a nonadmin command", async () => {
    const initial = flag({ high_risk: true, enabled: false, is_active: false });
    const read = vi.fn((input: RequestInfo | URL) => Promise.resolve(String(input).endsWith("/approve")
      ? new Response(JSON.stringify({ detail: "role does not permit update on feature_flag" }), { status: 403 })
      : response([initial])));
    vi.stubGlobal("fetch", read);
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText(initial.key)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /簽核明細/ }));
    fireEvent.click(screen.getByRole("button", { name: "以目前登入帳號核准" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("role does not permit update on feature_flag");
    expect(screen.getByText("尚無簽核記錄")).toBeVisible();
    expect(screen.queryByText("已記錄目前登入帳號的核准。")).not.toBeInTheDocument();
    expect(screen.queryByText("● ACTIVE")).not.toBeInTheDocument();
  });

  it("does not display enabled success when the API denies enable", async () => {
    const initial = flag({ enabled: false, is_active: false });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => Promise.resolve(String(input).endsWith("/enable")
      ? new Response(JSON.stringify({ detail: "forbidden" }), { status: 403 }) : response([initial]))));
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText(initial.key)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /啟用功能/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("forbidden");
    expect(screen.getByText("○ DISABLED")).toBeVisible();
    expect(screen.queryByText("● ACTIVE")).not.toBeInTheDocument();
    expect(screen.queryByText(/Feature flag .* enabled!/)).not.toBeInTheDocument();
  });

  it("also omits forged names supplied directly to the production adapter", async () => {
    const read = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => Promise.resolve(new Response(JSON.stringify({ status: "ok", flag: flag() }))));
    vi.stubGlobal("fetch", read);
    await approveFeatureFlag("unit.actual.flag", "unverified-handwritten-name");
    expect(read.mock.calls[0][1]).toMatchObject({ method: "POST", body: "{}" });
    await enableFeatureFlag("unit.actual.flag", ["invented-first", "invented-second"]);
    expect(read.mock.calls[1][1]).toMatchObject({ method: "POST", body: "{}" });
  });

  it("preserves the explicit POC handwritten-approval and enable fixture workflow", async () => {
    vi.stubEnv("NODE_ENV", "test");
    vi.stubEnv("ODP_DEPLOY_ENV", "test");
    vi.stubEnv("ODP_PRODUCT_MODE", "poc");
    vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
    vi.stubEnv("ODP_REQUIRE_LIVE_DATA", "false");
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const initial = flag({ high_risk: true, enabled: false, is_active: false, approved_by: ["unit-poc-first"] });
    const approved = { ...initial, approved_by: ["unit-poc-first", "unit-poc-second"] };
    const read = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => Promise.resolve(new Response(JSON.stringify({ status: "ok",
      flag: String(input).endsWith("/enable") ? { ...approved, enabled: true, is_active: true } : approved }))));
    vi.stubGlobal("fetch", read);
    render(<FeatureFlagsAdminWorkspace initialFlags={[initial]} />);
    fireEvent.click(screen.getByRole("button", { name: /簽核明細/ }));
    fireEvent.change(screen.getByPlaceholderText(/輸入核准者/), { target: { value: "unit-poc-second" } });
    fireEvent.click(screen.getByRole("button", { name: "+ 簽名" }));
    expect(await screen.findByText("unit-poc-second")).toBeVisible();
    expect(read.mock.calls[0][1]).toMatchObject({ body: JSON.stringify({ approver: "unit-poc-second" }) });
    fireEvent.click(screen.getByRole("button", { name: /正式啟用/ }));
    expect(await screen.findByText("● ACTIVE")).toBeVisible();
    expect(read.mock.calls[1][1]).toMatchObject({ body: JSON.stringify({ approvals: approved.approved_by }) });
  });
});


describe("Feature Flags command context isolation", () => {
  it.each([
    { command: "approve", returnToOriginal: false },
    { command: "enable", returnToOriginal: false },
    { command: "disable", returnToOriginal: false },
    { command: "enableFromModal", returnToOriginal: false },
    { command: "approve", returnToOriginal: true },
  ])("does not commit an old $command result to a new ready registry (A-B-A=$returnToOriginal)", async ({ command, returnToOriginal }) => {
    const initial = flag({ owner: "unit-initial-owner", enabled: command === "disable", is_active: command === "disable",
      high_risk: command === "approve" || command === "enableFromModal",
      approved_by: command === "enableFromModal" ? ["unit-first", "unit-second"] : [] });
    const pendingPost = deferredRead();
    let originalReads = 0;
    const read = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return pendingPost.promise;
      const isOriginal = String(input).startsWith("/original/");
      if (isOriginal) originalReads += 1;
      const current = isOriginal && originalReads === 1 ? initial
        : flag({ enabled: false, is_active: false, owner: isOriginal ? "unit-returned-owner" : "unit-new-owner" });
      return Promise.resolve(response([current]));
    });
    vi.stubGlobal("fetch", read);
    const view = render(<FeatureFlagsAdminWorkspace baseUrl="/original" />);
    expect(await screen.findByText("unit-initial-owner")).toBeVisible();
    if (command === "approve" || command === "enableFromModal") {
      fireEvent.click(screen.getByRole("button", { name: /簽核明細/ }));
      fireEvent.click(screen.getByRole("button", { name: command === "approve" ? "以目前登入帳號核准" : /正式啟用/ }));
    } else {
      fireEvent.click(screen.getByRole("button", { name: command === "disable" ? /緊急停用/ : /啟用功能/ }));
    }
    expect(read.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    view.rerender(<FeatureFlagsAdminWorkspace baseUrl="/next" />);
    expect(await screen.findByText("unit-new-owner")).toBeVisible();
    if (returnToOriginal) {
      view.rerender(<FeatureFlagsAdminWorkspace baseUrl="/original" />);
      expect(await screen.findByText("unit-returned-owner")).toBeVisible();
    }
    await act(async () => { pendingPost.resolve(new Response(JSON.stringify({ status: "ok", flag: {
      ...initial, enabled: command === "approve" ? initial.enabled : command !== "disable",
      is_active: command === "approve" ? initial.is_active : command !== "disable",
      approved_by: command === "approve" ? ["unit-old-approval"] : command === "disable" ? [] : initial.approved_by,
    } }))); });
    expect(screen.getByText(returnToOriginal ? "unit-returned-owner" : "unit-new-owner")).toBeVisible();
    expect(screen.queryByText("unit-initial-owner")).not.toBeInTheDocument();
    expect(screen.queryByText("unit-old-approval")).not.toBeInTheDocument();
    expect(screen.queryByText("● ACTIVE")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /高風險 Feature Flag 雙人核准/ })).not.toBeInTheDocument();
    expect(screen.queryByText("已記錄目前登入帳號的核准。")).not.toBeInTheDocument();
    expect(screen.queryByText(/Kill-Switch engaged:|Feature flag .* enabled!|Dual approval complete!/)).not.toBeInTheDocument();
  });
});
