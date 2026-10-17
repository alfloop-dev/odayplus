import type { Page } from "@playwright/test";

/** Select a persona preference for the explicitly granted synthetic QA user.
 * This grants no platform role: the bootstrap API still validates its claims.
 */
export async function primeOperatorPersona(page: Page, roleId: string) {
  await page.addInitScript((role) => {
    // Test-only trace evidence for the observed URL=Network / view=Today
    // failure. Preserve the native storage operation and original assertions;
    // traces retain which application caller rewrites the workspace choice.
    const nativeSetItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function (this: Storage, key: string, value: string) {
      const result = Reflect.apply(nativeSetItem, this, arguments);
      if (this === window.sessionStorage && key === "oday.operator.workspace") {
        const workspaceIds = ["today", "store", "growth", "network", "govern"];
        const requested = new URL(window.location.href).searchParams.get("ws");
        console.info("OperatorWorkspacePreference", JSON.stringify({
          at: performance.now(),
          storedWorkspace: workspaceIds.includes(value) ? value : "unrecognized",
          locationWorkspace: requested === null ? null : workspaceIds.includes(requested) ? requested : "unrecognized",
          caller: (new Error().stack ?? "").split("\n").slice(2, 7).map((line) => {
            const location = line.trim().match(/^at [^()]* \((https?:\/\/[^()\s]+):(\d+):(\d+)\)$/)
              ?? line.trim().match(/^at (https?:\/\/[^()\s]+):(\d+):(\d+)$/);
            if (!location) return "<non-application frame>";
            try {
              const url = new URL(location[1]);
              return url.origin === window.location.origin && url.pathname.startsWith("/_next/static/")
                ? `${url.pathname}:${location[2]}:${location[3]}`
                : "<non-application frame>";
            } catch {
              return "<non-application frame>";
            }
          }),
        }));
      }
      return result;
    };
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
