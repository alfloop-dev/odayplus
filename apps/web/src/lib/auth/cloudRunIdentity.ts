export type ServiceIdentityTokenResolver = (
  audience: string,
) => Promise<string>;

const METADATA_IDENTITY_ENDPOINT =
  "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity";

export async function resolveGoogleMetadataIdentityToken(
  audience: string,
  fetchImpl: typeof fetch = fetch,
): Promise<string> {
  const normalizedAudience = audience.trim();
  if (!normalizedAudience) {
    throw new Error("Cloud Run service audience is required");
  }

  const endpoint = new URL(METADATA_IDENTITY_ENDPOINT);
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
