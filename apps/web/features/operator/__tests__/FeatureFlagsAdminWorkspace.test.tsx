import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeatureFlagsAdminWorkspace } from "../FeatureFlagsAdminWorkspace";
import { approveFeatureFlag, DEFAULT_FEATURE_FLAGS, disableFeatureFlag, enableFeatureFlag, fetchFeatureFlags, type FeatureFlagDto } from "../featureFlagsAdapter";

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

const reasonText = "Reviewed actual flag operation impact";
const proof = { reason: reasonText, riskAcknowledged: true as const };

function fillCommandProof(reason = reasonText) {
  fireEvent.change(screen.getByRole("textbox", { name: /操作理由/ }), { target: { value: reason } });
  fireEvent.click(screen.getByRole("checkbox", { name: "我已確認此操作的風險及影響範圍" }));
}

function confirmChosenCommand() {
  fillCommandProof();
  fireEvent.click(screen.getByRole("button", { name: /^確認(核准|啟用|停用)$/ }));
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
    confirmChosenCommand();
    await waitFor(() => expect(screen.getAllByText("○ DISABLED")).toHaveLength(2));
    expect(read).toHaveBeenLastCalledWith("/api/v1/admin/feature-flags/unit.actual.flag/disable", {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(proof),
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
    confirmChosenCommand();
    expect(await screen.findByText("unit-server-actor")).toBeVisible();
    expect(screen.getByText("已記錄目前登入帳號的核准。")).toBeVisible();
    expect(read).toHaveBeenLastCalledWith("/api/v1/admin/feature-flags/unit.actual.flag/approve", {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(proof),
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
    confirmChosenCommand();
    expect(await screen.findByText("● ACTIVE")).toBeVisible();
    expect(read).toHaveBeenLastCalledWith("/api/v1/admin/feature-flags/unit.actual.flag/enable", {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(proof),
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
    confirmChosenCommand();
    expect(await screen.findByRole("alert")).toHaveTextContent("role does not permit update on feature_flag");
    expect(within(screen.getByRole("dialog")).getByRole("alert")).toHaveTextContent("role does not permit update on feature_flag");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
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
    confirmChosenCommand();
    expect(await screen.findByRole("alert")).toHaveTextContent("forbidden");
    expect(within(screen.getByRole("dialog")).getByRole("alert")).toHaveTextContent("forbidden");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getByText("○ DISABLED")).toBeVisible();
    expect(screen.queryByText("● ACTIVE")).not.toBeInTheDocument();
    expect(screen.queryByText(/Feature flag .* enabled!/)).not.toBeInTheDocument();
  });

  it("also omits forged names supplied directly to the production adapter", async () => {
    const read = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => Promise.resolve(new Response(JSON.stringify({ status: "ok", flag: flag() }))));
    vi.stubGlobal("fetch", read);
    await approveFeatureFlag("unit.actual.flag", "unverified-handwritten-name", "", proof);
    expect(read.mock.calls[0][1]).toMatchObject({ method: "POST", body: JSON.stringify(proof) });
    await enableFeatureFlag("unit.actual.flag", ["invented-first", "invented-second"], "", proof);
    expect(read.mock.calls[1][1]).toMatchObject({ method: "POST", body: JSON.stringify(proof) });
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
    fireEvent.click(screen.getByRole("button", { name: "+ 簽名" }));
    fireEvent.change(screen.getByPlaceholderText(/輸入核准者/), { target: { value: "unit-poc-second" } });
    confirmChosenCommand();
    expect(await screen.findByText("unit-poc-second")).toBeVisible();
    expect(read.mock.calls[0][1]).toMatchObject({ body: JSON.stringify({ approver: "unit-poc-second", ...proof }) });
    fireEvent.click(screen.getByRole("button", { name: /正式啟用/ }));
    confirmChosenCommand();
    expect(await screen.findByText("● ACTIVE")).toBeVisible();
    expect(read.mock.calls[1][1]).toMatchObject({ body: JSON.stringify({ approvals: approved.approved_by, ...proof }) });
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
    confirmChosenCommand();
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

describe("Feature Flags reason and explicit final confirmation", () => {
  it.each(["approve", "enable", "disable"] as const)("does not POST on the first %s click, invalid proof, or cancel", async (command) => {
    const initial = flag({ enabled: command === "disable", is_active: command === "disable", high_risk: command === "approve" });
    const read = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => Promise.resolve(response([initial])));
    vi.stubGlobal("fetch", read);
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText(initial.key)).toBeVisible();
    if (command === "approve") {
      fireEvent.click(screen.getByRole("button", { name: /簽核明細/ }));
      fireEvent.click(screen.getByRole("button", { name: "以目前登入帳號核准" }));
    } else {
      fireEvent.click(screen.getByRole("button", { name: command === "disable" ? /緊急停用/ : /啟用功能/ }));
    }
    const posts = () => read.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts()).toHaveLength(0);
    expect(screen.getByRole("dialog")).toHaveTextContent(initial.key);
    expect(screen.getByRole("dialog")).toHaveTextContent(initial.owner);
    fireEvent.click(screen.getByRole("button", { name: /^確認(核准|啟用|停用)$/ }));
    expect(screen.getByRole("alert")).toHaveTextContent("操作風險");
    expect(posts()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts()).toHaveLength(0);
    expect(screen.getByText(command === "disable" ? "● ACTIVE" : "○ DISABLED")).toBeVisible();
  });

  it("rejects a nine-character approval reason and an unchecked acknowledgment before one trimmed trusted POST", async () => {
    const initial = flag({ high_risk: true, enabled: false, is_active: false });
    const approved = { ...initial, approved_by: ["actual-server-subject"] };
    const read = vi.fn((input: RequestInfo | URL, init?: RequestInit) => Promise.resolve(init?.method === "POST"
      ? new Response(JSON.stringify({ status: "ok", flag: approved })) : response([initial])));
    vi.stubGlobal("fetch", read);
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText(initial.key)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /簽核明細/ }));
    fireEvent.click(screen.getByRole("button", { name: "以目前登入帳號核准" }));
    fillCommandProof("123456789");
    fireEvent.click(screen.getByRole("button", { name: "確認核准" }));
    expect(screen.getByRole("alert")).toHaveTextContent("至少需要 10");
    fireEvent.change(screen.getByRole("textbox", { name: /操作理由/ }), { target: { value: `  ${reasonText}  ` } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "確認核准" }));
    expect(read.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "確認核准" }));
    expect(await screen.findByText("actual-server-subject")).toBeVisible();
    const posts = read.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts).toHaveLength(1);
    expect(JSON.parse(String(posts[0][1]?.body))).toEqual(proof);
    expect(screen.getByRole("button", { name: /正式啟用/ })).toBeDisabled();
    expect(screen.getByText("○ DISABLED")).toBeVisible();
  });

  it("keeps a failed command's reason and acknowledgment for retry and displays the actual pending audit receipt", async () => {
    const initial = flag({ enabled: false, is_active: false });
    let attempts = 0;
    const read = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method !== "POST") return Promise.resolve(response([initial]));
      attempts += 1;
      return Promise.resolve(attempts === 1
        ? new Response(JSON.stringify({ detail: "actual server refusal" }), { status: 403 })
        : new Response(JSON.stringify({ status: "ok", flag: { ...initial, enabled: true, is_active: true }, audit: { event_id: "actual-flag-event-id", delivery_status: "pending" } })));
    });
    vi.stubGlobal("fetch", read);
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText(initial.key)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /啟用功能/ }));
    confirmChosenCommand();
    expect(await screen.findByRole("alert")).toHaveTextContent("actual server refusal");
    expect(within(screen.getByRole("dialog")).getByRole("alert")).toHaveTextContent("actual server refusal");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getByRole("textbox", { name: /操作理由/ })).toHaveValue(reasonText);
    expect(screen.getByRole("checkbox")).toBeChecked();
    expect(screen.getByText("○ DISABLED")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "確認啟用" }));
    expect(await screen.findByText("● ACTIVE")).toBeVisible();
    expect(screen.getByText(/稽核事件：actual-flag-event-id；傳遞狀態：pending/)).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText(/已封存|已歸檔|已送達外部/)).not.toBeInTheDocument();
    const posts = read.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts).toHaveLength(2);
    for (const [, init] of posts) expect(JSON.parse(String(init?.body))).toEqual(proof);
  });

  it("admits only one in-flight final submit and blocks dismissal until its canonical response", async () => {
    const initial = flag({ enabled: false, is_active: false });
    const pending = deferredRead();
    const read = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => init?.method === "POST" ? pending.promise : Promise.resolve(response([initial])));
    vi.stubGlobal("fetch", read);
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText(initial.key)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /啟用功能/ }));
    fillCommandProof();
    const form = screen.getByRole("textbox", { name: /操作理由/ }).closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form!);
    fireEvent.submit(form!);
    expect(read.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "處理中" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(screen.getByText("○ DISABLED")).toBeVisible();
    await act(async () => { pending.resolve(new Response(JSON.stringify({ status: "ok", flag: { ...initial, enabled: true, is_active: true } }))); });
    expect(await screen.findByText("● ACTIVE")).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("cancels a prepared command with Escape without posting and restores the invoking control", async () => {
    const initial = flag({ enabled: false, is_active: false });
    const read = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => Promise.resolve(response([initial])));
    vi.stubGlobal("fetch", read);
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText(initial.key)).toBeVisible();
    const trigger = screen.getByRole("button", { name: /啟用功能/ });
    trigger.focus();
    fireEvent.click(trigger);
    fillCommandProof();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(read.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  });

  it.each(["pending", "not_configured"])("retains only the actual %s audit receipt from the adapter", async (delivery_status) => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(JSON.stringify({ status: "ok", flag: flag(), audit: { event_id: "server-event", delivery_status } })))));
    const result = await disableFeatureFlag("unit.actual.flag", "", proof);
    expect(result.audit).toEqual({ event_id: "server-event", delivery_status });
  });
});

it.each([
  { label: "different flag key", result: { status: "ok", flag: flag({ key: "other-server-key" }) } },
  { label: "malformed audit receipt", result: { status: "ok", flag: flag(), audit: { event_id: "", delivery_status: "pending" } } },
])("retains the original state and proof when a command returns $label", async ({ result }) => {
  const initial = flag({ enabled: false, is_active: false });
  vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => Promise.resolve(init?.method === "POST"
    ? new Response(JSON.stringify(result)) : response([initial]))));
  render(<FeatureFlagsAdminWorkspace />);
  expect(await screen.findByText(initial.key)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: /啟用功能/ }));
  confirmChosenCommand();
  expect(await screen.findByRole("alert")).toHaveTextContent("回應格式無效");
  expect(within(screen.getByRole("dialog")).getByRole("alert")).toHaveTextContent("回應格式無效");
  expect(screen.getAllByRole("alert")).toHaveLength(1);
  expect(screen.getByText("○ DISABLED")).toBeVisible();
  expect(screen.queryByText("● ACTIVE")).not.toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: /操作理由/ })).toHaveValue(reasonText);
  expect(screen.getByRole("checkbox")).toBeChecked();
});

const seededCommandResponses = [
  { label: "root seed source", result: { status: "ok", source: "seed-r4", flag: flag() } },
  { label: "nested fixture metadata", result: { status: "ok", flag: { ...flag(), metadata: { fixture: true } } } },
];

it.each(seededCommandResponses)("rejects production POST $label without changing state, proof or success", async ({ result }) => {
  const initial = flag({ enabled: false, is_active: false });
  vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => Promise.resolve(init?.method === "POST"
    ? new Response(JSON.stringify(result)) : response([initial]))));
  render(<FeatureFlagsAdminWorkspace />);
  expect(await screen.findByText(initial.key)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: /啟用功能/ }));
  confirmChosenCommand();
  expect(await screen.findByRole("alert")).toHaveTextContent("非正式資料來源");
  expect(within(screen.getByRole("dialog")).getByRole("alert")).toHaveTextContent("非正式資料來源");
  expect(screen.getAllByRole("alert")).toHaveLength(1);
  expect(screen.getByText("○ DISABLED")).toBeVisible();
  expect(screen.queryByText("● ACTIVE")).not.toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: /操作理由/ })).toHaveValue(reasonText);
  expect(screen.getByRole("checkbox")).toBeChecked();
  expect(screen.queryByText(/Feature flag .* enabled!/)).not.toBeInTheDocument();
});

it.each(seededCommandResponses)("preserves explicit POC command compatibility for $label", async ({ result }) => {
  vi.stubEnv("NODE_ENV", "test");
  vi.stubEnv("ODP_DEPLOY_ENV", "test");
  vi.stubEnv("ODP_PRODUCT_MODE", "poc");
  vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
  vi.stubEnv("ODP_REQUIRE_LIVE_DATA", "false");
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
  const read = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => Promise.resolve(new Response(JSON.stringify(result))));
  vi.stubGlobal("fetch", read);
  const actual = await enableFeatureFlag("unit.actual.flag", ["unit-poc-first", "unit-poc-second"], "", proof);
  expect(actual.flag).toEqual(result.flag);
  expect(read.mock.calls[0][1]).toMatchObject({ body: JSON.stringify({ approvals: ["unit-poc-first", "unit-poc-second"], ...proof }) });
});

// Frozen H contract from Root/Codex. Every response below is synthetic;
// these tests do not grant any Intake release/source/model permission.
const tenantA = "00000000-0000-0000-0000-000000000001";
const tenantB = "00000000-0000-0000-0000-000000000002";
function scopedFlag(overrides: Partial<FeatureFlagDto> = {}): FeatureFlagDto {
  return flag({
    key: "unit.scoped.flag", enabled: false, is_active: false, high_risk: true,
    scope: "per-tenant-per-source", configuration_version: 7,
    bindings: [{ tenant_id: tenantA, source_id: "unit-source-a" }, { tenant_id: tenantB, source_id: "unit-source-b" }],
    approved_by: ["unit-first", "unit-second"], ...overrides,
  });
}
function commandResponse(value: unknown): Response {
  return new Response(JSON.stringify({ status: "ok", flag: value }));
}

const invalidScopedMetadata = [
  { label: "missing scope", metadata: { bindings: [], configuration_version: 7 } },
  { label: "missing bindings", metadata: { scope: "per-tenant", configuration_version: 7 } },
  { label: "missing version", metadata: { scope: "per-tenant", bindings: [] } },
  { label: "partial global metadata", metadata: { scope: "global" } },
  { label: "unknown scope", metadata: { scope: "per-source", bindings: [], configuration_version: 7 } },
  { label: "null scope", metadata: { scope: null, bindings: [], configuration_version: 7 } },
  ...[null, "7", true, 0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1].map((version) => ({
    label: `invalid version ${version}`, metadata: { scope: "per-tenant", bindings: [], configuration_version: version },
  })),
  ...[null, {}, [null], [{ tenant_id: "not-a-uuid", source_id: null }], [{ tenant_id: tenantA }],
    [{ tenant_id: tenantA, source_id: 42 }], [{ tenant_id: tenantA, source_id: null, extra: true }],
    [{ tenant_id: tenantA, source_id: "unit-source" }],
    [{ tenant_id: tenantA, source_id: null }, { tenant_id: tenantA, source_id: null }],
  ].map((bindings, index) => ({ label: `invalid tenant binding ${index}`, metadata: { scope: "per-tenant", bindings, configuration_version: 7 } })),
  ...[null, "", "  "].map((source_id) => ({
    label: `invalid paired source ${source_id}`, metadata: { scope: "per-tenant-per-source", bindings: [{ tenant_id: tenantA, source_id }], configuration_version: 7 },
  })),
  { label: "duplicate pair", metadata: { scope: "per-tenant-per-source", bindings: [{ tenant_id: tenantA, source_id: "unit-source" }, { tenant_id: tenantA, source_id: "unit-source" }], configuration_version: 7 } },
  { label: "global with tenant binding", metadata: { scope: "global", bindings: [{ tenant_id: tenantA, source_id: null }], configuration_version: 7 } },
];

describe("scoped flag adapter contract", () => {
  it.each(invalidScopedMetadata)("rejects $label on admin reads and every command response", async ({ metadata }) => {
    const malformed = { ...flag(), ...metadata };
    const read = vi.fn((_url: RequestInfo | URL, init?: RequestInit) => Promise.resolve(init?.method === "POST"
      ? commandResponse(malformed) : response([malformed as FeatureFlagDto])));
    vi.stubGlobal("fetch", read);
    await expect(fetchFeatureFlags()).rejects.toThrow("資料格式無效");
    await expect(approveFeatureFlag("unit.actual.flag", "forged", "", proof)).rejects.toThrow("回應格式無效");
    await expect(enableFeatureFlag("unit.actual.flag", ["forged"], "", proof)).rejects.toThrow("回應格式無效");
    await expect(disableFeatureFlag("unit.actual.flag", "", proof)).rejects.toThrow("回應格式無效");
  });

  it.each(["global", "per-tenant", "global-per-tenant", "per-tenant-per-source"] as const)("retains complete %s metadata without expanding pair units", async (scope) => {
    const actual = scopedFlag({ scope, bindings: scope === "global" ? [] : scope === "per-tenant-per-source"
      ? scopedFlag().bindings : [{ tenant_id: tenantA, source_id: null }] });
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response([actual]))));
    expect(await fetchFeatureFlags()).toEqual([actual]);
  });

  it("preserves the legacy global DTO and command bodies without inventing metadata", async () => {
    const legacy = flag();
    const read = vi.fn((_url: RequestInfo | URL, init?: RequestInit) => Promise.resolve(init?.method === "POST"
      ? commandResponse(legacy) : response([legacy])));
    vi.stubGlobal("fetch", read);
    expect(await fetchFeatureFlags()).toEqual([legacy]);
    await approveFeatureFlag(legacy.key, "forged", "", proof);
    await enableFeatureFlag(legacy.key, ["forged"], "", proof);
    for (const [, init] of read.mock.calls.filter(([, init]) => init?.method === "POST")) expect(JSON.parse(String(init?.body))).toEqual(proof);
  });

  it.each([0, -1, 1.5, Number.NaN, Number.POSITIVE_INFINITY, Number.MAX_SAFE_INTEGER + 1])("refuses invalid request version %s before transport", async (configuration_version) => {
    const read = vi.fn();
    vi.stubGlobal("fetch", read);
    const invalid = { ...proof, configuration_version };
    await expect(approveFeatureFlag("unit.scoped.flag", "forged", "", invalid)).rejects.toThrow("設定版本無效");
    await expect(enableFeatureFlag("unit.scoped.flag", ["forged"], "", invalid)).rejects.toThrow("設定版本無效");
    expect(read).not.toHaveBeenCalled();
  });

  it.each(["approve", "enable"] as const)("rejects a successful %s response for a different or missing reviewed version", async (command) => {
    for (const returned of [scopedFlag({ configuration_version: 8 }), flag({ key: "unit.scoped.flag" })]) {
      vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(commandResponse(returned))));
      const versionedProof = { ...proof, configuration_version: 7 };
      await expect(command === "approve"
        ? approveFeatureFlag(returned.key, "forged", "", versionedProof)
        : enableFeatureFlag(returned.key, ["forged"], "", versionedProof)).rejects.toThrow("回應格式無效");
    }
  });
});

describe("original flag confirmation with scope and version", () => {
  it.each([false, true])("keeps the enabled scoped Kill-Switch usable with is_active=false (expired=%s)", async (is_expired) => {
    const initial = scopedFlag({ enabled: true, is_active: false, is_expired,
      expires_on: is_expired ? "2000-01-01" : null });
    const read = vi.fn((_url: RequestInfo | URL, init?: RequestInit) => Promise.resolve(init?.method === "POST"
      ? commandResponse({ ...initial, enabled: false, approved_by: [], configuration_version: 8 }) : response([initial])));
    vi.stubGlobal("fetch", read);
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText(initial.key)).toBeVisible();
    expect(screen.getByText("● ACTIVE")).toBeVisible(); // explicitly labelled stored configuration
    expect(screen.getByText(/情境有效性（伺服器）：不生效/)).toBeVisible();
    expect(screen.getByRole("button", { name: "已啟用 (1)" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "已停用 (0)" }));
    expect(screen.queryByText(initial.key)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "已啟用 (1)" }));
    fireEvent.click(screen.getByRole("button", { name: /緊急停用/ }));
    expect(screen.getByRole("dialog")).toHaveTextContent("儲存設定：已啟用");
    expect(screen.getByRole("dialog")).toHaveTextContent(`到期判定（伺服器）：${is_expired ? "已到期" : "未到期"}`);
    expect(screen.getByRole("dialog")).toHaveTextContent("設定版本：7");
    expect(read.mock.calls).toHaveLength(1);
    confirmChosenCommand();
    expect(await screen.findByText(/Kill-Switch engaged:/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "已停用 (1)" }));
    expect(screen.getByText("○ DISABLED")).toBeVisible();
    expect(read).toHaveBeenLastCalledWith("/api/v1/admin/feature-flags/unit.scoped.flag/disable", {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(proof),
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("設定版本：")).toHaveTextContent("設定版本：8");
  });

  it("shows only exact reviewed pairs through approval then enable and posts that version without identity claims", async () => {
    const initial = scopedFlag({ approved_by: ["unit-first"] });
    const approved = { ...initial, approved_by: ["unit-first", "unit-server-second"] };
    const read = vi.fn((url: RequestInfo | URL, init?: RequestInit) => Promise.resolve(init?.method !== "POST" ? response([initial])
      : commandResponse(String(url).endsWith("/approve") ? approved : { ...approved, enabled: true })));
    vi.stubGlobal("fetch", read);
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText(initial.key)).toBeVisible();
    const assertPairs = (root: HTMLElement) => {
      const rows = within(root).getAllByRole("listitem");
      expect(rows).toHaveLength(2);
      expect(rows[0]).toHaveTextContent(`Tenant: ${tenantA} · Source: unit-source-a`);
      expect(rows[0]).not.toHaveTextContent("unit-source-b");
      expect(rows[1]).toHaveTextContent(`Tenant: ${tenantB} · Source: unit-source-b`);
      expect(rows[1]).not.toHaveTextContent("unit-source-a");
    };
    assertPairs(screen.getByRole("list", { name: "精確範圍單位" }));
    fireEvent.click(screen.getByRole("button", { name: /簽核明細/ }));
    fireEvent.click(screen.getByRole("button", { name: "以目前登入帳號核准" }));
    const modal = screen.getByRole("dialog");
    assertPairs(within(modal).getByRole("list", { name: "精確範圍單位" }));
    expect(modal).toHaveTextContent("範圍：per-tenant-per-source");
    expect(modal).toHaveTextContent("設定版本：7");
    confirmChosenCommand();
    expect(await screen.findByText("unit-server-second")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /正式啟用/ }));
    expect(screen.getByRole("dialog")).toHaveTextContent("設定版本：7");
    confirmChosenCommand();
    expect(await screen.findByText("● ACTIVE")).toBeVisible();
    expect(screen.getByText(/情境有效性（伺服器）：不生效/)).toBeVisible();
    const posts = read.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts.map(([url]) => url)).toEqual(["/api/v1/admin/feature-flags/unit.scoped.flag/approve", "/api/v1/admin/feature-flags/unit.scoped.flag/enable"]);
    for (const [, init] of posts) expect(JSON.parse(String(init?.body))).toEqual({ ...proof, configuration_version: 7 });
    // The same original details flow must offer disable, never re-enable, for a stored-enabled flag.
    fireEvent.click(screen.getByRole("button", { name: /簽核明細/ }));
    expect(within(screen.getByRole("dialog")).getByRole("button", { name: /緊急停用/ })).toBeEnabled();
    expect(within(screen.getByRole("dialog")).queryByRole("button", { name: /正式啟用/ })).not.toBeInTheDocument();
  });

  it("also sends a provided global configuration version from the reviewed confirmation", async () => {
    const initial = scopedFlag({ scope: "global", bindings: [] });
    const read = vi.fn((_url: RequestInfo | URL, init?: RequestInit) => Promise.resolve(init?.method === "POST"
      ? commandResponse({ ...initial, enabled: true, is_active: true }) : response([initial])));
    vi.stubGlobal("fetch", read);
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText(initial.key)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /啟用功能/ }));
    expect(screen.getByRole("dialog")).toHaveTextContent("範圍：global");
    expect(screen.getByRole("dialog")).toHaveTextContent("設定版本：7");
    confirmChosenCommand();
    expect(await screen.findByText("● ACTIVE")).toBeVisible();
    expect(JSON.parse(String(read.mock.calls[1][1]?.body))).toEqual({ ...proof, configuration_version: 7 });
  });

  it.each(["approve", "enable"] as const)("preserves proof, reviewed version and server refusal without automatic %s retry/reapproval", async (command) => {
    for (const status of [409, 403, 503]) {
      const initial = scopedFlag();
      const read = vi.fn((_url: RequestInfo | URL, init?: RequestInit) => Promise.resolve(init?.method === "POST"
        ? new Response(JSON.stringify({ detail: `unit-server-${status}` }), { status }) : response([initial])));
      vi.stubGlobal("fetch", read);
      const view = render(<FeatureFlagsAdminWorkspace />);
      expect(await screen.findByText(initial.key)).toBeVisible();
      if (command === "approve") {
        fireEvent.click(screen.getByRole("button", { name: /簽核明細/ }));
        fireEvent.click(screen.getByRole("button", { name: "以目前登入帳號核准" }));
      } else fireEvent.click(screen.getByRole("button", { name: /啟用功能/ }));
      confirmChosenCommand();
      expect(await screen.findByRole("alert")).toHaveTextContent(`unit-server-${status}`);
      expect(screen.getByRole("textbox", { name: /操作理由/ })).toHaveValue(reasonText);
      expect(screen.getByRole("checkbox")).toBeChecked();
      expect(screen.getByRole("dialog")).toHaveTextContent("設定版本：7");
      expect(screen.getByText("○ DISABLED")).toBeVisible();
      expect(read.mock.calls).toHaveLength(2); // no automatic read, retry, reapproval, or enable
      expect(JSON.parse(String(read.mock.calls[1][1]?.body))).toEqual({ ...proof, configuration_version: 7 });
      view.unmount();
    }
  });

  it.each([
    { label: "changed configuration", returned: scopedFlag({ configuration_version: 8 }) },
    { label: "missing scoped version", returned: { ...scopedFlag(), configuration_version: null } },
    { label: "malformed binding", returned: { ...scopedFlag(), bindings: [{ tenant_id: tenantA }] } },
  ])("retains reviewed scoped state and proof for a $label command response", async ({ returned }) => {
    const initial = scopedFlag();
    const read = vi.fn((_url: RequestInfo | URL, init?: RequestInit) => Promise.resolve(init?.method === "POST"
      ? commandResponse(returned) : response([initial])));
    vi.stubGlobal("fetch", read);
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText(initial.key)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /啟用功能/ }));
    confirmChosenCommand();
    expect(await screen.findByRole("alert")).toHaveTextContent("回應格式無效");
    expect(screen.getByRole("dialog")).toHaveTextContent("設定版本：7");
    expect(screen.getByRole("textbox", { name: /操作理由/ })).toHaveValue(reasonText);
    expect(screen.getByRole("checkbox")).toBeChecked();
    expect(screen.getByText("○ DISABLED")).toBeVisible();
    expect(read.mock.calls).toHaveLength(2);
  });

  it.each(["per-tenant", "global-per-tenant"] as const)("displays exact tenant-only units for %s in the same confirmation", async (scope) => {
    const initial = scopedFlag({ scope, bindings: [{ tenant_id: tenantA, source_id: null }] });
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response([initial]))));
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText(initial.key)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /啟用功能/ }));
    const modal = screen.getByRole("dialog");
    expect(modal).toHaveTextContent(`範圍：${scope}`);
    expect(modal).toHaveTextContent("設定版本：7");
    expect(within(modal).getAllByRole("listitem").filter((item) => item.textContent?.includes("Tenant:"))).toHaveLength(1);
    expect(within(modal).getByRole("list", { name: "精確範圍單位" })).toHaveTextContent(`Tenant: ${tenantA} · Source: 不限定來源`);
  });

  it("labels omitted global metadata as legacy rather than inventing a reviewed version", async () => {
    const initial = flag({ enabled: false, is_active: false });
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response([initial]))));
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByText(initial.key)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /啟用功能/ }));
    expect(screen.getByRole("dialog")).toHaveTextContent("範圍：global（舊版 API）");
    expect(screen.getByRole("dialog")).toHaveTextContent("設定版本：未提供（舊版 global API）");
  });

  it("renders malformed scoped reads as errors, not legacy globals or command controls", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response([flag({ scope: "per-tenant" })]))));
    render(<FeatureFlagsAdminWorkspace />);
    expect(await screen.findByRole("alert")).toHaveTextContent("資料格式無效");
    expectNoFlagFacts();
  });
});

// Keep the original context/sequence fences on the scoped path as well.
describe("scoped command lifecycle isolation", () => {
  it.each(["approve", "enable", "disable"] as const)("ignores old %s success on changed context, A-B-A and unmount", async (command) => {
    for (const transition of ["changed", "A-B-A", "unmount"] as const) {
      const initial = scopedFlag({ enabled: command === "disable" });
      const pending = deferredRead();
      let reads = 0;
      const read = vi.fn((_url: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "POST") return pending.promise;
        reads += 1;
        return Promise.resolve(response([reads === 1 ? initial : scopedFlag({ configuration_version: 19, owner: "unit-new-context" })]));
      });
      vi.stubGlobal("fetch", read);
      const view = render(<FeatureFlagsAdminWorkspace baseUrl="/old" />);
      expect(await screen.findByText(initial.key)).toBeVisible();
      if (command === "approve") {
        fireEvent.click(screen.getByRole("button", { name: /簽核明細/ }));
        fireEvent.click(screen.getByRole("button", { name: "以目前登入帳號核准" }));
      } else fireEvent.click(screen.getByRole("button", { name: command === "disable" ? /緊急停用/ : /啟用功能/ }));
      confirmChosenCommand();
      if (transition === "unmount") view.unmount();
      else {
        view.rerender(<FeatureFlagsAdminWorkspace baseUrl="/new" />);
        expect(await screen.findByText("unit-new-context")).toBeVisible();
        if (transition === "A-B-A") {
          view.rerender(<FeatureFlagsAdminWorkspace baseUrl="/old" />);
          expect(await screen.findByText("unit-new-context")).toBeVisible();
        }
      }
      await act(async () => { pending.resolve(commandResponse({ ...initial, enabled: command !== "disable",
        configuration_version: command === "disable" ? 8 : 7, approved_by: ["unit-old-result"] })); });
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(screen.queryByText(/unit-old-result|Kill-Switch engaged:|Feature flag .* enabled!|已記錄目前登入帳號的核准/)).not.toBeInTheDocument();
      expect(read.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
      if (transition !== "unmount") {
        expect(screen.getByText("unit-new-context")).toBeVisible();
        expect(screen.getByText("設定版本：")).toHaveTextContent("設定版本：19");
      }
      view.unmount();
    }
  });
});
