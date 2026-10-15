import type { Page } from "@playwright/test";

/** Select a persona preference for the explicitly granted synthetic QA user.
 * This grants no platform role: the bootstrap API still validates its claims.
 */
export async function primeOperatorPersona(page: Page, roleId: string) {
  await page.addInitScript((role) => {
    if (!sessionStorage.getItem("oday.operator.role")) sessionStorage.setItem("oday.operator.role", role);
  }, roleId);
}

export async function openOperatorWorkspace(page: Page, workspace: string) {
  const roleId = workspace === "growth" ? "marketing-manager" : workspace === "network" ? "expansion-manager" : "ops-lead";
  // Keep one browser context while measuring several personas/workspaces.
  if (page.url() === "about:blank") await page.goto("/operator");
  await page.evaluate((role) => sessionStorage.setItem("oday.operator.role", role), roleId);
  await page.goto(`/operator?ws=${workspace}`);
}
