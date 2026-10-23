import { expect, request as playwrightRequest, test, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { PG_API, PG_CONTROL, PG_WEB } from "../e2e/_intakeScorePg";

// Actual local password form, durable account/session, signed API boundary,
// normal application and owned PostgreSQL. All business inputs are explicitly
// synthetic; this proves engineering composition, not human UAT or production.
test.describe.configure({ mode: "serial", timeout: 120_000 });
const PASSWORD = "Synthetic!Area-Draft!2026";
const base = "/api/v1/operator/area-planning-drafts";
async function browserRequest(page: Page, path: string, input: {
  method?: string; headers?: Record<string, string>; data?: unknown;
} = {}) {
  // Use the same browser cookie transport as the UI. APIRequestContext filters
  // Secure cookies on HTTP 127.0.0.1 while Chromium accepts the loopback origin.
  // Never extract credentials or weaken the product's Secure/HttpOnly cookie.
  return page.evaluate(async ({ path, input }) => {
    const response = await fetch(path, { method: input.method ?? "GET", credentials: "same-origin", cache: "no-store",
      headers: { ...input.headers, ...(input.data === undefined ? {} : { "Content-Type": "application/json" }) },
      ...(input.data === undefined ? {} : { body: JSON.stringify(input.data) }) });
    const text = await response.text();return { status: response.status, text, json: JSON.parse(text) };
  }, { path, input });
}
async function login(page: Page, username: string, role = "expansion-manager") {
  await page.addInitScript(roleId => {
    sessionStorage.setItem("oday.operator.role", roleId);
    localStorage.setItem("oday.operator.role", roleId);
  }, role);
  await page.goto(`${PG_WEB}/login?returnTo=${encodeURIComponent("/operator?ws=network&tab=areas")}`);
  await page.getByLabel("使用者帳號", { exact: true }).fill(username);
  await page.getByLabel("密碼", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).toHaveURL(url => url.pathname === "/operator");
  const session = await browserRequest(page, "/auth/session");
  expect(session.status, session.text).toBe(200);
  expect(session.json.subject).toBe(username);
}
for (const width of [1440, 1024, 390]) {
  test(`area draft persists and reopens through password session at ${width}px`, async ({ page, browser }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    const controls = await playwrightRequest.newContext({ baseURL: PG_API,
      extraHTTPHeaders: { "x-synthetic-control": PG_CONTROL } });
    const other = await browser.newContext();
    try {
      const prepared = await controls.post("/_synthetic/area-planning/setup");
      expect(prepared.status(), await prepared.text()).toBe(200);
      const scenario = await prepared.json();
      expect(scenario.scope).toBe("ISOLATED_SYNTHETIC_AREA_DRAFT_PASSWORD_PG_NO_PRODUCTION_CLAIM");
      await login(page, scenario.manager_username);
      await page.locator(`[data-zone-id="${scenario.zone_id}"]`).click();
      const launch = page.getByTestId("find-areas-zone-actions").getByRole("button", { name: "加入季度展店計畫", exact: true });
      await expect(launch).toBeEnabled();await launch.click();
      let dialog = page.getByRole("dialog", { name: "季度展店：區域規劃草稿", exact: true });
      const name = `Synthetic area ${width} ${randomUUID().slice(0, 8)}`;
      await dialog.getByLabel("草稿名稱", { exact: true }).fill(name);
      const saving = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === base);
      await dialog.getByRole("button", { name: "保存區域草稿", exact: true }).click();
      const saved = await saving;expect(saved.status(), await saved.text()).toBe(201);
      const receipt = await saved.json(), id = receipt.draft.draftId;
      expect(receipt.draft).toMatchObject({ name, heatZoneId: scenario.zone_id, quarter: null,
        budget: null, candidateIds: [], createdBy: scenario.manager_username, tenantId: scenario.tenant_id, status: "DRAFT", version: 1, nativeScenarioId: null });
      await expect(dialog.getByText(`已核對區域規劃草稿 ${name} v1。`, { exact: true })).toBeVisible();
      await dialog.getByRole("button", { name: "關閉", exact: true }).click();
      // Reload starts a new authenticated shell and guarded Network read. Wait
      // for that actual API result before checking the action's normal5s state.
      // The original assertion,120s case limit and zero retries stay intact.
      const reloadRead = page.waitForResponse(response => response.request().method() === "GET"
        && new URL(response.url()).pathname === "/api/v1/operator/network-listings");
      await page.reload();
      const reloaded = await reloadRead;expect(reloaded.status(), await reloaded.text()).toBe(200);
      const reloadedSnapshot = await reloaded.json();
      expect(reloadedSnapshot.source).toBe("api");
      expect(reloadedSnapshot.selectedHeatZoneId).toBe(scenario.zone_id);
      expect(reloadedSnapshot.heatZones.map((zone: { id: string }) => zone.id)).toContain(scenario.zone_id);
      await expect(page.locator(`[data-zone-id="${scenario.zone_id}"]`)).toBeVisible();
      await expect(launch).toBeEnabled();await launch.click();
      dialog = page.getByRole("dialog", { name: "季度展店：區域規劃草稿", exact: true });
      await dialog.getByRole("button", { name: `${name} · v1`, exact: true }).click();
      await expect(dialog.getByLabel("草稿名稱", { exact: true })).toHaveValue(name);
      await expect(dialog.getByLabel("規劃季度", { exact: true })).toHaveValue("");
      await expect(dialog.getByLabel("展店預算（元）", { exact: true })).toHaveValue("");
      await dialog.getByLabel("規劃季度", { exact: true }).fill("2027Q2");
      await dialog.getByLabel("展店預算（元）", { exact: true }).fill("0");
      await dialog.getByLabel("Synthetic area candidate", { exact: true }).check();
      await dialog.getByRole("button", { name: "保存草稿修改", exact: true }).click();
      await expect(dialog.getByText(`已核對區域規劃草稿 ${name} v2。`, { exact: true })).toBeVisible();
      const current = await browserRequest(page, `${base}/${id}`, { headers: { "x-operator-role": "expansion-manager" } });
      expect(current.status, current.text).toBe(200);
      const actual = current.json;expect(actual).toMatchObject({ draftId: id, version: 2,
        quarter: "2027Q2", budget: 0, candidateIds: [scenario.candidate_id], status: "DRAFT", nativeScenarioId: null });
      const stale = await browserRequest(page, `${base}/${id}`, { method: "PUT", headers: {
        "x-operator-role": "expansion-manager", "Idempotency-Key": randomUUID() }, data: {
        name, heatZoneId: scenario.zone_id, quarter: "2027Q2", budget: 1, candidateIds: [scenario.candidate_id], expectedVersion: 1 } });
      expect(stale.status).toBe(409);
      const native = await browserRequest(page, "/api/v1/netplan/scenarios", { method: "POST", headers: {
        "x-operator-role": "expansion-manager", "Idempotency-Key": randomUUID() }, data: {
        tenant_id: scenario.tenant_id, scenario_name: "Synthetic no permission elevation", planning_horizon: "2027Q2",
        constraints: { max_budget: 1 }, existing_stores: [], candidate_sites: [] } });
      expect(native.status).toBe(403);
      const foreignPage = await other.newPage();await login(foreignPage, scenario.foreign_username, "expansion-staff");
      const foreign = await browserRequest(foreignPage, `${base}/${id}`, { headers: { "x-operator-role": "expansion-staff" } });
      expect(foreign.status).toBe(404);
      const geometry = await dialog.evaluate(element => {
        const box = element.getBoundingClientRect();return { left: box.left, right: box.right,
          client: element.clientWidth, scroll: element.scrollWidth,
          documentClient: document.documentElement.clientWidth, documentScroll: document.documentElement.scrollWidth,
          fields: [...element.querySelectorAll("input")].map(input => {
            const rect = input.getBoundingClientRect();return { left: rect.left, right: rect.right, width: rect.width };
          }) };
      });
      expect(geometry.left).toBeGreaterThanOrEqual(-1);expect(geometry.right).toBeLessThanOrEqual(width + 1);
      expect(geometry.scroll).toBeLessThanOrEqual(geometry.client + 1);
      expect(geometry.documentScroll).toBeLessThanOrEqual(geometry.documentClient + 1);
      expect(geometry.fields.every(field => field.width > 0 && field.left >= geometry.left - 1 && field.right <= geometry.right + 1)).toBe(true);
      await testInfo.attach(`actual-area-password-${width}-receipt-and-geometry.json`, { body: JSON.stringify({ scope: scenario.scope,
        actual, geometry, staleStatus: stale.status, nativeDeniedStatus: native.status, foreignStatus: foreign.status }, null, 2), contentType: "application/json" });
      await page.screenshot({ path: testInfo.outputPath(`actual-area-draft-${width}px.png`), fullPage: true });
      await dialog.screenshot({ path: testInfo.outputPath(`actual-area-draft-dialog-${width}px.png`) });
      await page.keyboard.press("Escape");await expect(dialog).toHaveCount(0);await expect(launch).toBeFocused();
    } finally { await other.close();await controls.dispose(); }
  });
}
