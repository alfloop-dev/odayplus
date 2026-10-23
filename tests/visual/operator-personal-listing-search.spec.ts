import { expect, request as playwrightRequest, test } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { PG_API, PG_CONTROL, PG_WEB } from "../e2e/_intakeScorePg";

// Explicit isolated PostgreSQL composition; real routers, native persistence,
// registered matching worker and production-built UI. No route interception.
// Synthetic identities and facts do not constitute human UAT or production GO.
test.describe.configure({ mode: "serial", timeout: 120_000 });

for (const width of [1440, 1024, 390]) {
  test(`personal search persists, matches receipts, edits and deletes at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    const api = await playwrightRequest.newContext({ baseURL: PG_API,
      extraHTTPHeaders: { "x-synthetic-control": PG_CONTROL } });
    try {
      const prepared = await api.post("/_synthetic/listing-search/setup");
      expect(prepared.status(), await prepared.text()).toBe(200);
      const scenario = await prepared.json();
      expect(scenario.search_scope).toBe("ISOLATED_SYNTHETIC_LISTING_SEARCH_BROWSER_NO_PRODUCTION_CLAIM");
      const headers = { "x-roles": "expansion_user,site_reviewer", "x-operator-role": "expansion-manager",
        "x-tenant-id": scenario.tenant_id, "x-subject-id": scenario.proposer_id };
      await page.setExtraHTTPHeaders(headers);
      await page.addInitScript(({ tenant, subject }) => {
        sessionStorage.setItem("oday.operator.role", "expansion-manager");
        localStorage.setItem("oday.operator.role", "expansion-manager");
        sessionStorage.setItem("oday.operator.subject", subject);
        sessionStorage.setItem("oday.operator.tenant", tenant);
      }, { tenant: scenario.tenant_id, subject: scenario.proposer_id });
      const workspace = `${PG_WEB}/operator?ws=network&tab=areas`;
      await page.goto(workspace);
      // Select the actual server-owned zone; URL guesses and local HZ-01
      // fallback cannot authorize or establish a saved search scope.
      await page.locator(`[data-zone-id="${scenario.zone_id}"]`).click();
      const actions = page.getByTestId("find-areas-zone-actions");
      const launch = actions.getByRole("button", { name: "建立物件搜尋條件", exact: true });
      await expect(launch).toBeEnabled({ timeout: 30_000 });
      await launch.click();
      let dialog = page.getByRole("dialog", { name: "個人物件搜尋條件" });
      const name = `Synthetic personal search ${width} ${randomUUID().slice(0, 8)}`;
      await dialog.getByLabel("條件名稱", { exact: true }).fill(name);
      await dialog.getByLabel("最小坪數", { exact: true }).fill("14");
      await dialog.getByLabel("最大坪數", { exact: true }).fill("26");
      await dialog.getByLabel("每月租金上限（元）", { exact: true }).fill("60000");
      await dialog.getByLabel("僅限一樓", { exact: true }).check();
      const savedResponse = page.waitForResponse(r => r.request().method() === "POST"
        && new URL(r.url()).pathname === "/api/v1/operator/network-listings/searches");
      await dialog.getByRole("button", { name: "保存個人條件", exact: true }).click();
      const saved = await savedResponse;
      expect(saved.status(), await saved.text()).toBe(201);
      const receipt = await saved.json();
      const id = receipt.search.saved_view_id;
      expect(receipt.search).toMatchObject({ name, version: 1, visibility: "PRIVATE", owner_subject_id: scenario.proposer_id });
      await expect(dialog.getByText(`已核對個人搜尋條件 ${name} v1。`, { exact: false })).toBeVisible();
      const drain = await api.post("/_synthetic/listing-search/drain");
      expect(drain.status(), await drain.text()).toBe(200);
      const delivered = await drain.json();
      expect(delivered.matching_jobs.length).toBeGreaterThan(0);
      expect(delivered.matching_jobs.every((job: { status: string }) => job.status === "succeeded")).toBe(true);
      await dialog.getByRole("button", { name: "重新讀取此條件與結果", exact: true }).click();
      await expect(dialog.getByRole("heading", { name: "目前可見的符合進件（1）", exact: true })).toBeVisible();
      await expect(dialog.getByRole("button", { name: new RegExp(`查看進件 ${scenario.intake_id}`) })).toBeVisible();
      const crossSubject = await api.get(`/api/v1/operator/network-listings/searches/${id}`, {
        headers: { ...headers, "x-subject-id": scenario.reviewer_id } });
      expect(crossSubject.status()).toBe(404);
      const crossTenant = await api.get(`/api/v1/operator/network-listings/searches/${id}`, {
        headers: { ...headers, "x-tenant-id": randomUUID() } });
      expect(crossTenant.status()).toBe(404);
      await dialog.getByRole("button", { name: "關閉", exact: true }).click();
      await page.reload();
      await expect(launch).toBeEnabled({ timeout: 30_000 });
      await launch.click();
      dialog = page.getByRole("dialog", { name: "個人物件搜尋條件" });
      await dialog.getByRole("button", { name: `${name} · v1`, exact: true }).click();
      await expect(dialog.getByLabel("每月租金上限（元）", { exact: true })).toHaveValue("60000");
      await expect(dialog.getByRole("heading", { name: "目前可見的符合進件（1）", exact: true })).toBeVisible();
      const geometry = await dialog.evaluate(element => {
        const box = element.getBoundingClientRect();
        const inputs = [...element.querySelectorAll("input")].map(input => {
          const r = input.getBoundingClientRect(); return { x: r.x, right: r.right, width: r.width };
        });
        return { viewport: innerWidth, left: box.left, right: box.right,
          dialogClient: element.clientWidth, dialogScroll: element.scrollWidth,
          documentClient: document.documentElement.clientWidth, documentScroll: document.documentElement.scrollWidth, inputs,
          overflowDiagnostics: [...document.querySelectorAll("body *")].map(node => {
            const rect = node.getBoundingClientRect();
            return { tag: node.tagName, className: node.getAttribute("class"), x: rect.x, right: rect.right };
          }).filter(node => node.right > innerWidth + 1 || node.x < -1).slice(0, 24) };
      });
      await testInfo.attach(`actual-${width}px-geometry-and-receipt.json`, {
        body: JSON.stringify({ scope: scenario.search_scope, geometry, scenario, receipt, delivered,
          crossSubjectStatus: crossSubject.status(), crossTenantStatus: crossTenant.status() }, null, 2), contentType: "application/json" });
      await page.screenshot({ path: testInfo.outputPath(`actual-personal-search-${width}px.png`), fullPage: true });
      await dialog.screenshot({ path: testInfo.outputPath(`actual-personal-search-dialog-${width}px.png`) });
      expect(geometry.left).toBeGreaterThanOrEqual(-1);
      expect(geometry.right).toBeLessThanOrEqual(width + 1);
      expect(geometry.dialogScroll).toBeLessThanOrEqual(geometry.dialogClient + 1);
      expect(geometry.documentScroll).toBeLessThanOrEqual(geometry.documentClient + 1);
      expect(geometry.inputs.every(input => input.width > 0 && input.x >= geometry.left - 1 && input.right <= geometry.right + 1)).toBe(true);
      await dialog.getByRole("button", { name: "關閉", exact: true }).focus();
      await page.keyboard.press("Shift+Tab");
      expect(await dialog.evaluate(element => element.contains(document.activeElement))).toBe(true);
      await dialog.getByLabel("每月租金上限（元）", { exact: true }).fill("45000");
      await dialog.getByRole("button", { name: "保存修改", exact: true }).click();
      await expect(dialog.getByText(`已核對個人搜尋條件 ${name} v2。`, { exact: false })).toBeVisible();
      const secondDrain = await api.post("/_synthetic/listing-search/drain");
      expect(secondDrain.status(), await secondDrain.text()).toBe(200);
      await dialog.getByRole("button", { name: "重新讀取此條件與結果", exact: true }).click();
      await expect(dialog.getByRole("heading", { name: "目前可見的符合進件（0）", exact: true })).toBeVisible();
      await dialog.getByRole("button", { name: "刪除個人條件", exact: true }).click();
      await dialog.getByRole("button", { name: "確認刪除", exact: true }).click();
      await expect(dialog.getByText("已核對：個人搜尋條件已刪除。原有進件與稽核紀錄保留。", { exact: true })).toBeVisible();
      const afterDelete = await api.get(`/api/v1/operator/network-listings/searches/${id}`, { headers });
      expect(afterDelete.status()).toBe(404);
      await page.keyboard.press("Escape");
      await expect(dialog).toHaveCount(0);
      await expect(launch).toBeFocused();
    } finally { await api.dispose(); }
  });
}
