export type ServiceIdentityTokenResolver = (
  audience: string,
) => Promise<string>;

const METADATA_IDENTITY_ENDPOINT =
  "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity";

export async function resolveGoogleMetadataIdentityToken(
  audience: string,
  fetchImpl: typeof fetch = fetch,
  environment: Record<string, string | undefined> = process.env,
): Promise<string> {
  const normalizedAudience = audience.trim();
  if (!normalizedAudience) {
    throw new Error("Cloud Run service audience is required");
  }

  const endpoint = new URL(METADATA_IDENTITY_ENDPOINT);
  const metadataHost = environment.GCE_METADATA_HOST?.trim();
  if (metadataHost) {
    // Keep the same metadata protocol and resolver. An explicit isolated
    // transport must be a literal loopback host; never fall back on failure.
    if (!/^(?:127\.0\.0\.1|\[::1\])(?::[1-9]\d{0,4})?$/.test(metadataHost)) {
      throw new Error("Metadata override must be a literal loopback host with a valid port");
    }
    try {
      endpoint.host = new URL(`http://${metadataHost}`).host;
    } catch {
      throw new Error("Metadata override must be a literal loopback host with a valid port");
    }
  }
  endpoint.searchParams.set("audience", normalizedAudience);
  endpoint.searchParams.set("format", "full");
  const response = await fetchImpl(endpoint, {
    method: "GET",
    headers: { "metadata-flavor": "Google" },
    cache: "no-store",
    signal: AbortSignal.timeout(3_000),
  });
  if (!response.ok) {
    throw new Error(`Metadata identity endpoint returned ${response.status}`);
  }

  const token = (await response.text()).trim();
  if (token.split(".").length !== 3) {
    throw new Error("Metadata identity endpoint returned an invalid token");
  }
  return token;
}
