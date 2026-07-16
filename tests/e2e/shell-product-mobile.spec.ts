import { test, expect, devices, type Page } from "@playwright/test";

/**
 * Product shell E2E on a phone viewport — ODP-PGAP-SHELL-001 (acceptance §6, §7).
 *
 * Scope note: the operator surfaces render inside the R0 AppShell, whose CSS
 * has no responsive rules at all (packages/ui/src/styles/shell.css is a fixed
 * desktop grid, so at 412px the sidebar leaves ~115px of main). Making that
 * frame responsive is ODP-PGAP-UX-001's owned layer, so these tests assert what
 * this task owns — the operator surfaces render and their controls are
 * touch-sized — and the full no-overflow assertion runs against the franchisee
 * portal, which this task deliberately renders outside that frame.
 *
 * A separate file rather than a describe block in shell-product.spec.ts:
 * `devices["Pixel 7"]` carries `defaultBrowserType`, which Playwright only
 * accepts at file top level (it forces a new worker). A separate Playwright
 * project would instead re-run every other suite on a phone, which is not what
 * this task needs to prove.
 *
 * Run:
 *   npx playwright test tests/e2e --grep ODP-PGAP-SHELL-001
 */

/** Operator Console routes are tenant-isolated; see playwright.config.ts. */
const TENANT = "tenant-a";

test.use({ ...devices["Pixel 7"] });

/**
 * Assert nothing inside a shell region overflows that region's own box.
 *
 * Measured against the region rather than the viewport on purpose. The R0
 * GlobalHeader (role/theme/density switchers, env badge) is ~1130px wide and
 * stretches the document on every route at a phone width; that is pre-existing
 * and belongs to ODP-PGAP-UX-001 (responsive behaviour), not to this task.
 * Measuring against the viewport would therefore fail on someone else's layer
 * while hiding real regressions in this one — so this asserts the property this
 * task actually owns: the shell's own content reflows into whatever width the
 * frame gives it, and never forces a scrollbar of its own.
 */
async function expectRegionFitsItsBox(page: Page, testId: string) {
  const result = await page.evaluate((id) => {
    const root = document.querySelector(`[data-testid="${id}"]`);
    if (!root) return { missing: true, offenders: [] as string[] };
    const limit = root.getBoundingClientRect().right + 1;
    const offenders = Array.from(root.querySelectorAll("*"))
      .filter((el) => {
        // A region that opts into its own horizontal scroll (wide tables) is
        // allowed to contain wider children — that is the documented escape.
        const scroller = (el as HTMLElement).closest("[data-scroll-x]");
        return !scroller && el.getBoundingClientRect().right > limit;
      })
      .map((el) => `${el.tagName}.${String((el as HTMLElement).className).slice(0, 40)}`);
    return { missing: false, offenders: offenders.slice(0, 5) };
  }, testId);

  expect(result.missing, `region ${testId} not found`).toBe(false);
  expect(result.offenders, `content in ${testId} overflows its container`).toEqual([]);
}

test("ODP-PGAP-SHELL-001 franchisee portal supports viewing, acknowledgement and reporting", async ({
  browser,
}) => {
  const context = await browser.newContext({
    ...devices["Pixel 7"],
    extraHTTPHeaders: {
      "x-subject-id": "franchisee-e2e",
      "x-roles": "franchisee",
      "x-tenant-id": TENANT,
    },
  });
  const page = await context.newPage();
  await page.goto("/franchisee");

  await expect(page.getByTestId("shell-franchisee")).toBeVisible();
  await expect(page.getByTestId("franchisee-data-source")).toHaveAttribute("data-source", "api");
  await expectRegionFitsItsBox(page, "shell-franchisee");

  // Acknowledge — durable across a reload.
  const notification = page.getByTestId("franchisee-notification-NTF-SLA-1024");
  await expect(notification).toBeVisible();
  await page.getByTestId("franchisee-ack-NTF-SLA-1024").click();
  await expect(page.getByTestId("franchisee-acked-NTF-SLA-1024")).toBeVisible();
  await page.reload();
  await expect(notification).toHaveAttribute("data-acknowledged", "true");

  // Report — durable across a reload.
  await page.getByTestId("franchisee-report-category").selectOption("equipment");
  await page.getByTestId("franchisee-report-message").fill("冷藏櫃溫度異常");
  await page.getByTestId("franchisee-report-submit").click();
  await expect(page.getByTestId("franchisee-report-sent")).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("franchisee-reports")).toContainText("冷藏櫃溫度異常");

  await context.close();
});

test("ODP-PGAP-SHELL-001 franchisee portal shows no operator-only data", async ({ browser }) => {
  const context = await browser.newContext({
    ...devices["Pixel 7"],
    extraHTTPHeaders: {
      "x-subject-id": "franchisee-e2e-2",
      "x-roles": "franchisee",
      "x-tenant-id": TENANT,
    },
  });
  const page = await context.newPage();
  await page.goto("/franchisee");

  const body = page.locator("body");
  // Approvals, model-snapshot notifications and other workspaces' entities are
  // operator-only and must be absent from the payload entirely.
  await expect(body).not.toContainText("APR-501");
  await expect(body).not.toContainText("SiteScore");
  await expect(body).not.toContainText("模型快照更新");
  await expect(body).not.toContainText("GRW-201");
  await expect(body).not.toContainText("NET-305");
  // Operator-internal task detail is projected away server-side.
  await expect(body).not.toContainText("指派給");

  await context.close();
});

test("ODP-PGAP-SHELL-001 mobile 404 surface renders and offers a way onward", async ({ page }) => {
  const response = await page.goto("/this-route-does-not-exist");
  expect(response?.status()).toBe(404);
  await expect(page.getByTestId("shell-state-not-found")).toBeVisible();
  await expect(page.getByTestId("shell-state-next")).toBeVisible();
  await expectRegionFitsItsBox(page, "route-not-found");
});

test("ODP-PGAP-SHELL-001 mobile task center renders and its controls are touch-sized", async ({
  page,
}) => {
  await page.goto("/tasks");
  await expect(page.getByTestId("shell-tasks")).toBeVisible();
  await expect(page.getByTestId("tasks-list")).toBeVisible();

  // Filter controls stay above the 44px touch-target floor.
  const box = await page.getByTestId("filter-sla-breached").boundingBox();
  expect(box!.height).toBeGreaterThanOrEqual(44);
});

test("ODP-PGAP-SHELL-001 mobile home renders every region", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("shell-home")).toBeVisible();
  await expect(page.getByTestId("home-metrics")).toBeVisible();
  await expect(page.getByTestId("home-entry-points")).toBeVisible();
});
