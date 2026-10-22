import { afterEach, describe, expect, it, vi } from "vitest";
import { hasCurrentReviewDecisionIdentity, readReviewDecisionIdentity } from "../reviewDecisionAuthContext";
const identity = () => ({ actorSubjectId: "canonical-account", tenantId: "canonical-tenant", expiresAt: Math.floor(Date.now() / 1000) + 600 });
afterEach(() => { vi.unstubAllGlobals(); });
describe("durable native review actor and tenant consumer", () => {
  it("requires canonical actor/tenant/current expiry and accepts valid expiry renewal", () => {
    expect(hasCurrentReviewDecisionIdentity(identity())).toBe(true);
    expect(hasCurrentReviewDecisionIdentity({ ...identity(), expiresAt: identity().expiresAt + 100 })).toBe(true);
  });
  it.each([true, "9999999999", null, 1.5, 0, -1, NaN, Infinity])("refuses invalid expiry %s", (expiresAt) => {
    expect(hasCurrentReviewDecisionIdentity({ ...identity(), expiresAt })).toBe(false);
  });
  it.each(["actorSubjectId", "tenantId"])("does not grant a missing %s from display or storage", (field) => {
    const value: Record<string, unknown> = { ...identity(), subject: "display-account", tenant: "display-tenant" }; delete value[field];
    expect(hasCurrentReviewDecisionIdentity(value)).toBe(false);
  });
  it("an expired trusted context refuses even with unchanged actor and tenant", () => {
    expect(hasCurrentReviewDecisionIdentity({ ...identity(), expiresAt: Math.floor(Date.now() / 1000) - 1 })).toBe(false);
  });
  it.each(["http", "json"])("rejects a superseded identity read at its %s boundary", async (phase) => {
    let current = true;
    const fetchMock = vi.fn(async () => {
      const r = new Response(JSON.stringify(identity()), { status: 200 });
      if (phase === "http") current = false;
      else { const read = r.json.bind(r); r.json = async () => { const v = await read(); current = false; return v; }; }
      return r;
    });
    vi.stubGlobal("fetch", fetchMock);
    expect(await readReviewDecisionIdentity(() => current)).toBeNull();
    expect(fetchMock).toHaveBeenCalledWith("/auth/operator-context", { cache: "no-store", headers: { accept: "application/json" } });
  });
});
