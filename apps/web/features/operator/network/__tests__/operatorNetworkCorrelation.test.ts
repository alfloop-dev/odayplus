import { afterEach, describe, expect, it, vi } from "vitest";
import { newCorrelationId } from "../operatorNetworkClient";
import { createOdpApiClient } from "@oday-plus/openapi-client";
import { intakeApi } from "../intake/intakeClient";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

afterEach(() => vi.unstubAllGlobals());

describe("Canonical event correlation UUIDs", () => {
  it("sends the valid correlation UUID through the real generated promotion client and preserves the idempotency key", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "10000000-0000-4000-8000-000000000001" });
    const fetchImpl = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ detail: "synthetic unavailable" }), { status: 503 }));
    const client = createOdpApiClient({ baseUrl: "https://example.invalid", fetchImpl });
    if (!client) throw new Error("Synthetic SDK client configuration is missing");
    await intakeApi.requestPromotion(client, "20000000-0000-4000-8000-000000000001", {
      target_format_code: "FORMAT-A", reason: "Synthetic transport validation", gate_snapshot_sha256: "a".repeat(64), risk_acknowledged: true,
    }, { idempotencyKey: "promotion-existing-intent-key", ifMatch: 'W/"7"' });
    expect(fetchImpl).toHaveBeenCalledOnce();
    const headers = new Headers(fetchImpl.mock.calls[0][1]?.headers);
    expect(headers.get("X-Correlation-Id")).toMatch(UUID_V4);
    expect(headers.get("Idempotency-Key")).toBe("promotion-existing-intent-key");
    expect(headers.get("If-Match")).toBe('W/"7"');
  });

  it("uses a UUID without the legacy corr prefix", () => {
    const value = "10000000-0000-4000-8000-000000000001";
    vi.stubGlobal("crypto", { randomUUID: () => value });
    expect(newCorrelationId()).toBe(value);
    expect(newCorrelationId()).toMatch(UUID_V4);
  });

  it("keeps the UUID version and variant when only getRandomValues is available", () => {
    vi.stubGlobal("crypto", { getRandomValues: (bytes: Uint8Array) => bytes.fill(255) });
    expect(newCorrelationId()).toBe("ffffffff-ffff-4fff-bfff-ffffffffffff");
    expect(newCorrelationId()).toMatch(UUID_V4);
  });

  it("does not send a non-UUID token when crypto is unavailable", () => {
    vi.stubGlobal("crypto", undefined);
    expect(() => newCorrelationId()).toThrow("Correlation UUID generation is unavailable");
  });
});
