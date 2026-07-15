import { randomUUID } from "node:crypto";

import { test, expect, type Browser, type Page } from "@playwright/test";

/**
 * Product shell E2E — ODP-PGAP-SHELL-001.
 *
 * Drives the real shell against the real FastAPI backend booted by
 * playwright.config.ts. Nothing here stubs a route or asserts on fixture copy:
 * the point of this task is that the shell shows what the backend actually
 * holds, so the tests have to prove that end to end.
 *
 * Mobile coverage lives in shell-product-mobile.spec.ts (a phone viewport must
 * be configured at file top level).
 *
 * Run:
 *   npx playwright test tests/e2e --grep ODP-PGAP-SHELL-001
 */

/** Operator Console routes are tenant-isolated; see playwright.config.ts. */
const TENANT = "tenant-a";

/** The default e2e principal resolves to ops-lead (operations_manager). */
const OPS_ROLES =
  "finance_legal,expansion_user,operations_manager,regional_supervisor,site_reviewer,data_owner,auditor,executive";

/**
 * A page acting as a brand-new operator user.
 *
 * Acknowledgement, preferences and settings are personal state keyed by
 * subject, and playwright.config reuses a long-lived API server
 * (reuseExistingServer). A fixed subject would therefore inherit the previous
 * run's writes and the test would only pass on a cold backend — so each call
 * mints a unique subject and starts from a genuinely clean personal state.
 *
 * The principal keeps its full role set so RBAC still grants operator_console
 * UPDATE, while X-Operator-Role narrows the Operator Console identity that
 * decides which rows are visible.
 */
async function freshOperatorPage(
  browser: Browser,
  operatorRole: "pm-audit" | "ops-lead" = "pm-audit",
): Promise<Page> {
  const context = await browser.newContext({
    extraHTTPHeaders: {
      "x-subject-id": `product-e2e-${operatorRole}-${randomUUID()}`,
      "x-roles": OPS_ROLES,
      "x-tenant-id": TENANT,
      "x-operator-role": operatorRole,
    },
  });
  return context.newPage();
}

// ---------------------------------------------------------------------------
// Home — acceptance §1
// ---------------------------------------------------------------------------

test("ODP-PGAP-SHELL-001 home aggregates API-backed status, tasks, approvals and freshness", async ({
  page,
}) => {
  await page.goto("/");

  await expect(page.getByTestId("shell-home")).toBeVisible();
  // The data actually came from the API — not a fixture fallback.
  await expect(page.getByTestId("home-data-source")).toHaveAttribute("data-source", "api");
  await expect(page.getByTestId("home-data-source")).toHaveAttribute("data-state", "ready");

  await expect(page.getByTestId("home-status-headline")).toContainText("待處理");
  for (const metric of [
    "metric-open-tasks",
    "metric-sla-breached",
    "metric-sla-at-risk",
    "metric-approvals",
    "metric-notifications",
  ]) {
    await expect(page.getByTestId(metric)).toBeVisible();
  }

  // Every freshness source is named individually.
  await expect(page.getByTestId("home-freshness-operator-state")).toBeVisible();
  await expect(page.getByTestId("home-freshness-shell-overlay")).toBeVisible();

  await expect(page.getByTestId("home-tasks")).toBeVisible();
  await expect(page.getByTestId("home-approvals")).toBeVisible();
});

test("ODP-PGAP-SHELL-001 home entry points are role-relevant and reachable", async ({ page }) => {
  await page.goto("/");

  const entries = page.getByTestId("home-entry-points");
  await expect(entries).toBeVisible();
  // ops-lead reaches every workspace, including the admin surface.
  await expect(page.getByTestId("home-entry-tasks")).toBeVisible();
  await expect(page.getByTestId("home-entry-admin")).toBeVisible();

  await page.getByTestId("home-entry-tasks").click();
  await expect(page).toHaveURL(/\/tasks$/);
  await expect(page.getByTestId("shell-tasks")).toBeVisible();
});

test("ODP-PGAP-SHELL-001 header counts come from the API, not a hardcoded fixture", async ({
  page,
}) => {
  await page.goto("/");

  const openTasks = await page.getByTestId("metric-open-tasks-value").innerText();
  const header = page.getByTestId("global-header");
  await expect(header).toBeVisible();
  // The R0 shell hardcoded taskCount={7}; the badge must now agree with the
  // aggregate the page rendered.
  await expect(header).toContainText(openTasks.trim());
});

// ---------------------------------------------------------------------------
// Task Center — acceptance §2
// ---------------------------------------------------------------------------

test("ODP-PGAP-SHELL-001 task center assigns durably and the assignment survives a reload", async ({
  page,
}) => {
  await page.goto("/tasks");
  await expect(page.getByTestId("tasks-data-source")).toHaveAttribute("data-source", "api");

  const firstRow = page.getByTestId("tasks-list").locator("li").first();
  const taskId = (await firstRow.getAttribute("data-testid"))!.replace("task-row-", "");

  await expect(page.getByTestId(`task-assignee-${taskId}`)).toContainText("未指派");

  await page.getByTestId(`task-assign-open-${taskId}`).click();
  await page.getByTestId(`task-assign-select-${taskId}`).selectOption("operator-cs-lead");
  await page.getByTestId(`task-assign-submit-${taskId}`).click();

  await expect(page.getByTestId(`task-assignee-${taskId}`)).toContainText("客服主管");

  // Durable: a reload re-reads from the backend rather than local state.
  await page.reload();
  await expect(page.getByTestId(`task-assignee-${taskId}`)).toContainText("客服主管");
});

test("ODP-PGAP-SHELL-001 task center filters by SLA and assignee via shareable URLs", async ({
  page,
}) => {
  await page.goto("/tasks");
  await page.getByTestId("filter-sla-breached").click();
  await expect(page).toHaveURL(/sla=breached/);

  // Filters compose rather than replacing one another.
  await page.getByTestId("filter-assignee-me").click();
  await expect(page).toHaveURL(/sla=breached/);
  await expect(page).toHaveURL(/assignee=me/);

  // A filtered URL is shareable — a cold load reproduces the same view.
  await page.goto("/tasks?sla=breached&assignee=me");
  await expect(page.getByTestId("filter-sla-breached")).toHaveAttribute("data-active", "true");
  await expect(page.getByTestId("filter-assignee-me")).toHaveAttribute("data-active", "true");
});

test("ODP-PGAP-SHELL-001 task deep link resolves a single task", async ({ page }) => {
  await page.goto("/tasks");
  const firstRow = page.getByTestId("tasks-list").locator("li").first();
  const taskId = (await firstRow.getAttribute("data-testid"))!.replace("task-row-", "");

  await page.getByTestId(`task-link-${taskId}`).click();
  await expect(page).toHaveURL(new RegExp(`taskId=${taskId}`));
  await expect(page.getByTestId(`task-row-${taskId}`)).toBeVisible();
  await expect(page.getByTestId("tasks-list").locator("li")).toHaveCount(1);
});

// ---------------------------------------------------------------------------
// Notifications — acceptance §3
// ---------------------------------------------------------------------------

test("ODP-PGAP-SHELL-001 notifications acknowledge durably with severity and source links", async ({
  browser,
}) => {
  // Inbox state is personal, so this test acts as its own pm-audit user: no
  // parallel test — and no previous run against the reused API server — can
  // acknowledge the row out from under it.
  const page = await freshOperatorPage(browser);
  await page.goto("/notifications");
  await expect(page.getByTestId("notifications-data-source")).toHaveAttribute("data-source", "api");

  const row = page.getByTestId("notifications-list").locator("li").first();
  const id = (await row.getAttribute("data-testid"))!.replace("notification-", "");
  // Severity ordering puts the critical SLA notification first.
  await expect(row).toHaveAttribute("data-severity", "critical");
  await expect(row).toHaveAttribute("data-acknowledged", "false");
  await expect(page.getByTestId(`notification-source-${id}`)).toBeVisible();

  await page.getByTestId(`notification-ack-${id}`).click();
  await expect(page.getByTestId(`notification-acked-${id}`)).toBeVisible();

  await page.reload();
  await expect(page.getByTestId(`notification-${id}`)).toHaveAttribute("data-acknowledged", "true");
  await page.context().close();
});

test("ODP-PGAP-SHELL-001 notification preferences persist as a server write", async ({
  browser,
}) => {
  // Preferences are personal; a fresh subject keeps this independent of every
  // other test and of previous runs against the reused API server.
  const page = await freshOperatorPage(browser);
  await page.goto("/notifications");

  await page.getByTestId("preferences-severity-floor").selectOption("warning");
  await page.getByTestId("preferences-channel-email").uncheck();
  await page.getByTestId("preferences-submit").click();
  await expect(page.getByTestId("preferences-saved")).toBeVisible();

  await page.reload();
  await expect(page.getByTestId("preferences-severity-floor")).toHaveValue("warning");
  await expect(page.getByTestId("preferences-channel-email")).not.toBeChecked();
  await page.context().close();
});

// ---------------------------------------------------------------------------
// Global search — acceptance §4
// ---------------------------------------------------------------------------

test("ODP-PGAP-SHELL-001 search returns authorized cross-domain results", async ({ page }) => {
  await page.goto("/search");
  await expect(page.getByTestId("search-data-source")).toHaveAttribute("data-source", "api");

  await page.getByTestId("search-input").fill("ISS");
  await page.getByTestId("search-submit").click();
  await expect(page).toHaveURL(/q=ISS/);
  await expect(page.getByTestId("search-results")).toBeVisible();

  const first = page.getByTestId("search-results").locator("a").first();
  await expect(first).toBeVisible();
  await first.click();
  await expect(page).toHaveURL(/taskId=/);
});

test("ODP-PGAP-SHELL-001 search supports keyboard command navigation", async ({ page }) => {
  await page.goto("/search?q=");
  await expect(page.getByTestId("search-commands")).toBeVisible();
  // The shortcuts are attached in an effect; a keypress before that is
  // silently dropped, so wait for the listener rather than for hydration luck.
  await expect(page.getByTestId("search-keyboard-ready")).toBeAttached();

  // ArrowDown from the query box enters the result list.
  await page.getByTestId("search-input").focus();
  await page.keyboard.press("ArrowDown");
  const focusedFirst = await page.evaluate(
    () => document.activeElement?.getAttribute("data-nav-index"),
  );
  expect(focusedFirst).toBe("0");

  await page.keyboard.press("ArrowDown");
  const focusedSecond = await page.evaluate(
    () => document.activeElement?.getAttribute("data-nav-index"),
  );
  expect(focusedSecond).toBe("1");

  // Enter opens the focused command — the link is really focused, not painted.
  await page.keyboard.press("Enter");
  await expect(page).not.toHaveURL(/\/search\?q=$/);
});

test("ODP-PGAP-SHELL-001 search does not leak unauthorized workspaces", async ({ browser }) => {
  // An expansion-only principal must never receive a store-workspace entity —
  // asserted against the page content, so a client-side filter would not pass.
  const context = await browser.newContext({
    extraHTTPHeaders: {
      "x-subject-id": "operator-expansion-manager",
      "x-roles": "expansion_user",
      "x-tenant-id": TENANT,
    },
  });
  const page = await context.newPage();
  await page.goto("/search?q=");

  await expect(page.getByTestId("search-scope")).not.toContainText("store");
  await expect(page.locator("body")).not.toContainText("ISS-1024");
  await expect(page.locator("body")).not.toContainText("支付失敗率異常升高");
  await expect(page.getByTestId("search-command-command-admin")).toHaveCount(0);

  await context.close();
});

// ---------------------------------------------------------------------------
// Admin + settings — acceptance §5
// ---------------------------------------------------------------------------

test("ODP-PGAP-SHELL-001 admin workspace grant is an audited server write", async ({ page }) => {
  await page.goto("/admin");
  await expect(page.getByTestId("shell-admin")).toBeVisible();
  await expect(page.getByTestId("admin-role-cs-lead")).toHaveAttribute("data-overridden", "false");

  await page.getByTestId("admin-grant-cs-lead-govern").uncheck();
  await page.getByTestId("admin-grant-submit-cs-lead").click();
  await expect(page.getByTestId("admin-grant-saved-cs-lead")).toBeVisible();

  await page.reload();
  await expect(page.getByTestId("admin-role-cs-lead")).toHaveAttribute("data-overridden", "true");
  await expect(page.getByTestId("admin-role-workspaces-cs-lead")).not.toContainText("govern");
  // The high-risk write shows up in the surface's own audit trail.
  await expect(page.getByTestId("admin-audit")).toContainText("cs-lead");
});

test("ODP-PGAP-SHELL-001 admin refuses a lockout and renders the server's reason", async ({
  page,
}) => {
  await page.goto("/admin");
  // Removing govern from ops-lead would leave nobody able to restore grants.
  await page.getByTestId("admin-grant-ops-lead-govern").uncheck();
  await page.getByTestId("admin-grant-submit-ops-lead").click();

  await expect(page.getByTestId("admin-grant-error-ops-lead")).toBeVisible();
  await expect(page.getByTestId("admin-grant-error-ops-lead")).toContainText("營運主管");
});

test("ODP-PGAP-SHELL-001 admin is forbidden for a non-admin role", async ({ browser }) => {
  const context = await browser.newContext({
    extraHTTPHeaders: {
      "x-subject-id": "operator-pm-audit",
      "x-roles": "auditor",
      "x-tenant-id": TENANT,
    },
  });
  const page = await context.newPage();
  await page.goto("/admin");

  await expect(page.getByTestId("admin-state")).toBeVisible();
  await expect(page.getByTestId("admin-state")).toHaveAttribute("data-state", "forbidden");
  // A dead end is not acceptable — the state names a next step.
  await expect(page.getByTestId("shell-state-next")).toBeVisible();

  await context.close();
});

test("ODP-PGAP-SHELL-001 settings persist as a governed server write", async ({ browser }) => {
  // Settings are personal; a fresh user starts from the documented defaults so
  // the change below is genuinely a change.
  const page = await freshOperatorPage(browser);
  await page.goto("/settings");
  await expect(page.getByTestId("settings-data-source")).toHaveAttribute("data-source", "api");

  await page.getByTestId("settings-density").selectOption("compact");
  await page.getByTestId("settings-submit").click();
  await expect(page.getByTestId("settings-saved")).toBeVisible();

  await page.reload();
  await expect(page.getByTestId("settings-density")).toHaveValue("compact");
  await expect(page.getByTestId("settings-updated-by")).toBeVisible();
  await page.context().close();
});

// ---------------------------------------------------------------------------
// Error / recovery surfaces — acceptance §7 (desktop)
// ---------------------------------------------------------------------------

test("ODP-PGAP-SHELL-001 404 surface offers a way onward", async ({ page }) => {
  const response = await page.goto("/this-route-does-not-exist");
  expect(response?.status()).toBe(404);

  await expect(page.getByTestId("shell-state-not-found")).toBeVisible();
  await expect(page.getByTestId("shell-state-not-found")).toHaveAttribute("data-state", "not-found");
  await page.getByTestId("not-found-home").click();
  await expect(page).toHaveURL(/\/$/);
});

test("ODP-PGAP-SHELL-001 offline is announced and recovers when the link returns", async ({
  page,
  context,
}) => {
  await page.goto("/");
  await expect(page.getByTestId("offline-banner")).toHaveCount(0);

  await context.setOffline(true);
  await page.evaluate(() => window.dispatchEvent(new Event("offline")));
  await expect(page.getByTestId("shell-state-offline")).toBeVisible();

  await context.setOffline(false);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(page.getByTestId("offline-banner")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Production mode — acceptance §8
// ---------------------------------------------------------------------------

test("ODP-PGAP-SHELL-001 no shell route renders placeholder or POC copy", async ({ page }) => {
  // The R0 placeholder ("骨架就緒，等待模組 UI 接入") and the module-* testid
  // are what this task exists to remove from the shell.
  for (const path of ["/", "/tasks", "/search", "/notifications", "/settings", "/admin"]) {
    await page.goto(path);
    await expect(page.locator("body"), path).not.toContainText("骨架就緒，等待模組 UI 接入");
    await expect(page.locator("body"), path).not.toContainText("占位畫面");
    await expect(page.locator("body"), path).not.toContainText("尚無資料來源");
    await expect(page.locator("[data-testid^='module-']"), path).toHaveCount(0);
  }
});
