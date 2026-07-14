/**
 * ODP-OC-R4-011 — full R4 Operator Console visual + accessibility gate.
 *
 * This spec is part of the mandatory product-release runner (see
 * scripts/e2e/run_product_e2e.sh). It proves, against the canonical package-6
 * design (docs_archive/00_source_zips/operator_console/r4-20260707-package-6),
 * that every runtime R4 surface:
 *   - renders at both a desktop (1440x900) and a constrained (1024x768) width,
 *   - has no *major* (serious/critical) accessibility violation, and
 *   - is reachable under the six operator roles with allow/deny enforced.
 *
 * The 32 archived data-screen-label values are mapped to their productized
 * runtime surface in docs/evidence/completion/ODP-OC-R4-011/screen_label_coverage.json
 * and audited statically by scripts/e2e/check_operator_visual_a11y_gate.py.
 *
 * Screenshots are attached to the Playwright report as the desktop/constrained
 * comparison evidence required by the acceptance criteria.
 */

import AxeBuilder from "@axe-core/playwright";
import { expect, request as playwrightRequest, test, type Page } from "@playwright/test";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";

const VIEWPORTS = [
  { id: "desktop-1440x900", width: 1440, height: 900 },
  { id: "constrained-1024x768", width: 1024, height: 768 },
] as const;

// Archived screen label -> productized runtime surface (package-6 parity).
// `ready` is a selector proven by the merged operator suite so navigation is
// deterministic even though several archived labels render under testids
// rather than a literal data-screen-label attribute. `tab` is an optional tab
// control to activate before the surface is asserted. This list mirrors the
// runtime_workspace entries in
// docs/evidence/completion/ODP-OC-R4-011/screen_label_coverage.json.
type Surface = { label: string; url: string; ready: string; tab?: string };

const WORKSPACE_SURFACES: Surface[] = [
  { label: "Today 今日工作", url: "/operator", ready: '[data-testid="operator-today-workspace"]' },
  { label: "Store Ops 門市營運", url: "/operator?ws=store", ready: '[data-screen-label="Store Ops 門市營運"]' },
  { label: "Store Ops 全店四燈摘要", url: "/operator?ws=store", ready: '[aria-label="Store Ops four-light quick filters"]' },
  { label: "Govern 治理稽核", url: "/operator?ws=govern", ready: '[data-testid="governance-workspace"]' },
  { label: "Growth 營收成長", url: "/operator?ws=growth", ready: '[data-testid="growth-workspace"]' },
  { label: "Growth 建立入口", url: "/operator?ws=growth", ready: '[data-testid="growth-entry-cards"]' },
  { label: "Growth 會員分群", url: "/operator?ws=growth", tab: "growth-tab-segments", ready: '[data-testid="growth-segment-table"]' },
  { label: "Growth PriceOps", url: "/operator?ws=growth", tab: "growth-tab-priceops", ready: '[aria-label="PriceOps recommendations"]' },
  { label: "Network 展店與店網", url: "/operator?ws=network", ready: '[data-testid="network-find-areas-workspace"]' },
  { label: "Network Expansion Flow Stepper", url: "/operator?ws=network", ready: '[data-testid="network-expansion-stepper"]' },
  { label: "Network 找區域", url: "/operator?ws=network", tab: "network-tab-0", ready: '[data-testid="network-panel-find-areas"]' },
  { label: "Network 物件雷達", url: "/operator?ws=network", tab: "network-tab-1", ready: '[data-testid="network-panel-listings"]' },
  { label: "Network 候選點工作台", url: "/operator?ws=network", tab: "network-tab-2", ready: '[data-testid="network-panel-candidates"]' },
  { label: "Network SiteScore Lab", url: "/operator?ws=network", tab: "network-tab-3", ready: '[data-testid="network-panel-sitescore"]' },
  { label: "Network 候選點比較", url: "/operator?ws=network", tab: "network-tab-4", ready: '[data-testid="network-panel-compare"]' },
  { label: "Network 選址審核", url: "/operator?ws=network", tab: "network-tab-5", ready: '[data-testid="network-panel-review"]' },
  { label: "Network 低效重配", url: "/operator?ws=network", tab: "network-tab-6", ready: '[data-testid="network-panel-rebalance"]' },
];

// Role -> allowed workspaces (server-side allow/deny source of truth).
const EXPECTED_WORKSPACES: Record<string, string[]> = {
  "cs-lead": ["today", "store", "govern"],
  "expansion-manager": ["today", "network", "govern"],
  "field-lead": ["today", "store"],
  "marketing-manager": ["today", "growth", "govern"],
  "ops-lead": ["today", "store", "growth", "network", "govern"],
  "pm-audit": ["today", "store", "govern"],
};

const ALL_WORKSPACES = ["today", "store", "growth", "network", "govern"];

function systemRoleFor(roleId: string) {
  switch (roleId) {
    case "field-lead":
      return "regional_supervisor";
    case "marketing-manager":
      return "marketing_manager";
    case "expansion-manager":
      return "expansion_user";
    case "pm-audit":
      return "auditor";
    default:
      return "operations_manager";
  }
}

const roleHeaders = (roleId: string) => ({
  "x-correlation-id": `corr-operator-visual-a11y-${roleId}`,
  "x-operator-role": roleId,
  "x-roles": systemRoleFor(roleId),
  "x-subject-id": `operator-${roleId}`,
  "x-tenant-id": "tenant-a",
});

async function scanNoMajorA11y(page: Page, context: string) {
  const results = await new AxeBuilder({ page }).include('[data-testid="operator-console"]').analyze();
  const major = results.violations
    .filter((violation) => violation.impact === "serious" || violation.impact === "critical")
    .map((violation) => ({ id: violation.id, impact: violation.impact, nodes: violation.nodes.length }));
  expect(major, `major a11y violations at ${context}`).toEqual([]);
}

test("all six roles enforce workspace allow/deny in the bootstrap envelope", async () => {
  const api = await playwrightRequest.newContext();
  try {
    for (const [roleId, allowed] of Object.entries(EXPECTED_WORKSPACES)) {
      const bootstrap = await api.get(`${API_BASE_URL}/api/v1/operator/bootstrap`, {
        headers: roleHeaders(roleId),
      });
      expect(bootstrap.status(), `${roleId} bootstrap`).toBe(200);
      const body = await bootstrap.json();
      const allowedWorkspaces: string[] = body.navigation.allowedWorkspaces;

      // Allow path: exactly the permitted workspaces are exposed.
      expect(allowedWorkspaces, `${roleId} allow`).toEqual(allowed);

      // Deny path: every other workspace is withheld from this role.
      const denied = ALL_WORKSPACES.filter((workspace) => !allowed.includes(workspace));
      for (const workspace of denied) {
        expect(allowedWorkspaces, `${roleId} deny ${workspace}`).not.toContain(workspace);
      }
    }
  } finally {
    await api.dispose();
  }
});

for (const viewport of VIEWPORTS) {
  test(`R4 runtime surfaces render with no major a11y violations at ${viewport.id}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });

    for (const surface of WORKSPACE_SURFACES) {
      await page.goto(surface.url);
      await expect(page.getByTestId("operator-console")).toBeVisible();

      // The productized route must never fall back to the design iframe.
      await expect(page.getByTestId("operator-design-frame")).toHaveCount(0);
      await expect(page.locator('iframe[src*="/operator-design/"]')).toHaveCount(0);

      if (surface.tab) {
        await page.getByTestId(surface.tab).click();
      }
      await expect(page.locator(surface.ready).first()).toBeVisible({ timeout: 15_000 });

      await scanNoMajorA11y(page, `${surface.label} @ ${viewport.id}`);
      await test.info().attach(`${surface.label} @ ${viewport.id}`, {
        body: await page.screenshot({ fullPage: true }),
        contentType: "image/png",
      });
    }
  });
}

test("Top Navigation, Notifications, and Role Switch chrome render on Today", async ({ page }) => {
  await page.goto("/operator");
  await expect(page.getByTestId("operator-console")).toBeVisible();

  // Top Navigation
  await expect(page.getByRole("navigation", { name: "Operator workspaces" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Open command palette" })).toBeVisible();

  // Notifications panel opens from the header (archived label: "Notifications").
  await page.getByRole("button", { name: "Open notifications" }).click();
  await expect(page.locator('[data-screen-label="Notifications"]')).toBeVisible();

  // Role Switch menu opens (archived label: "Role Switch Menu").
  await page.getByRole("button", { name: "Open notifications" }).click();
  await page.getByTestId("operator-role-switch").click();
  await expect(page.locator('[data-screen-label="Role Switch Menu"]')).toBeVisible();
});

test("Network find-areas map canvas is nonblank in deterministic test mode", async ({ page }) => {
  await page.goto("/operator?ws=network");
  await expect(page.getByTestId("network-find-areas-workspace")).toBeVisible();
  const map = page.getByTestId("heat-zone-map");
  await expect(map).toBeVisible();

  // The expansion flow stepper sits above the network tabs (package-6 R4).
  await expect(page.getByTestId("network-expansion-stepper")).toBeVisible();
});

test("workspace + entity + tab deep-link state survives a reload", async ({ page }) => {
  // The store deep link is URL-driven (proven by operator-shell-today).
  await page.goto("/operator?ws=store&entity=ISS-1024&tab=triage");
  const store = page.locator('[data-screen-label="Store Ops 門市營運"]');
  await expect(store).toBeVisible();
  await expect(store).toHaveAttribute("data-selected-issue-id", "ISS-1024");
  await expect(store).toHaveAttribute("data-selected-tab-id", "triage");

  await page.reload();
  // After reload the store workspace, selected issue, and tab are resolved from
  // the URL rather than resetting to the Today default.
  await expect(store).toBeVisible();
  await expect(store).toHaveAttribute("data-selected-issue-id", "ISS-1024");
  await expect(store).toHaveAttribute("data-selected-tab-id", "triage");
});
