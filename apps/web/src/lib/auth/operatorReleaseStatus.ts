import type { OperatorReleaseStatus } from "../../../features/operator/OperatorReleaseNotice";
import { resolveGoogleMetadataIdentityToken } from "./cloudRunIdentity";
import { readWebSession } from "./session";

const MODEL_LABELS: Record<string, string> = {
  avm: "AVM",
  forecastops: "ForecastOps",
  heatzone: "HeatZone",
  sitescore: "SiteScore",
};

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

/**
 * Server-only, read-only projection of the existing API readiness surface.
 * No business API/grant is needed by a pure administrator. Transport identity
 * stays on the server; raw readiness errors/credentials never enter page props.
 */
export async function readOperatorReleaseStatus(
  cookie: string | undefined,
  environment: NodeJS.ProcessEnv = process.env,
): Promise<OperatorReleaseStatus> {
  const declared = environment.ODP_RELEASE_PROFILE?.trim() || "full";
  const deployment = environment.ODP_DEPLOY_ENV?.trim();
  const profile = declared === "full" || (declared === "dev-admin" && deployment === "dev")
    ? declared : null;
  const unknown: OperatorReleaseStatus = { profile, models: "unknown", unavailableServices: [] };
  try {
    const session = await readWebSession(cookie, { environment });
    if (!session || !profile) return unknown;
    const base = new URL(environment.ODP_API_BASE_URL || "");
    const audience = environment.ODP_API_SERVICE_AUDIENCE?.trim();
    const sha = environment.ODAY_RELEASE_SHA?.trim();
    // Use only deployed, credential-free origins and exact candidate identities.
    if (base.protocol !== "https:" || base.username || base.password || base.search || base.hash
      || base.pathname !== "/" || !audience || !sha || !/^[0-9a-f]{40}$/.test(sha)
      || !["dev", "staging", "production"].includes(deployment || "")) return unknown;
    const token = await resolveGoogleMetadataIdentityToken(audience);
    const options: RequestInit = {
      headers: { accept: "application/json", "x-serverless-authorization": `Bearer ${token}` },
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(10_000),
    };
    const [readiness, version] = await Promise.all([
      fetch(new URL("/readiness", base), options),
      fetch(new URL("/platform/version", base), options),
    ]);
    if (!readiness.ok || !version.ok) return unknown;
    const identity = record(await version.json());
    const payload = record(await readiness.json());
    const details = record(payload.details);
    const runtimeProfile = record(details.releaseProfile);
    if (identity.release_sha !== sha || payload.status !== "ok"
      || details.deploymentMode !== deployment || details.requireLiveData !== true
      || runtimeProfile.valid !== true || runtimeProfile.name !== profile
      || runtimeProfile.modelReadinessClaimed !== (profile === "full")) return unknown;
    const models = record(details.models);
    const capabilities = record(models.capabilities);
    // Missing/malformed evidence is unknown, never a manufactured ready state.
    if (models.autoSeeded !== false || typeof models.productionBindingsReady !== "boolean"
      || Object.keys(MODEL_LABELS).some(service => {
        const capability = record(capabilities[service]);
        return typeof capability.available !== "boolean"
          || (capability.available === false && !capability.reasonCode);
      })) return unknown;
    if (models.productionBindingsReady === true
      && (models.mode !== "mlflow-production" || models.error
        || record(capabilities.forecastops).available !== true)) return unknown;
    if (models.productionBindingsReady === false
      && (!models.error || !Array.isArray(models.blockingReasons)
        || !models.blockingReasons.includes("PRODUCTION_MODEL_BINDINGS_UNVERIFIED"))) return unknown;
    return {
      profile,
      models: models.productionBindingsReady ? "ready" : "limited",
      unavailableServices: Object.entries(MODEL_LABELS)
        .filter(([service]) => record(capabilities[service]).available === false)
        .map(([, label]) => label),
    };
  } catch {
    // Includes session/metadata/HTTP/JSON failures: no raw error can leak secrets.
    return unknown;
  }
}
