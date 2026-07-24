import { expect, request as playwrightRequest, test } from "@playwright/test";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";

const roleHeaders = (roleId: string) => ({
  "x-correlation-id": `corr-operator-shell-${roleId}`,
  "x-operator-role": roleId,
  "x-roles": systemRoleFor(roleId),
  "x-subject-id": `operator-${roleId}`,
  "x-tenant-id": "tenant-a",
});

const expectedWorkspaces: Record<string, string[]> = {
  "cs-lead": ["today", "store", "govern"],
  "expansion-manager": ["today", "network", "govern"],
  "field-lead": ["today", "store"],
  "marketing-manager": ["today", "growth", "govern"],
  "ops-lead": ["today", "store", "growth", "network", "govern"],
  "pm-audit": ["today", "store", "network", "govern"],
};

test.describe.configure({ mode: "serial" });

test("bootstrap and today APIs return role-aware envelope data for all six roles", async () => {
  const api = await playwrightRequest.newContext();
  const queueSignatures = new Set<string>();

  for (const [roleId, allowedWorkspaces] of Object.entries(expectedWorkspaces)) {
    const headers = roleHeaders(roleId);
    const bootstrap = await api.get(`${API_BASE_URL}/api/v1/operator/bootstrap`, { headers });
    const today = await api.get(`${API_BASE_URL}/api/v1/operator/today`, { headers });

    expect(bootstrap.status(), `${roleId} bootstrap`).toBe(200);
    expect(today.status(), `${roleId} today`).toBe(200);

    const bootstrapBody = await bootstrap.json();
    const todayBody = await today.json();

    expect(bootstrapBody.navigation.allowedWorkspaces).toEqual(allowedWorkspaces);
    expect(todayBody.navigation.allowedWorkspaces).toEqual(allowedWorkspaces);
    expect(todayBody.meta.role.id).toBe(roleId);
    expect(todayBody.today.queue.length).toBeGreaterThan(0);
    expect(todayBody.search.count).toBeGreaterThanOrEqual(todayBody.today.queue.length);
    expect(todayBody.header.counts.taskCenter).toBe(todayBody.today.queue.length);
    expect(todayBody.header.counts.approvals).toBe(todayBody.today.decisions.length);

    queueSignatures.add(todayBody.today.queue.map((item: { id: string }) => item.id).join("|"));
  }

  expect(queueSignatures.size).toBe(Object.keys(expectedWorkspaces).length);
  await api.dispose();
});

test("Ctrl+K search opens the exact entity and tab from the API target", async ({ page }) => {
  await page.goto("/operator");
  await expect(page.getByTestId("operator-envelope-source")).toHaveText("operator-shell-api-envelope");

  await page.keyboard.press("Control+K");
  await expect(page.getByRole("combobox", { name: "Global search" })).toBeFocused();

  await page.getByRole("combobox", { name: "Global search" }).fill("ISS-1024");
  await expect(page.getByRole("listbox")).toContainText("ISS-1024");
  await page.keyboard.press("Enter");

  await expect(page).toHaveURL(/\/operator\?ws=store&entity=ISS-1024&tab=triage/);
  const storeWorkspace = page.locator('[data-screen-label="Store Ops 門市營運"]');
  await expect(storeWorkspace).toBeVisible();
  await expect(storeWorkspace).toHaveAttribute("data-selected-issue-id", "ISS-1024");
  await expect(storeWorkspace).toHaveAttribute("data-selected-tab-id", "triage");
});

test("Today queue selection opens the queue target workspace, entity, and tab", async ({ page }) => {
  await page.goto("/operator");
  await expect(page.getByTestId("operator-today-workspace")).toBeVisible();

  await page.getByTestId("operator-today-queue").getByRole("button", { name: /ISS-1021/ }).click();

  await expect(page).toHaveURL(/\/operator\?ws=store&entity=ISS-1021&tab=assign/);
  const storeWorkspace = page.locator('[data-screen-label="Store Ops 門市營運"]');
  await expect(storeWorkspace).toBeVisible();
  await expect(storeWorkspace).toHaveAttribute("data-selected-issue-id", "ISS-1021");
  await expect(storeWorkspace).toHaveAttribute("data-selected-tab-id", "assign");
});

test("approval writes refresh header counts from the API envelope without session reset", async ({ page }) => {
  await page.goto("/operator");
  await expect(page.getByTestId("operator-today-workspace")).toBeVisible();
  await expect(page.getByTestId("operator-envelope-source")).toHaveText("operator-shell-api-envelope");

  const count = page.getByTestId("operator-approval-count").locator("strong");
  await expect(count).not.toHaveText("0");
  const before = Number(await count.textContent());
  expect(before).toBeGreaterThan(0);

  await page.getByTestId("operator-decision-rail").getByRole("button", { name: "核准" }).first().click();

  await expect(count).toHaveText(String(before - 1));
  await expect(page.getByTestId("operator-task-center-count").locator("strong")).not.toHaveText("0");
});

function systemRoleFor(roleId: string) {
  switch (roleId) {
    case "field-lead":
      return "regional_supervisor";
    case "marketing-manager":
      return "marketing_manager";
    case "expansion-manager":
      return "expansion_user,site_reviewer";
    case "pm-audit":
      return "auditor";
    default:
      return "operations_manager";
  }
}
