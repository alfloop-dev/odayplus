import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readOperatorReleaseStatus } from "../operatorReleaseStatus";
import { readWebSession } from "../session";
import { resolveGoogleMetadataIdentityToken } from "../cloudRunIdentity";

vi.mock("../session", () => ({ readWebSession: vi.fn() }));
vi.mock("../cloudRunIdentity", () => ({ resolveGoogleMetadataIdentityToken: vi.fn() }));

const sha = "a".repeat(40);
const env = {
  ODP_RELEASE_PROFILE: "dev-admin", ODP_DEPLOY_ENV: "dev", ODAY_RELEASE_SHA: sha,
  ODP_API_BASE_URL: "https://private-api.invalid", ODP_API_SERVICE_AUDIENCE: "https://stable-api.invalid",
};
const session = { kind: "web-session" as const, subject: "sole-admin", issuedAt: 1, expiresAt: 9999999999 };
let payload: ReturnType<typeof readiness>;
let version: { release_sha: string };
let readinessStatus: number;
let fetchMock: ReturnType<typeof vi.fn>;

function readiness(profile = "dev-admin", ready = false) {
  return {
    status: "ok",
    details: {
      deploymentMode: "dev", requireLiveData: true,
      releaseProfile: { name: profile, valid: true, modelReadinessClaimed: profile === "full" },
      models: {
        autoSeeded: false, productionBindingsReady: ready,
        mode: ready ? "mlflow-production" : "mlflow-production-unverified",
        error: ready ? null : "binding absent; sensitive raw details must not be projected",
        blockingReasons: ready ? [] : ["PRODUCTION_MODEL_BINDINGS_UNVERIFIED"],
        capabilities: {
          forecastops: { available: ready, reasonCode: ready ? null : "PRODUCTION_BINDING_NOT_RESOLVED" },
          avm: { available: false, reasonCode: "DATA_MATURITY_DISABLED" },
          heatzone: { available: false, reasonCode: "DATA_MATURITY_DISABLED" },
          sitescore: { available: false, reasonCode: "DATA_MATURITY_DISABLED" },
        },
      },
    },
  };
}

beforeEach(() => {
  payload = readiness();
  version = { release_sha: sha };
  readinessStatus = 200;
  vi.mocked(readWebSession).mockResolvedValue(session);
  vi.mocked(resolveGoogleMetadataIdentityToken).mockResolvedValue("server-only-transport-token");
  fetchMock = vi.fn(async (input: URL, _options: RequestInit) => new Response(JSON.stringify(
    input.pathname === "/readiness" ? payload : version,
  ), { status: input.pathname === "/readiness" ? readinessStatus : 200 }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { vi.resetAllMocks(); vi.unstubAllGlobals(); });

const unknown = { profile: "dev-admin", models: "unknown", unavailableServices: [] };

describe("server-side operator release status projection", () => {
  it("reads real readiness with transport identity, binds SHA/profile/env, exposes no raw errors or tokens", async () => {
    const status = await readOperatorReleaseStatus("sealed-cookie", env);
    expect(status).toEqual({ profile: "dev-admin", models: "limited", unavailableServices: ["AVM", "ForecastOps", "HeatZone", "SiteScore"] });
    expect(readWebSession).toHaveBeenCalledWith("sealed-cookie", { environment: env });
    expect(resolveGoogleMetadataIdentityToken).toHaveBeenCalledWith(env.ODP_API_SERVICE_AUDIENCE);
    expect(fetchMock.mock.calls.map(([url]) => url.pathname)).toEqual(["/readiness", "/platform/version"]);
    for (const [, options] of fetchMock.mock.calls) {
      expect(options).toMatchObject({ cache: "no-store", redirect: "error", headers: {
        accept: "application/json", "x-serverless-authorization": "Bearer server-only-transport-token",
      } });
      expect(options.headers.authorization).toBeUndefined();
      expect(options.headers["x-operator-role"]).toBeUndefined();
    }
    expect(JSON.stringify(status)).not.toMatch(/sensitive|server-only|sealed-cookie/);
  });

  it.each(["full", "dev-admin"])("reports actual ready models under %s without claiming every capability available", async profile => {
    payload = readiness(profile, true);
    expect(await readOperatorReleaseStatus("cookie", { ...env, ODP_RELEASE_PROFILE: profile })).toEqual({
      profile, models: "ready", unavailableServices: ["AVM", "HeatZone", "SiteScore"],
    });
  });

  it("keeps default full scope and reports actual unresolved models", async () => {
    payload = readiness("full");
    expect(await readOperatorReleaseStatus("cookie", { ...env, ODP_RELEASE_PROFILE: undefined })).toMatchObject({ profile: "full", models: "limited" });
  });

  it.each(["staging", "production"])("does not admit admin scope in %s", async deployment => {
    expect(await readOperatorReleaseStatus("cookie", { ...env, ODP_DEPLOY_ENV: deployment })).toEqual({ ...unknown, profile: null });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    { ODP_RELEASE_PROFILE: "bogus" }, { ODP_API_BASE_URL: "" },
    { ODP_API_BASE_URL: "http://private-api.invalid" }, { ODP_API_BASE_URL: "https://user:secret@private-api.invalid" },
    { ODP_API_SERVICE_AUDIENCE: "" }, { ODAY_RELEASE_SHA: "local" },
  ])("refuses absent/unsafe runtime binding before network calls: %j", async change => {
    expect((await readOperatorReleaseStatus("cookie", { ...env, ...change })).models).toBe("unknown");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(resolveGoogleMetadataIdentityToken).not.toHaveBeenCalled();
  });

  it.each([null, "unavailable"])("does not query runtime without a resolvable durable session (%s)", async mode => {
    if (mode === null) vi.mocked(readWebSession).mockResolvedValue(null);
    else vi.mocked(readWebSession).mockRejectedValue(new Error("secret-database-url"));
    expect(await readOperatorReleaseStatus("cookie", env)).toEqual(unknown);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    "profile", "invalid_profile", "claim", "deployment", "live", "sha", "surrogate", "missing_capability", "missing_reason", "missing_binding", "ready_unverified", "ready_error", "ready_forecast_absent", "no_limitation_error", "no_limitation_reason",
  ])("does not claim status with mismatched/malformed evidence (%s)", async kind => {
    const details = payload.details;
    if (kind === "profile") details.releaseProfile.name = "full";
    if (kind === "invalid_profile") details.releaseProfile.valid = false;
    if (kind === "claim") details.releaseProfile.modelReadinessClaimed = true;
    if (kind === "deployment") details.deploymentMode = "production";
    if (kind === "live") details.requireLiveData = false;
    if (kind === "sha") version.release_sha = "b".repeat(40);
    if (kind === "surrogate") details.models.autoSeeded = true;
    if (kind === "missing_capability") delete (details.models.capabilities as Record<string, unknown>).avm;
    if (kind === "missing_reason") details.models.capabilities.avm.reasonCode = "";
    if (kind === "missing_binding") delete (details.models as Partial<typeof details.models>).productionBindingsReady;
    if (kind.startsWith("ready_")) {
      details.models.productionBindingsReady = true;
      details.models.capabilities.forecastops.available = true;
      details.models.mode = "mlflow-production";
      details.models.error = null;
      if (kind === "ready_unverified") details.models.mode = "mlflow-production-unverified";
      if (kind === "ready_error") details.models.error = "unresolved";
      if (kind === "ready_forecast_absent") details.models.capabilities.forecastops.available = false;
    }
    if (kind === "no_limitation_error") details.models.error = null;
    if (kind === "no_limitation_reason") details.models.blockingReasons = [];
    expect(await readOperatorReleaseStatus("cookie", env)).toEqual(unknown);
  });

  it.each(["http", "metadata", "network", "json"])("shows unknown on %s failure without exposing raw errors", async kind => {
    if (kind === "http") readinessStatus = 503;
    if (kind === "metadata") vi.mocked(resolveGoogleMetadataIdentityToken).mockRejectedValue(new Error("secret token"));
    if (kind === "network") fetchMock.mockRejectedValue(new Error("secret URL"));
    if (kind === "json") fetchMock.mockResolvedValue(new Response("not json"));
    expect(await readOperatorReleaseStatus("cookie", env)).toEqual(unknown);
  });
});
