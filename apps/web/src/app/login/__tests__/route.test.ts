import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { POST } from "../route";
import { MockIdentityStore, setIdentityStoreForTests } from "../../../lib/auth/identityStore";
import { MockSessionStore, setSessionStoreForTests } from "../../../lib/auth/sessionStore";
import { DEFAULT_LOGIN_THROTTLE_CONFIG, LoginThrottle, MockLoginThrottleStore, setLoginThrottleForTests } from "../../../lib/auth/loginThrottle";

const password = "Old-offline-password-729!";
const entries = [{ accountId: "offline-account", tenantId: "offline-tenant", username: "offline-user",
  email: "offline@example.invalid", status: "active" as const, password }];

function request() {
  return new NextRequest("https://ops.oday.plus/login", { method: "POST", headers: {
    origin: "https://ops.oday.plus", "content-type": "application/json", accept: "application/json",
  }, body: JSON.stringify({ username: "offline-user", password, verifiedCredentialRevision: "999" }) });
}

function install() {
  vi.stubEnv("ODP_WEB_SESSION_SECRET", "offline-test-session-key-at-least-32-bytes");
  const identity = new MockIdentityStore(entries);
  const sessions = new MockSessionStore();
  setIdentityStoreForTests(identity);
  setSessionStoreForTests(sessions);
  setLoginThrottleForTests(new LoginThrottle(new MockLoginThrottleStore(), DEFAULT_LOGIN_THROTTLE_CONFIG, "offline-throttle-pepper"));
  return { identity, sessions };
}

afterEach(() => {
  setIdentityStoreForTests(undefined);
  setSessionStoreForTests(undefined);
  setLoginThrottleForTests(undefined);
  vi.unstubAllEnvs();
});

describe("mounted login verified-revision handoff", () => {
  it("passes only the verified server revision, never a submitted revision or response credential", async () => {
    const { sessions } = install();
    const create = vi.spyOn(sessions, "createSession");
    const response = await POST(request());
    expect(response.status).toBe(200);
    expect(create).toHaveBeenCalledWith(expect.objectContaining({ verifiedCredentialRevision: "1" }));
    expect(response.headers.get("cache-control")).toBe("no-store");
    const body = await response.text();
    expect(body).not.toContain(password);
    expect(body).not.toContain("Revision");
    expect(body).not.toContain("phc");
  });

  it("does not seal a cookie after stale verification/session-fence refusal", async () => {
    const { identity, sessions } = install();
    vi.spyOn(identity, "verifyPassword").mockImplementation(async () => {
      await identity.changePassword("offline-account", "rotated-offline-password");
      return { valid: true, newHash: "stale-rehash" };
    });
    const create = vi.spyOn(sessions, "createSession").mockImplementation(async (params) => {
      if (params.verifiedCredentialRevision !== (await identity.getPasswordCredential(params.accountId))?.credentialRevision) {
        throw new Error("SESSION_CREDENTIAL_REVISION_REFUSED");
      }
      throw new Error("unexpected matching revision in stale test");
    });
    const response = await POST(request());
    expect(create).toHaveBeenCalledWith(expect.objectContaining({ verifiedCredentialRevision: "1" }));
    expect(response.status).toBe(503);
    expect(response.headers.get("set-cookie")).toBeNull();
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.text()).not.toMatch(/Revision|stale-rehash|Old-offline-password/);
    expect((await identity.getPasswordCredential("offline-account"))?.phcHash).toBe("rotated-offline-password");
  });
});
