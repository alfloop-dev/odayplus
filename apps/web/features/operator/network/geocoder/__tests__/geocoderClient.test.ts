import { afterEach, describe, expect, it, vi } from "vitest";
import {
  isGeocoderConfigured,
  parseSearchPayload,
  resolveGeocoderUrl,
  searchAddress,
  unconfiguredGeocoderError,
} from "../geocoderClient";

const ENDPOINT = "https://geocoder.internal.test/geocode";

/** Minimal Response stand-in; only the fields the client actually reads. */
function jsonResponse(
  body: unknown,
  init: { ok?: boolean; status?: number; correlationId?: string } = {},
): Response {
  const status = init.status ?? 200;
  return {
    ok: init.ok ?? status < 400,
    status,
    headers: { get: (name: string) => (name === "X-Correlation-Id" ? init.correlationId ?? null : null) },
    json: async () => body,
  } as unknown as Response;
}

function brokenBodyResponse(status = 200): Response {
  return {
    ok: status < 400,
    status,
    headers: { get: () => null },
    json: async () => {
      throw new SyntaxError("Unexpected token < in JSON");
    },
  } as unknown as Response;
}

/** The gateway's live single-result shape (services/provider-gateway/app.py). */
const GATEWAY_PAYLOAD = {
  result: {
    latitude: 25.0478,
    longitude: 121.517,
    confidence: 0.98,
    precision: "rooftop",
    provider_id: "geocode.primary_api",
    city: "台北市",
    district: "中正區",
  },
  request_id: "place-abc",
  observed_at: "2026-08-08T10:00:00+00:00",
  upstream: "google-maps-geocoding",
};

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("endpoint resolution", () => {
  it("reports unconfigured when the env var is absent", () => {
    vi.stubEnv("NEXT_PUBLIC_ODP_GEOCODER_URL", "");
    expect(resolveGeocoderUrl()).toBeNull();
    expect(isGeocoderConfigured()).toBe(false);
  });

  it("treats a mock:// endpoint as unconfigured so fixtures cannot reach the UI", () => {
    vi.stubEnv("NEXT_PUBLIC_ODP_GEOCODER_URL", "mock://geocoder");
    expect(resolveGeocoderUrl()).toBeNull();
    expect(isGeocoderConfigured()).toBe(false);
  });

  it("accepts a real endpoint", () => {
    vi.stubEnv("NEXT_PUBLIC_ODP_GEOCODER_URL", ENDPOINT);
    expect(resolveGeocoderUrl()).toBe(ENDPOINT);
    expect(isGeocoderConfigured()).toBe(true);
  });
});

describe("searchAddress without a configured endpoint", () => {
  it("returns a structured error and never calls out", async () => {
    vi.stubEnv("NEXT_PUBLIC_ODP_GEOCODER_URL", "");
    const fetchImpl = vi.fn();

    const outcome = await searchAddress("新北市新莊區興德路100號", { fetchImpl });

    expect(fetchImpl).not.toHaveBeenCalled();
    expect(outcome.ok).toBe(false);
    if (outcome.ok) return;
    expect(outcome.error.code).toBe("ODP-GEOCODER-UNCONFIGURED");
    expect(outcome.error.retryable).toBe(false);
  });

  it("states in the copy that no estimated position is shown", () => {
    const error = unconfiguredGeocoderError();
    expect(error.nextAction).toContain("不會顯示模擬座標");
  });
});

describe("searchAddress transport", () => {
  it("posts the address with a correlation ID to the configured endpoint", async () => {
    vi.stubEnv("NEXT_PUBLIC_ODP_GEOCODER_URL", ENDPOINT);
    const fetchImpl = vi.fn(async () => jsonResponse(GATEWAY_PAYLOAD));

    const outcome = await searchAddress("台北市中正區信義路一段1號", {
      fetchImpl: fetchImpl as unknown as typeof fetch,
      correlationId: "corr-test-1",
    });

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(ENDPOINT);
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["X-Correlation-Id"]).toBe("corr-test-1");
    expect(JSON.parse(String(init.body))).toEqual({ address: "台北市中正區信義路一段1號" });
    expect(outcome.ok).toBe(true);
  });

  it("maps an upstream 502 onto a retryable error with no candidates", async () => {
    vi.stubEnv("NEXT_PUBLIC_ODP_GEOCODER_URL", ENDPOINT);
    const fetchImpl = vi.fn(async () => jsonResponse({ detail: "google status OVER_QUERY_LIMIT" }, { status: 502 }));

    const outcome = await searchAddress("台北市中正區信義路一段1號", {
      fetchImpl: fetchImpl as unknown as typeof fetch,
      correlationId: "corr-test-2",
    });

    expect(outcome.ok).toBe(false);
    if (outcome.ok) return;
    expect(outcome.error.code).toBe("ODP-GEOCODER-UPSTREAM");
    expect(outcome.error.retryable).toBe(true);
    // The server's own copy wins over anything invented client-side.
    expect(outcome.error.summary).toBe("google status OVER_QUERY_LIMIT");
    expect(outcome.error.correlationId).toBe("corr-test-2");
  });

  it("maps a 400 onto a non-retryable query error", async () => {
    vi.stubEnv("NEXT_PUBLIC_ODP_GEOCODER_URL", ENDPOINT);
    const fetchImpl = vi.fn(async () => jsonResponse({ detail: "address required" }, { status: 400 }));

    const outcome = await searchAddress("台北市中正區", {
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    expect(outcome.ok).toBe(false);
    if (outcome.ok) return;
    expect(outcome.error.code).toBe("ODP-GEOCODER-QUERY-INVALID");
    expect(outcome.error.retryable).toBe(false);
  });

  it("distinguishes an unreadable body from an empty result", async () => {
    vi.stubEnv("NEXT_PUBLIC_ODP_GEOCODER_URL", ENDPOINT);
    const fetchImpl = vi.fn(async () => brokenBodyResponse());

    const outcome = await searchAddress("台北市中正區信義路一段1號", {
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    expect(outcome.ok).toBe(false);
    if (outcome.ok) return;
    expect(outcome.error.code).toBe("ODP-GEOCODER-MALFORMED");
  });

  it("reports a network failure as an error, not as zero candidates", async () => {
    vi.stubEnv("NEXT_PUBLIC_ODP_GEOCODER_URL", ENDPOINT);
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });

    const outcome = await searchAddress("台北市中正區信義路一段1號", {
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    expect(outcome.ok).toBe(false);
    if (outcome.ok) return;
    expect(outcome.error.code).toBe("ODP-GEOCODER-NETWORK");
    expect(outcome.error.retryable).toBe(true);
  });

  it("reports an aborted request as a timeout", async () => {
    vi.stubEnv("NEXT_PUBLIC_ODP_GEOCODER_URL", ENDPOINT);
    const fetchImpl = vi.fn(async () => {
      const abort = new Error("aborted");
      abort.name = "AbortError";
      throw abort;
    });

    const outcome = await searchAddress("台北市中正區信義路一段1號", {
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    expect(outcome.ok).toBe(false);
    if (outcome.ok) return;
    expect(outcome.error.code).toBe("ODP-GEOCODER-TIMEOUT");
  });
});

describe("parseSearchPayload never fabricates a coordinate", () => {
  it("reads the gateway's single-result shape", () => {
    const result = parseSearchPayload(GATEWAY_PAYLOAD, "台北市中正區信義路一段1號", "corr-1");

    expect(result.candidates).toHaveLength(1);
    expect(result.rejectedRowCount).toBe(0);
    const [candidate] = result.candidates;
    expect(candidate.latitude).toBe(25.0478);
    expect(candidate.longitude).toBe(121.517);
    expect(candidate.confidence).toBe(0.98);
    expect(candidate.precision).toBe("rooftop");
    expect(candidate.provider).toBe("geocode.primary_api");
    expect(candidate.providerRequestId).toBe("place-abc");
    // The raw query is preserved verbatim for the audit trail.
    expect(candidate.addressRaw).toBe("台北市中正區信義路一段1號");
  });

  it("reads the multi-candidate shape and keeps provider order", () => {
    const result = parseSearchPayload(
      {
        results: [
          { latitude: 25.04, longitude: 121.51, confidence: 0.95, precision: "rooftop" },
          { latitude: 25.05, longitude: 121.52, confidence: 0.4, precision: "approximate" },
        ],
      },
      "台北市中正區信義路",
      "corr-2",
    );

    expect(result.candidates.map((item) => item.latitude)).toEqual([25.04, 25.05]);
    expect(result.candidates[0].candidateId).not.toBe(result.candidates[1].candidateId);
  });

  it("returns zero candidates for the gateway's ZERO_RESULTS empty object", () => {
    const result = parseSearchPayload({}, "查無此地址路", "corr-3");
    expect(result.candidates).toEqual([]);
    expect(result.rejectedRowCount).toBe(0);
  });

  it("drops and counts a row missing a longitude instead of defaulting it", () => {
    const result = parseSearchPayload(
      { results: [{ latitude: 25.04, confidence: 0.9, precision: "rooftop" }] },
      "台北市中正區信義路",
      "corr-4",
    );

    expect(result.candidates).toEqual([]);
    expect(result.rejectedRowCount).toBe(1);
  });

  it("drops a row whose coordinates are not finite numbers", () => {
    const result = parseSearchPayload(
      {
        results: [
          { latitude: null, longitude: 121.5 },
          { latitude: "25.04", longitude: "121.51" },
          { latitude: 25.04, longitude: 121.51, confidence: 0.9, precision: "rooftop" },
        ],
      },
      "台北市中正區信義路",
      "corr-5",
    );

    expect(result.candidates).toHaveLength(1);
    expect(result.rejectedRowCount).toBe(2);
  });

  it("leaves an absent confidence non-finite rather than defaulting it to a passing value", () => {
    const result = parseSearchPayload(
      { results: [{ latitude: 25.04, longitude: 121.51, precision: "rooftop" }] },
      "台北市中正區信義路",
      "corr-6",
    );

    expect(result.candidates).toHaveLength(1);
    expect(Number.isFinite(result.candidates[0].confidence)).toBe(false);
  });

  it("survives a non-object payload without inventing a result", () => {
    for (const payload of [null, "oops", 42, []]) {
      const result = parseSearchPayload(payload, "台北市中正區信義路", "corr-7");
      expect(result.candidates).toEqual([]);
    }
  });
});
