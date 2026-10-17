import "@testing-library/jest-dom/vitest";
import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { normalizeNotificationPreferences, ShellNotificationPreferences, type ShellNotificationPreferencesProps } from "../ShellNotificationPreferences";

// Transport/session-pin controls only; not a production account or delivery receipt.
const initial = { channels: { inApp: true, email: true, push: false }, severityFloor: "info", digest: "owner-current-digest" };
function envelope(preferences = initial, allowed = true) {
  return { preferences, isDefault: true, severityLevels: ["info", "warning", "critical"], actions: { update: { allowed } } };
}
function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status }); }
function receipt(preferences = initial, overrides: Record<string, unknown> = {}) {
  return { preferences, auditEvent: { auditEventId: "real-audit-1", category: "shell.notification", action: "update_preferences",
    targetType: "notification_preferences", actorSubjectId: "subject-A", ...overrides } };
}
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
function props(overrides: Partial<ShellNotificationPreferencesProps> = {}): ShellNotificationPreferencesProps {
  return { scopeKey: "A-epoch1", expectedSubjectId: "subject-A", headers: { "X-Operator-Role": "ops-lead" },
    createAuthenticatedReadGuard: (current) => async () => current(), ...overrides };
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function changeEmail() { fireEvent.click(await screen.findByLabelText("email")); }
function save() { fireEvent.click(screen.getByRole("button", { name: "儲存通知偏好" })); }

describe("personal notification preferences", () => {
  it("waits for real preferences and names the server default without inventing a delivery configuration", async () => {
    const pending = deferred<Response>(); vi.stubGlobal("fetch", vi.fn(() => pending.promise));
    render(<ShellNotificationPreferences {...props()} />);
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("正在讀取");
    await act(async () => pending.resolve(response(envelope())));
    expect(await screen.findByLabelText("email")).toBeChecked();
    expect(screen.getByText("尚未保存個人偏好，以下為服務提供的預設值。")).toBeVisible();
    expect(screen.getByText(/owner-current-digest/)).toBeVisible();
    expect(screen.getByRole("button", { name: "儲存通知偏好" })).toBeDisabled();
  });

  it("sends original PUT preferences without an actor and confirms audit plus a fresh server read before success", async () => {
    const put = deferred<Response>(); const reread = deferred<Response>(); let reads = 0;
    const fetcher = vi.fn((_url: string, init?: RequestInit) => init?.method === "PUT" ? put.promise
      : ++reads === 1 ? Promise.resolve(response(envelope())) : reread.promise);
    vi.stubGlobal("fetch", fetcher); render(<ShellNotificationPreferences {...props()} />);
    await changeEmail(); save();
    await waitFor(() => expect(fetcher.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(true));
    const submitted = fetcher.mock.calls.find(([, init]) => init?.method === "PUT")![1]!;
    const desired = { ...initial, channels: { ...initial.channels, email: false } };
    expect(JSON.parse(String(submitted.body))).toEqual(desired);
    expect(Object.keys(JSON.parse(String(submitted.body)))).toEqual(["channels", "severityFloor", "digest"]);
    expect(submitted.credentials).toBe("include");
    expect((submitted.headers as Record<string, string>)["Idempotency-Key"]).toBeTruthy();
    expect(screen.queryByText("通知偏好已保存並向後端重新核對。")).not.toBeInTheDocument();
    await act(async () => put.resolve(response(receipt(desired))));
    expect(screen.queryByText("通知偏好已保存並向後端重新核對。")).not.toBeInTheDocument();
    await act(async () => reread.resolve(response(envelope(desired))));
    expect(await screen.findByText("通知偏好已保存並向後端重新核對。")).toBeVisible();
    expect(screen.getByLabelText("email")).not.toBeChecked();
    expect(screen.queryByText("有尚未儲存的變更。")).not.toBeInTheDocument();
  });

  it.each([403, 503])("keeps draft and the same idempotency key after HTTP %s instead of showing success", async (status) => {
    const keys: string[] = [];
    vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => {
      if (init?.method === "PUT") { keys.push((init.headers as Record<string, string>)["Idempotency-Key"]); return Promise.resolve(response({}, status)); }
      return Promise.resolve(response(envelope()));
    }));
    render(<ShellNotificationPreferences {...props()} />); await changeEmail(); save();
    expect(await screen.findByRole("alert")).toHaveTextContent(`HTTP ${status}`);
    expect(screen.getByLabelText("email")).not.toBeChecked(); save();
    await waitFor(() => expect(keys).toHaveLength(2)); expect(keys[1]).toBe(keys[0]);
    expect(screen.queryByText("通知偏好已保存並向後端重新核對。")).not.toBeInTheDocument();
  });

  it.each(["missing hint", "explicit denial"])("keeps %s read-only and never sends PUT", async (kind) => {
    const value = kind === "missing hint" ? { ...envelope(), actions: undefined } : envelope(initial, false);
    const fetcher = vi.fn(() => Promise.resolve(response(value))); vi.stubGlobal("fetch", fetcher);
    render(<ShellNotificationPreferences {...props()} />);
    expect(await screen.findByLabelText("email")).toBeDisabled();
    expect(screen.getByRole("button", { name: "儲存通知偏好" })).toBeDisabled();
    expect(fetcher.mock.calls).toHaveLength(1);
  });

  it.each(["wrong actor", "seed root", "fixture metadata", "missing audit"])("rejects a %s command result while retaining proof input", async (kind) => {
    const desired = { ...initial, channels: { ...initial.channels, email: false } };
    const value = kind === "wrong actor" ? receipt(desired, { actorSubjectId: "subject-other" })
      : kind === "seed root" ? { ...receipt(desired), source: "seed-r4" }
        : kind === "fixture metadata" ? { ...receipt(desired), metadata: { fixture: true } }
          : { preferences: desired };
    vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => Promise.resolve(response(init?.method === "PUT" ? value : envelope()))));
    render(<ShellNotificationPreferences {...props()} />); await changeEmail(); save();
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.getByLabelText("email")).not.toBeChecked();
    expect(screen.queryByText("通知偏好已保存並向後端重新核對。")).not.toBeInTheDocument();
  });

  it("keeps unconfirmed input when a valid receipt is followed by different backend values", async () => {
    const desired = { ...initial, channels: { ...initial.channels, email: false } };
    vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => Promise.resolve(response(init?.method === "PUT" ? receipt(desired) : envelope()))));
    render(<ShellNotificationPreferences {...props()} />); await changeEmail(); save();
    expect(await screen.findByRole("alert")).toHaveTextContent("目前後端值不同");
    expect(screen.getByLabelText("email")).not.toBeChecked();
    expect(screen.queryByText("通知偏好已保存並向後端重新核對。")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "還原為後端值" }));
    expect(screen.getByLabelText("email")).toBeChecked();
  });

  it.each(["HTTP", "JSON", "failure"])("ignores an old account's late %s read after the current account is ready", async (phase) => {
    const pending = deferred<Response>(); const json = deferred<unknown>(); let reads = 0;
    const jsonStarted = vi.fn(() => json.promise);
    vi.stubGlobal("fetch", vi.fn(() => ++reads === 1
      ? phase === "JSON" ? Promise.resolve({ ok: true, json: jsonStarted } as unknown as Response) : pending.promise
      : Promise.resolve(response(envelope({ ...initial, digest: "current-B-digest" })))));
    const current = props(); const view = render(<ShellNotificationPreferences {...current} />);
    await waitFor(() => expect(phase === "JSON" ? jsonStarted.mock.calls.length : reads).toBe(1));
    view.rerender(<ShellNotificationPreferences {...current} scopeKey="B-epoch2" expectedSubjectId="subject-B" />);
    expect(await screen.findByText(/current-B-digest/)).toBeVisible();
    await act(async () => { if (phase === "JSON") json.resolve(envelope());
      else pending.resolve(response(envelope(), phase === "failure" ? 503 : 200)); });
    expect(screen.getByText(/current-B-digest/)).toBeVisible();
    expect(screen.queryByText(/owner-current-digest/)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it.each(["success", "failure"])("does not apply an old PUT %s through an A-B-A account epoch", async (result) => {
    const pending = deferred<Response>(); let puts = 0;
    vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => {
      if (init?.method === "PUT") { ++puts; return pending.promise; }
      return Promise.resolve(response(envelope()));
    }));
    const current = props(); const view = render(<ShellNotificationPreferences {...current} />);
    await changeEmail(); save(); await waitFor(() => expect(puts).toBe(1));
    view.rerender(<ShellNotificationPreferences {...current} scopeKey="B-epoch2" expectedSubjectId="subject-B" />);
    expect(await screen.findByLabelText("email")).toBeChecked();
    view.rerender(<ShellNotificationPreferences {...current} scopeKey="A-epoch3" />);
    expect(await screen.findByLabelText("email")).toBeChecked();
    await act(async () => pending.resolve(response(receipt({ ...initial, channels: { ...initial.channels, email: false } }), result === "failure" ? 503 : 200)));
    expect(screen.getByLabelText("email")).toBeChecked(); expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("通知偏好已保存並向後端重新核對。")).not.toBeInTheDocument();
  });

  it("sends no preference data request after a refused session pin", async () => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    render(<ShellNotificationPreferences {...props({ createAuthenticatedReadGuard: () => async () => false })} />);
    await act(async () => {}); expect(fetcher).not.toHaveBeenCalled();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("retains explicit POC provenance labeling and supports StrictMode replay", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response({ ...envelope(), source: "seed-r4" }))));
    render(<StrictMode><ShellNotificationPreferences {...props({ allowFixtureData: true })} /></StrictMode>);
    expect(await screen.findByLabelText("email")).toBeChecked();
    expect(screen.getByText("POC：示範模式，不作正式個人設定證明。")).toBeVisible();
  });

  it("does not coerce malformed channel booleans or invent a missing severity catalog", () => {
    expect(() => normalizeNotificationPreferences({ ...envelope(), preferences: { ...initial, channels: { email: "false" } } })).toThrow();
    expect(() => normalizeNotificationPreferences({ ...envelope(), severityLevels: [] })).toThrow();
    expect(normalizeNotificationPreferences({ ...envelope(), actions: undefined }).canUpdate).toBe(false);
  });
});
