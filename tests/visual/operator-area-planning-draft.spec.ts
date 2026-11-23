import { expect, request as playwrightRequest, test, type Locator, type Page, type Response } from "@playwright/test";
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
async function dialogGeometry(dialog: Locator, width: number) {
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
  return geometry;
}
// The executive principal is granted ops-lead, expansion-manager and pm-audit
// personas; the server starts it on ops-lead. The person then selects the
// expansion persona in the normal header role menu (selection among grants only).
async function selectExpansionManagerPersona(page: Page) {
  await page.getByRole("button", { name: /營運主管/, expanded: false }).click();
  await page.locator('[data-screen-label="Role Switch Menu"]').getByRole("button", { name: /^展店經理/ }).click();
  await expect(page.getByRole("button", { name: /展店經理/, expanded: false })).toBeVisible();
}
async function openAreaDialog(page: Page, zoneId: string) {
  await page.locator(`[data-zone-id="${zoneId}"]`).click();
  const launch = page.getByTestId("find-areas-zone-actions").getByRole("button", { name: "加入季度展店計畫", exact: true });
  await expect(launch).toBeEnabled();await launch.click();
  return page.getByRole("dialog", { name: "季度展店：區域規劃草稿", exact: true });
}
const path = (response: Response) => new URL(response.url()).pathname;
const nativeBase = "/api/v1/netplan/scenarios/";
// Fresh area GET plus the linked native GET that the dialog issues for one row.
function linkedReads(page: Page, id: string) {
  return Promise.all([
    page.waitForResponse(response => response.request().method() === "GET" && path(response) === `${base}/${id}`),
    page.waitForResponse(response => response.request().method() === "GET" && path(response).startsWith(nativeBase)),
  ]);
}
for (const width of [1440, 1024, 390]) {
  test(`area draft persists and reopens through password session at ${width}px`, async ({ page, browser }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    const controls = await playwrightRequest.newContext({ baseURL: PG_API,
      extraHTTPHeaders: { "x-synthetic-control": PG_CONTROL } });
    const other = await browser.newContext();
    const executiveContext = await browser.newContext({ viewport: { width, height: 1000 } });
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
      // This owning read comes from the normal signed API and persisted HeatZone,
      // independently of the seven-row local fixture geometry qualification.
      expect(reloadedSnapshot.heatZoneAvailability).toMatchObject({ state: "available", source: "canonical-heatzone" });
      const recordedZone = reloadedSnapshot.heatZones.find((zone: { id: string }) => zone.id === scenario.zone_id);
      expect(recordedZone).toMatchObject({ id: scenario.zone_id, confidence: 0.6, recordedMap: {
        id: scenario.zone_id, score: 60, confidence: 0.6,
        modelVersion: "declared-area-browser-only", featureVersion: "declared-area-browser-only",
        sourceSnapshotIds: ["declared-area-browser-source"], competitorPressureScore: null, executionMode: "",
      } });
      await expect(page.getByTestId("find-areas-tray").locator('button[aria-current="true"]'))
        .toHaveAttribute("data-zone-id", scenario.zone_id);
      const facts = page.getByTestId("find-areas-zone-facts");
      await expect(facts.locator("dt")).toHaveText([
        "未滿足需求", "店型適配", "競爭壓力", "自家稀釋風險", "租金可行性", "物件供給", "資料信心", "評估模式",
      ]);
      for (const [label, value] of [["資料信心", "60%"], ["競爭壓力", "未評估"], ["評估模式", "未知"]] as const) {
        await expect(facts.locator("div").filter({ has: page.getByText(label, { exact: true }) }).locator("dd"))
          .toHaveText(value);
      }
      const recordedFacts = await facts.locator("div").evaluateAll(rows => rows.map(row => ({
        label: row.querySelector("dt")?.textContent, value: row.querySelector("dd")?.textContent,
      })));
      const nativeGeometry = await page.getByTestId("network-panel-find-areas").evaluate(panel => ({
        columns: Array.from(panel.children, child => Math.round(child.getBoundingClientRect().width)),
        mapHeight: Math.round(document.querySelector('[data-testid="find-areas-map-frame"]')!.getBoundingClientRect().height),
        documentClient: document.documentElement.clientWidth, documentScroll: document.documentElement.scrollWidth,
      }));
      expect(nativeGeometry.documentScroll).toBeLessThanOrEqual(nativeGeometry.documentClient);
      if (width === 1440) {
        expect(nativeGeometry.columns).toEqual([206, 836, 330]);
        expect(nativeGeometry.mapHeight).toBe(426);
      }
      await page.screenshot({ path: testInfo.outputPath(`actual-find-areas-recorded-${width}px.png`), fullPage: true });
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
      const geometry = await dialogGeometry(dialog, width);
      await testInfo.attach(`actual-area-password-${width}-receipt-and-geometry.json`, { body: JSON.stringify({ scope: scenario.scope,
        actual, geometry, nativeGeometry, recordedProjection: { source: reloadedSnapshot.source,
          selectedHeatZoneId: reloadedSnapshot.selectedHeatZoneId, availability: reloadedSnapshot.heatZoneAvailability,
          recordedMap: recordedZone.recordedMap, facts: recordedFacts },
        staleStatus: stale.status, nativeDeniedStatus: native.status, foreignStatus: foreign.status }, null, 2), contentType: "application/json" });
      await page.screenshot({ path: testInfo.outputPath(`actual-area-draft-${width}px.png`), fullPage: true });
      await dialog.screenshot({ path: testInfo.outputPath(`actual-area-draft-dialog-${width}px.png`) });
      await page.keyboard.press("Escape");await expect(dialog).toHaveCount(0);await expect(launch).toBeFocused();

      // A separate same-tenant executive (the native NetPlan creator) opens the
      // same persisted completed draft through its own password session and BFF,
      // explicitly creates the native DRAFT, then reloads and reopens it.
      const executive = await executiveContext.newPage();
      const writes: string[] = [];
      executive.on("request", sent => {
        if (sent.method() !== "GET" && new URL(sent.url()).pathname.startsWith("/api/v1/")) {
          writes.push(`${sent.method()} ${new URL(sent.url()).pathname}`);
        }
      });
      await login(executive, scenario.executive_username);await selectExpansionManagerPersona(executive);
      let executiveDialog = await openAreaDialog(executive, scenario.zone_id);
      await executiveDialog.getByRole("button", { name: `${name} · v2`, exact: true }).click();
      await expect(executiveDialog.getByLabel("規劃季度", { exact: true })).toHaveValue("2027Q2");
      await expect(executiveDialog.getByLabel("展店預算（元）", { exact: true })).toHaveValue("0");
      await expect(executiveDialog.getByLabel("Synthetic area candidate", { exact: true })).toBeChecked();
      const create = executiveDialog.getByRole("button", { name: "建立 NetPlan 情境草稿", exact: true });
      await expect(create).toBeEnabled();
      const materializing = executive.waitForResponse(response => response.request().method() === "POST"
        && path(response) === `${base}/${id}/materialize`);
      const afterMaterialize = linkedReads(executive, id);
      await create.click();
      const materialized = await materializing;expect(materialized.status(), await materialized.text()).toBe(200);
      const linkReceipt = await materialized.json(), linked = linkReceipt.draft;
      expect(linked).toMatchObject({ draftId: id, tenantId: scenario.tenant_id, heatZoneId: scenario.zone_id, name,
        quarter: "2027Q2", budget: 0, candidateIds: [scenario.candidate_id], createdBy: scenario.manager_username,
        materializedBy: scenario.executive_username, status: "MATERIALIZED", version: 3 });
      expect(linkReceipt.replayed).toBe(false);
      const scenarioId: string = linked.nativeScenarioId;
      expect(scenarioId).toEqual(expect.any(String));
      const nativeExpected = { scenario_id: scenarioId, tenant_id: scenario.tenant_id, scenario_name: name,
        planning_horizon: "2027Q2", status: "draft", constraints: { max_budget: 0 }, solve: null, approvals: [] };
      const verifyNative = (detail: { options_by_entity: Record<string, { source_snapshot_ids: string[] }[]> }) => {
        expect(detail).toMatchObject(nativeExpected);
        expect(Object.keys(detail.options_by_entity)).toEqual([scenario.candidate_id]);
        // ID-only original mapping: no source, model, currency or GM is invented.
        expect(detail.options_by_entity[scenario.candidate_id].every(option => option.source_snapshot_ids.length === 0)).toBe(true);
      };
      const [freshArea, freshNative] = await afterMaterialize;
      expect(freshArea.status(), await freshArea.text()).toBe(200);expect(await freshArea.json()).toEqual(linked);
      expect(path(freshNative)).toBe(nativeBase + scenarioId);
      expect(freshNative.status(), await freshNative.text()).toBe(200);
      const createdNative = await freshNative.json();verifyNative(createdNative);
      const verifyReadback = async (target: Locator) => {
        const readback = target.getByRole("region", { name: "NetPlan 情境草稿讀回" });
        await expect(readback).toContainText(`${name} · 2027Q2 · 狀態 draft · 預算上限 0`);
        await expect(readback).toContainText("此情境尚未解算或送審");
        await expect(readback).toContainText("期望毛利／成本／風險／產能：未提供來源，數值尚未核實（不代表 0 或預設值）");
        await expect(readback).not.toContainText("產能 1");
        await expect(readback).not.toContainText("期望毛利 0");
        await expect(target.getByLabel("草稿名稱", { exact: true })).toBeDisabled();
        await expect(target.getByRole("button", { name: "建立 NetPlan 情境草稿", exact: true })).toHaveCount(0);
      };
      await expect(executiveDialog.getByText(`已核對區域規劃草稿 ${name} 與其 NetPlan 情境草稿。`, { exact: true })).toBeVisible();
      await verifyReadback(executiveDialog);
      const executiveGeometry = await dialogGeometry(executiveDialog, width);
      await executiveDialog.screenshot({ path: testInfo.outputPath(`actual-area-native-created-${width}px.png`) });

      await executive.reload();await expect(executive).toHaveURL(url => url.pathname === "/operator");
      await selectExpansionManagerPersona(executive);
      executiveDialog = await openAreaDialog(executive, scenario.zone_id);
      const reopening = linkedReads(executive, id);
      await executiveDialog.getByRole("button", { name: `${name} · v3 · 已建立 NetPlan`, exact: true }).click();
      const [reopenedArea, reopenedNative] = await reopening;
      expect(reopenedArea.status(), await reopenedArea.text()).toBe(200);expect(await reopenedArea.json()).toEqual(linked);
      expect(path(reopenedNative)).toBe(nativeBase + scenarioId);
      expect(reopenedNative.status(), await reopenedNative.text()).toBe(200);
      const reopenedDetail = await reopenedNative.json();verifyNative(reopenedDetail);
      expect(reopenedDetail.created_at).toBe(createdNative.created_at);
      await verifyReadback(executiveDialog);
      // Only the explicit materialize command was sent: no retry, solve, submit or approval.
      expect(writes).toEqual([`POST ${base}/${id}/materialize`]);
      await testInfo.attach(`actual-area-native-${width}-receipt-and-readback.json`, { body: JSON.stringify({
        scope: scenario.scope, linked, createdNative, reopenedNative: reopenedDetail, writes, geometry: executiveGeometry,
      }, null, 2), contentType: "application/json" });
      await executiveDialog.screenshot({ path: testInfo.outputPath(`actual-area-native-reopened-${width}px.png`) });
      if (width === 390) {
        // Narrow window: the shared Modal is itself the scroll container, so its
        // header close and footer cancel scroll with the body. A real wheel scroll
        // (not Playwright's actionability auto-scroll) must bring each original
        // control into the viewport; it is then pressed at that visible point and
        // focus must return to this executive page's own launcher.
        const executiveLaunch = executive.getByTestId("find-areas-zone-actions")
          .getByRole("button", { name: "加入季度展店計畫", exact: true });
        const scrollOf = (target: Locator) => target.evaluate(element => ({
          top: element.scrollTop, max: element.scrollHeight - element.clientHeight }));
        const wheelTo = async (target: Locator, end: "top" | "bottom") => {
          const box = (await target.boundingBox())!;
          await executive.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
          const before = await scrollOf(target);
          await expect.poll(async () => {
            await executive.mouse.wheel(0, end === "bottom" ? 400 : -400);
            const now = await scrollOf(target);return end === "bottom" ? now.max - now.top : now.top;
          }, { timeout: 15_000 }).toBeLessThanOrEqual(1);
          const after = await scrollOf(target);
          return { from: before.top, to: after.top, max: after.max };
        };
        // Visible inside both the window and the dialog's scrollport, and the
        // topmost element at its centre is the control itself (nothing covers it).
        const visibility = (control: Locator) => control.evaluate(element => {
          const rect = element.getBoundingClientRect();
          const port = element.closest('[role="dialog"]')!.getBoundingClientRect();
          const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2, hit = document.elementFromPoint(x, y);
          return { x, y, top: rect.top, bottom: rect.bottom, inViewport: rect.top >= Math.max(0, port.top) - 1
            && rect.bottom <= Math.min(window.innerHeight, port.bottom) + 1 && rect.left >= 0 && rect.right <= window.innerWidth,
          hit: hit !== null && element.contains(hit) };
        });
        const pressVisible = async (target: Locator, control: Locator, expectedTop: number) => {
          const seen = await visibility(control);
          expect(seen).toMatchObject({ inViewport: true, hit: true });
          expect((await scrollOf(target)).top).toBe(expectedTop);
          await executive.mouse.click(seen.x, seen.y);
          await expect(target).toHaveCount(0);await expect(executiveLaunch).toBeFocused();
          return seen;
        };
        const cancel = executiveDialog.getByRole("button", { name: "取消", exact: true });
        const cancelBefore = await visibility(cancel);
        expect(cancelBefore.inViewport).toBe(false);
        const down = await wheelTo(executiveDialog, "bottom");
        expect(down.to - down.from).toBeGreaterThan(0);
        await executiveDialog.screenshot({ path: testInfo.outputPath(`actual-area-native-scrolled-cancel-${width}px.png`) });
        const cancelPressed = await pressVisible(executiveDialog, cancel, down.to);

        // Reopen the same persisted native-linked draft through the original
        // launcher and fresh area/native GETs; nothing is re-created.
        executiveDialog = await openAreaDialog(executive, scenario.zone_id);
        const reopeningAgain = linkedReads(executive, id);
        await executiveDialog.getByRole("button", { name: `${name} · v3 · 已建立 NetPlan`, exact: true }).click();
        const [againArea, againNative] = await reopeningAgain;
        expect(againArea.status(), await againArea.text()).toBe(200);expect(await againArea.json()).toEqual(linked);
        expect(path(againNative)).toBe(nativeBase + scenarioId);
        expect(againNative.status(), await againNative.text()).toBe(200);
        const againDetail = await againNative.json();verifyNative(againDetail);
        expect(againDetail.created_at).toBe(createdNative.created_at);
        await verifyReadback(executiveDialog);
        const close = executiveDialog.getByRole("button", { name: "關閉", exact: true });
        const bottom = await wheelTo(executiveDialog, "bottom");
        const closeBefore = await visibility(close);
        expect(closeBefore.inViewport).toBe(false);
        const up = await wheelTo(executiveDialog, "top");
        expect(up.from - up.to).toBeGreaterThan(0);
        await executiveDialog.screenshot({ path: testInfo.outputPath(`actual-area-native-scrolled-close-${width}px.png`) });
        const closePressed = await pressVisible(executiveDialog, close, up.to);
        await testInfo.attach(`actual-area-native-${width}-scroll-dismissal.json`, { body: JSON.stringify({
          scope: scenario.scope, cancel: { before: cancelBefore, scroll: down, pressed: cancelPressed },
          reopened: { draft: linked, nativeScenarioId: againDetail.scenario_id, createdAt: againDetail.created_at },
          close: { bottom, before: closeBefore, scroll: up, pressed: closePressed }, writes,
        }, null, 2), contentType: "application/json" });
      }
      // After every added interaction the persisted record is unchanged and the
      // explicit materialize remains the only write.
      const finalArea = await browserRequest(executive, `${base}/${id}`, { headers: { "x-operator-role": "expansion-manager" } });
      expect(finalArea.status, finalArea.text).toBe(200);expect(finalArea.json).toEqual(linked);
      expect(writes).toEqual([`POST ${base}/${id}/materialize`]);
    } finally { await other.close();await executiveContext.close();await controls.dispose(); }
  });
}
