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
  "cs-lead": ["today", "store"],
  "expansion-manager": ["today", "network"],
  "field-lead": ["today", "store"],
  "marketing-manager": ["today", "growth"],
  "ops-lead": ["today", "store", "govern"],
  "pm-audit": ["today", "network", "govern"],
};

const expectedCards: Record<string, string[]> = {
  "ops-lead": ["高風險未指派", "已逾期 Issue", "即將逾期", "成效待判斷", "待我核准", "需升級門市"],
  "cs-lead": ["AI 未解決案件", "Google 負評未回覆", "退款／付款案件", "回覆 SLA 即將逾期", "需轉交工務"],
  "field-lead": ["指派給我的工單", "設備離線", "遠端重啟失敗", "付款機異常", "巡檢待回報"],
  "marketing-manager": ["離峰機會", "會員召回候選", "促銷草稿", "待核准活動", "成效待判斷"],
  "expansion-manager": ["新外部物件", "追蹤中物件", "缺資料候選", "待評分／待送審", "待審 Review", "重配候選門市"],
  "pm-audit": ["待核准項", "Audit 待補", "今日決策數", "隱私敏感操作"],
};

test.describe.configure({ mode: "serial" });

test("expansion contributor keeps the server persona and its restricted navigation", async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem("oday.operator.role", "expansion-staff"));
  await page.goto("/operator");
  await expect(page.getByTestId("operator-envelope-source")).toHaveText("operator-shell-api-envelope");
  await expect(page.getByTitle("展店專員", { exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Operator workspaces" }).getByRole("button", { name: /門市營運/ })).toHaveAttribute("aria-disabled", "true");
  const api = await playwrightRequest.newContext();
  const response = await api.get(`${API_BASE_URL}/api/v1/operator/bootstrap`, { headers: roleHeaders("expansion-staff") });
  expect(response.status()).toBe(200);
  const body = await response.json();
  expect(body.meta.role.id).toBe("expansion-staff");
  expect(body.navigation.allowedWorkspaces).toEqual(["today", "network"]);
  await api.dispose();
});

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
    expect(todayBody.today.kpis.map((item: { label: string }) => item.label)).toEqual(expectedCards[roleId]);
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
    case "expansion-staff":
      return "expansion_user";
    case "pm-audit":
      return "auditor";
    default:
      return "operations_manager";
  }
}
