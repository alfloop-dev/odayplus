import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OperatorAdminConsole } from "../OperatorAdminConsole";
import { OperatorConsole } from "../OperatorConsole";
import { OperatorPasswordChange } from "../OperatorPasswordChange";
import { classifyAccessDenial, signOutOperator } from "../operatorSession";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function urlOf(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
}

const originalLocation = window.location;
let assign: ReturnType<typeof vi.fn>;

beforeEach(() => {
  window.sessionStorage.clear();
  assign = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...originalLocation, assign },
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
});

describe("signOutOperator", () => {
  it("revokes through /auth/logout and only then leaves for /login", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(200, { ok: true }));
    const navigate = vi.fn();

    await expect(signOutOperator(fetchMock, navigate)).resolves.toEqual({ ok: true });

    expect(fetchMock).toHaveBeenCalledWith("/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: { accept: "application/json" },
    });
    expect(navigate).toHaveBeenCalledWith("/login");
  });

  it("never claims success when revocation was not durable", async () => {
    const navigate = vi.fn();

    await expect(
      signOutOperator(vi.fn().mockResolvedValue(json(503, { error: { code: "WEB_AUTH_UNAVAILABLE" } })), navigate),
    ).resolves.toEqual({ ok: false, status: 503 });
    await expect(signOutOperator(vi.fn().mockRejectedValue(new Error("offline")), navigate)).resolves.toEqual({
      ok: false,
      status: null,
    });
    expect(navigate).not.toHaveBeenCalled();
  });

  it("classifies API refusals", async () => {
    await expect(classifyAccessDenial(json(403, { detail: "PASSWORD_CHANGE_REQUIRED" }))).resolves.toBe(
      "password_change_required",
    );
    await expect(classifyAccessDenial(json(403, { detail: "role does not permit view" }))).resolves.toBe("forbidden");
    await expect(classifyAccessDenial(json(401, { detail: "no_credentials" }))).resolves.toBe("unauthenticated");
    await expect(classifyAccessDenial(json(500, {}))).resolves.toBeNull();
  });
});

describe("OperatorConsole session wiring", () => {
  it("Logout calls the real sign-out route instead of a POC toast", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (urlOf(input) === "/auth/logout") return json(200, { ok: true });
      return json(403, { detail: "role does not permit view on operator_console" });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    fireEvent.click(screen.getByRole("button", { name: "Logout" }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login"));
    expect(fetchMock.mock.calls.some(([input]) => urlOf(input) === "/auth/logout")).toBe(true);
    expect(screen.queryByText(/尚未串接登入服務/)).not.toBeInTheDocument();
  });

  it("reports a failed sign-out and stays on the page", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        urlOf(input) === "/auth/logout"
          ? json(503, { error: { code: "WEB_AUTH_UNAVAILABLE" } })
          : json(403, { detail: "forbidden" }),
      ),
    );

    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    fireEvent.click(screen.getByRole("button", { name: "Logout" }));

    expect(await screen.findByText(/登出失敗/)).toBeInTheDocument();
    expect(assign).not.toHaveBeenCalled();
  });

  it("sends a session with a pending first-login rotation to the password page", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(403, { detail: "PASSWORD_CHANGE_REQUIRED" })));

    render(<OperatorConsole searchParams={{ ws: "today" }} />);

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/operator?view=password"));
  });

  it("points a pure administrator from the business shell to the admin console", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(403, { detail: "role does not permit view" })));

    render(<OperatorConsole searchParams={{ ws: "today" }} />);

    const gate = await screen.findByTestId("operator-data-unavailable");
    await waitFor(() => expect(gate).toHaveTextContent("管理後台"));
    expect(screen.getByTestId("operator-admin-link")).toHaveAttribute("href", "/operator?view=admin");
  });
});

describe("OperatorAdminConsole", () => {
  const adminUser = {
    subject_id: "5f0c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b",
    username: "first.admin",
    email: "first.admin@example.invalid",
    name: "First Admin",
    roles: ["platform_admin"],
    scope: { tenant_id: "0b5e8f0e-9c55-4f0e-8a51-0d7f1c2a3b4c", brand_ids: [], region_ids: [], store_ids: [], clearance: "CONFIDENTIAL" },
    attributes: { identity_source: "identity.accounts" },
    status: "active",
  };

  it("renders identity user administration for a user:view session without the business shell", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = urlOf(input);
      if (url === "/api/v1/operator/users") return json(200, { users: [adminUser], count: 1 });
      if (url === "/api/v1/operator/users/roles") return json(200, { roles: [], count: 0 });
      if (url === "/api/v1/operator/users/audit-trail") return json(200, { events: [], count: 0 });
      throw new Error(`unexpected request ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<OperatorAdminConsole />);

    const row = await screen.findByTestId(`user-row-${adminUser.subject_id}`);
    expect(row).toHaveTextContent("First Admin");
    expect(row).toHaveTextContent("first.admin@example.invalid");
    expect(fetchMock.mock.calls.some(([input]) => urlOf(input) === "/api/v1/operator/bootstrap")).toBe(false);
  });

  it("routes a pending password rotation to the password page", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(403, { detail: "PASSWORD_CHANGE_REQUIRED" })));

    render(<OperatorAdminConsole />);

    const link = await screen.findByRole("link", { name: "前往變更密碼" });
    expect(link).toHaveAttribute("href", "/operator?view=password");
  });

  it("refuses an account without user administration", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(403, { detail: "role does not permit view on user" })));

    render(<OperatorAdminConsole />);

    expect(await screen.findByText(/需要 platform_admin/)).toBeInTheDocument();
  });

  it("signs out through the real route", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) =>
      urlOf(input) === "/auth/logout" ? json(200, { ok: true }) : json(401, { detail: "no_credentials" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<OperatorAdminConsole />);
    fireEvent.click(screen.getByRole("button", { name: "Logout" }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login"));
  });
});

describe("OperatorPasswordChange", () => {
  function fill(current: string, next: string, confirm: string) {
    fireEvent.change(screen.getByLabelText("目前密碼"), { target: { value: current } });
    fireEvent.change(screen.getByLabelText("新密碼"), { target: { value: next } });
    fireEvent.change(screen.getByLabelText("確認新密碼"), { target: { value: confirm } });
    fireEvent.click(screen.getByRole("button", { name: "變更密碼" }));
  }

  it("rotates through /auth/password and continues to the admin console", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const navigate = vi.fn();

    render(<OperatorPasswordChange navigate={navigate} />);
    fill("one-time-secret-value", "a-new-personal-passphrase", "a-new-personal-passphrase");

    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/operator?view=admin"));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/auth/password");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      currentPassword: "one-time-secret-value",
      newPassword: "a-new-personal-passphrase",
    });
  });

  it("does not submit mismatched passwords", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<OperatorPasswordChange navigate={vi.fn()} />);
    fill("one-time-secret-value", "a-new-personal-passphrase", "different-passphrase-x");

    expect(await screen.findByText("兩次輸入的新密碼不一致。")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not submit when new password equals current password", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<OperatorPasswordChange navigate={vi.fn()} />);
    fill("same-password-1234", "same-password-1234", "same-password-1234");

    expect(await screen.findByText("新密碼不得與目前密碼相同。")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not submit when new password is NFKC equivalent to current password", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<OperatorPasswordChange navigate={vi.fn()} />);
    fill("same-password-1234", "ｓame-password-1234", "ｓame-password-1234");

    expect(await screen.findByText("新密碼不得與目前密碼相同。")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows the server refusal and stays on the form", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(json(401, { error: { code: "AUTH_INVALID_CREDENTIALS", summary: "x" } })),
    );
    const navigate = vi.fn();

    render(<OperatorPasswordChange navigate={navigate} />);
    fill("wrong-current-password", "a-new-personal-passphrase", "a-new-personal-passphrase");

    expect(await screen.findByText("目前密碼不正確。")).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("shows server policy violation refusal and stays on the form", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        json(400, {
          error: {
            code: "AUTH_PASSWORD_POLICY_VIOLATION",
            summary: "New password must be different from current password.",
          },
        }),
      ),
    );
    const navigate = vi.fn();

    render(<OperatorPasswordChange navigate={navigate} />);
    fill("current-pass-1234", "a-new-pass-1234", "a-new-pass-1234");

    expect(await screen.findByText("新密碼不符合安全政策或與目前密碼相同。")).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });
});
