import { expect, test, type APIRequestContext } from "@playwright/test";

/** Explicit premise for the disposable local POC review flow only.
 * Both subjects below are synthetic header-trust test principals. Their real
 * admin HTTP commands are engineering inputs, never human release approvals.
 * Formal authentication and default-disabled operation policy stay in force.
 */
export async function enableSyntheticSiteReviewFlag(api: APIRequestContext, apiBaseURL: string) {
  const origin = new URL(apiBaseURL);
  if (origin.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(origin.hostname)) {
    throw new Error("Synthetic review flag setup requires the disposable loopback POC API");
  }
  test.info().annotations.push({
    type: "scope",
    description: "One SiteScore GO flag enabled by two synthetic local POC administrators through actual HTTP; no production or human approval.",
  });
  const path = `${origin.origin}/api/v1/admin/feature-flags/high_risk.sitescore.approve`;
  const actors = ["synthetic-review-flag-admin-a", "synthetic-review-flag-admin-b"];
  const proof = { reason: "Explicit disposable browser review fixture enablement", riskAcknowledged: true };
  const headers = (actor: string) => ({
    "x-subject-id": actor, "x-roles": "platform_admin", "x-tenant-id": "tenant-a",
    "x-correlation-id": `synthetic-review-flag-${actor}`,
  });
  for (const actor of actors) {
    const response = await api.post(`${path}/approve`, { headers: headers(actor), data: proof, maxRedirects: 0 });
    expect(response.status(), await response.text()).toBe(200);
  }
  const enabled = await api.post(`${path}/enable`, { headers: headers(actors[0]), data: proof, maxRedirects: 0 });
  expect(enabled.status(), await enabled.text()).toBe(200);
  const body = await enabled.json();
  expect(body.flag.enabled).toBe(true);
  expect(body.flag.approved_by).toEqual(expect.arrayContaining(actors));
}
