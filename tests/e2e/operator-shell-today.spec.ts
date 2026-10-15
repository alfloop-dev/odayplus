import { expect, request as playwrightRequest, test } from "@playwright/test";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";

const roleHeaders = (roleId: string) => ({
  "x-correlation-id": `corr-operator-shell-${roleId}`,
  "x-operator-role": roleId,
  "x-roles": systemRoleFor(roleId),
  "x-subject-id": `operator-${roleId}`,
  "x-tenant-id": "tenant-a",
});

// Non-Network APIs share operator_console VIEW; Network tabs follow their
// own resource/intake guards rather than the display persona's old grants.
const consoleWorkspaces = ["today", "store", "growth", "govern"];
const allWorkspaces = ["today", "store", "growth", "network", "govern"];
const expectedWorkspaces: Record<string, string[]> = {
  "cs-lead": consoleWorkspaces,
  "expansion-manager": allWorkspaces,
  "field-lead": consoleWorkspaces,
  "marketing-manager": consoleWorkspaces,
  "ops-lead": consoleWorkspaces,
  "pm-audit": allWorkspaces,
};
const expectedNetworkTabs: Record<string, string[]> = {
  "cs-lead": [],
  "expansion-manager": ["areas", "radar", "candidates", "score", "compare", "review", "rebalance", "composition"],
  "field-lead": [],
  "marketing-manager": [],
  "ops-lead": [],
  "pm-audit": ["areas", "radar", "rebalance"],
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

test("expansion contributor keeps the server persona, principal-derived navigation and decision denial", async ({ page }) => {
  await page.setExtraHTTPHeaders(roleHeaders("expansion-staff"));
  await page.addInitScript(() => sessionStorage.setItem("oday.operator.role", "expansion-staff"));
  await page.goto("/operator");
  await expect(page.getByTestId("operator-envelope-source")).toHaveText("operator-shell-api-envelope");
  await expect(page.getByTitle("展店專員", { exact: true })).toBeVisible();
  // Expansion has operator_console VIEW, which protects these non-Network
  // reads. The persona label cannot remove its actual API grants.
  await expect(page.getByRole("navigation", { name: "Operator workspaces" }).getByRole("button", { name: /門市營運/ })).toHaveAttribute("aria-disabled", "false");
  const api = await playwrightRequest.newContext();
  const response = await api.get(`${API_BASE_URL}/api/v1/operator/bootstrap`, { headers: roleHeaders("expansion-staff") });
  expect(response.status()).toBe(200);
  const body = await response.json();
  expect(body.meta.role.id).toBe("expansion-staff");
  expect(body.navigation.allowedWorkspaces).toEqual(allWorkspaces);
  expect(body.meta.role.allowedWorkspaces).toEqual(allWorkspaces);
  // Console read access does not grant SiteScore decision authority.
  const decision = await api.post(`${API_BASE_URL}/api/v1/operator/network-reviews/RV-701/decide`, {
    headers: roleHeaders("expansion-staff"),
    data: { decision: "GO", reason: "Verify contributor decision denial without mutation." },
  });
  expect(decision.status()).toBe(403);
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
    expect(bootstrapBody.navigation.allowedTabs.network).toEqual(expectedNetworkTabs[roleId]);
    expect(todayBody.navigation.allowedTabs.network).toEqual(expectedNetworkTabs[roleId]);
    expect(todayBody.meta.role.id).toBe(roleId);
    expect(todayBody.today.kpis.map((item: { label: string }) => item.label)).toEqual(expectedCards[roleId]);
    expect(todayBody.today.queue.length).toBeGreaterThan(0);
    expect(todayBody.search.count).toBeGreaterThanOrEqual(todayBody.today.queue.length);
    expect(todayBody.header.counts.taskCenter).toBe(todayBody.today.queue.length);
    expect(todayBody.header.counts.approvals).toBe(todayBody.today.decisions.length);

    queueSignatures.add(todayBody.today.queue.map((item: { id: string }) => item.id).join("|"));
  }

  expect(queueSignatures.size).toBe(Object.keys(expectedWorkspaces).length);

  // Both principals display pm-audit, but their actual API grants differ.
  // Keep the deployed auditor+admin regression and the scoped viewer in the
  // browser-run API gate without trusting that shared persona as authority.
  for (const [roles, tabs] of [
    ["auditor,platform_admin", ["rebalance"]],
    ["operator_viewer", ["areas", "radar", "candidates", "score", "compare", "review"]],
  ] as const) {
    const headers = { ...roleHeaders("pm-audit"), "x-roles": roles };
    for (const endpoint of ["bootstrap", "today"]) {
      const response = await api.get(`${API_BASE_URL}/api/v1/operator/${endpoint}`, { headers });
      expect(response.status()).toBe(200);
      const body = await response.json();
      expect(body.navigation.allowedWorkspaces).toEqual(allWorkspaces);
      expect(body.navigation.allowedTabs.network).toEqual(tabs);
    }
    for (const [tab, endpoint] of Object.entries({
      areas: "/api/v1/operator/network-listings",
      radar: "/api/v1/operator/network-listings/intake",
      candidates: "/api/v1/operator/network-scoring",
      score: "/api/v1/operator/network-scoring",
      compare: "/api/v1/operator/network-scoring",
      review: "/api/v1/operator/network-reviews",
      rebalance: "/api/v1/operator/network-rebalance",
      composition: "/api/v1/heatzones/merge-split/proposals",
    })) {
      const read = await api.get(`${API_BASE_URL}${endpoint}`, { headers });
      if ((tabs as readonly string[]).includes(tab)) {
        expect(read.ok(), `${roles} ${tab}: ${read.status()}`).toBe(true);
      } else {
        expect(read.status(), `${roles} ${tab}`).toBe(403);
      }
    }
  }
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

  const card = page.getByTestId("operator-decision-rail").locator("article").first();
  const approvalId = await card.getAttribute("data-target-entity");
  expect(approvalId).toBeTruthy();
  await expect(card).toHaveAttribute("data-target-workspace", "govern");
  await expect(card).toHaveAttribute("data-target-tab", "approvals");
  const ownerRead = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/v1/operator/governance/snapshot" && response.request().method() === "GET");
  await card.getByRole("button", { name: "核准", exact: true }).click();
  const ownerResponse = await ownerRead;
  expect(ownerResponse.status()).toBe(200);
  const owner = await ownerResponse.json();
  expect(owner.actionAuthority).toMatchObject({ verified: true, decide: true });
  const exactRows = owner.approvals.filter((row: { id: string; status: string }) => row.id === approvalId && row.status === "pending");
  expect(exactRows).toHaveLength(1);
  const detail = page.getByRole("article", { name: "Approval detail" });
  await expect(detail).toContainText(approvalId!);
  await expect(detail.getByRole("heading", { name: exactRows[0].title, exact: true })).toBeVisible();
  // Opening an owner is read-only. Only its successful decision may change a count.
  await expect(count).toHaveText(String(before));
  await detail.getByLabel(/決策理由/).fill("Local E2E review of this recorded approval and its evidence.");
  const command = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/v1/operator/governance/decisions" && response.request().method() === "POST");
  const refreshed = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/v1/operator/bootstrap" && response.request().method() === "GET");
  await detail.getByRole("button", { name: "核准", exact: true }).click();
  const confirmed = await command;
  expect(confirmed.status()).toBe(200);
  const receipt = await confirmed.json();
  expect(receipt).toMatchObject({ approvalId, action: "approve", status: "approved", finalDecision: "Approved" });
  expect(receipt.decision.approvalId).toBe(approvalId);
  expect(receipt.correlation_id).toBe(confirmed.headers()["x-correlation-id"]);
  expect(confirmed.request().headers()["idempotency-key"]).toBeTruthy();
  const freshResponse = await refreshed;
  expect(freshResponse.status()).toBe(200);
  const fresh = await freshResponse.json();
  expect(fresh.header.counts.approvals).toBe(before - 1);
  expect(fresh.today.decisions.some((row: { id: string }) => row.id === approvalId)).toBe(false);

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
