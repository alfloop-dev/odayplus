import { test, expect } from "@playwright/test";

/**
 * OpsBoard shell smoke (ODP-R0-004).
 *
 * Verifies the R0 acceptance without any auth backend:
 *  - the app shell + role-aware sidebar render
 *  - design tokens are applied (CSS variables resolve)
 *  - all 14 work-area routes are reachable
 *  - navigation is role-aware (omitted items appear when the role changes)
 */

const ROUTES: { path: string; key: string }[] = [
  { path: "/", key: "home" },
  { path: "/tasks", key: "tasks" },
  { path: "/search", key: "search" },
  { path: "/expansion", key: "expansion" },
  { path: "/operations", key: "operations" },
  { path: "/interventions", key: "interventions" },
  { path: "/pricing", key: "pricing" },
  { path: "/adlift", key: "adlift" },
  { path: "/avm", key: "avm" },
  { path: "/netplan", key: "netplan" },
  { path: "/learning", key: "learning" },
  { path: "/audit", key: "audit" },
  { path: "/admin", key: "admin" },
  { path: "/franchisee", key: "franchisee" },
];

test("shell renders without an auth backend", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("app-shell")).toBeVisible();
  await expect(page.getByTestId("global-header")).toBeVisible();
  await expect(page.getByTestId("sidebar")).toBeVisible();
  await expect(page.getByTestId("env-badge")).toHaveText("dev");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("OpsBoard");
});

test("design tokens are applied as CSS variables", async ({ page }) => {
  await page.goto("/");
  const canvas = await page.evaluate(() =>
    getComputedStyle(document.documentElement)
      .getPropertyValue("--odp-color-bg-canvas")
      .trim(),
  );
  expect(canvas).toBe("#F8FAFC");
});

test("all 14 work-area routes are reachable", async ({ page }) => {
  for (const route of ROUTES) {
    const res = await page.goto(route.path);
    expect(res?.status(), `GET ${route.path}`).toBeLessThan(400);
    await expect(page.getByTestId("app-shell")).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  }
});

test("navigation is role-aware", async ({ page }) => {
  await page.goto("/");
  const sidebar = page.getByTestId("sidebar");

  // Default role is ops_manager: operations is visible, admin is not.
  await expect(sidebar.getByTestId("nav-operations")).toBeVisible();
  await expect(sidebar.getByTestId("nav-admin")).toHaveCount(0);

  // Switch to the admin role: the admin item now appears.
  await page.getByTestId("role-switcher").selectOption("admin");
  await expect(sidebar.getByTestId("nav-admin")).toBeVisible();
});

test("sidebar navigation updates the page header", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("nav-pricing").click();
  await expect(page).toHaveURL(/\/pricing$/);
  await expect(page.getByTestId("page-header")).toContainText("定價");
  await expect(page.getByTestId("module-pricing")).toBeVisible();
});
