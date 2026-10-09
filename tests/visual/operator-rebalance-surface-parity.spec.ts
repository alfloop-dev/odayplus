import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { acquireOperatorBackendLock, releaseOperatorBackendLock } from "../e2e/_operatorBackendLock";

test.describe.configure({ timeout: 180_000 });
test.beforeAll(acquireOperatorBackendLock);
test.afterAll(releaseOperatorBackendLock);
test.use({ extraHTTPHeaders: {} });
const phase = process.env.NETWORK_PARITY_CAPTURE_PHASE ?? "after";
const headers = { "x-subject-id": "operator-expansion-manager", "x-roles": "expansion_user,site_reviewer", "x-operator-role": "expansion-manager", "x-tenant-id": "tenant-a" };
async function measure(locator: Locator) {
  return locator.evaluate((el) => {
    const r = el.getBoundingClientRect();
    const css = getComputedStyle(el);
    return { x: r.x, y: r.y, width: r.width, height: r.height, scrollWidth: el.scrollWidth, columns: css.gridTemplateColumns, overflowX: css.overflowX };
  });
}
async function artifact(info: TestInfo, name: string) {
  const target = path.join(process.env.NETWORK_PARITY_EVIDENCE_DIR ?? info.outputDir, name);
  await mkdir(path.dirname(target), { recursive: true });
  return target;
}
async function save(info: TestInfo, name: string, content: unknown) {
  await writeFile(await artifact(info, name), JSON.stringify(content, null, 2));
}
async function shot(page: Page, info: TestInfo, name: string) {
  await page.evaluate(() => scrollTo(0, 0));
  const clip = await page.evaluate(() => ({ x: 0, y: 0, width: innerWidth, height: document.documentElement.scrollHeight }));
  await page.screenshot({ path: await artifact(info, `${name}.png`), fullPage: true, clip, animations: "disabled" });
}
for (const width of [1440, 1024, 390]) {
  test(`Rebalance source signals, AVM and blocked NetPlan at ${width}`, async ({ page, request }, info) => {
    await page.setViewportSize({ width, height: 900 });
    page.setDefaultTimeout(30_000);
    await page.addInitScript(() => {
      sessionStorage.setItem("oday.operator.role", "expansion-manager");
      sessionStorage.setItem("oday.operator.subject", "operator-expansion-manager");
      sessionStorage.setItem("oday.operator.tenant", "tenant-a");
    });
    const api = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
    expect((await request.post(`${api}/api/v1/operator/network-rebalance/reset`, { headers })).status()).toBe(200);
    expect((await request.get("/api/v1/operator/network-rebalance", { headers })).status()).toBe(200);
    await page.goto("/operator?ws=network");
    await page.getByTestId("network-tab-6").click();
    const panel = page.getByTestId("network-panel-rebalance");
    const primary = panel.getByTestId("rebalance-primary-action");
    const detail = panel.getByTestId("rebalance-detail-RB-801");
    await expect(primary).toContainText("建立 AVM 估值請求", { timeout: 30_000 });
    await expect(detail).toContainText("新北板橋文化");
    const designBoxes: Record<string, unknown> = {};
    if (process.env.NETWORK_PARITY_DESIGN === "1" && width !== 1024) {
      const design = await page.context().newPage();
      await design.setViewportSize({ width, height: 900 });
      await design.route(/^https?:\/\//, (route) => route.abort());
      await design.goto(pathToFileURL(path.resolve("docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/oday-plus-console-r7-standalone.html")).href, { waitUntil: "domcontentloaded" });
      await design.getByRole("button", { name: /林.*營運主管/ }).click();
      await design.getByRole("button", { name: /展店經理.*林曉青/ }).click();
      await design.getByRole("button", { name: /展店與店網.*Network/ }).click();
      await design.getByRole("button", { name: /^低效重配\s+Rebalance/ }).dispatchEvent("click");
      await expect(design.getByText(/已切換 Demo 角色/)).toBeHidden({ timeout: 15_000 });
      const reference = design.locator('[data-screen-label="Network 低效重配"]');
      await expect(reference).toContainText("90 天營收趨勢");
      await shot(design, info, `design-rebalance-${width}`);
      designBoxes.initial = await measure(reference);
      // Reference demo advances only; no product response or reference content invented.
      const referenceCta = reference.getByRole("button", { name: "建立 AVM 估值請求", exact: true });
      await referenceCta.dispatchEvent("click");
      await reference.getByRole("button", { name: /AVM 完成/ }).dispatchEvent("click");
      await expect(reference).toContainText("P50 公允價值");
      await shot(design, info, `design-avm-${width}`);
      await reference.getByRole("button", { name: /建立 NetPlan Review/ }).dispatchEvent("click");
      await expect(reference).toContainText("NETPLAN 三案");
      await shot(design, info, `design-netplan-${width}`);
      await design.close();
    }
    async function capture(state: string) {
      const boxes = { panel: await measure(panel), layout: await measure(panel.locator("section").first()), list: await measure(panel.getByLabel("Rebalance candidates")), card: await measure(panel.getByTestId("rebalance-card-RB-801")), detail: await measure(detail), stepper: await measure(detail.getByLabel("Rebalance workflow")), primary: await measure(primary) };
      const document = await page.evaluate(() => ({ width: innerWidth, scrollWidth: window.document.documentElement.scrollWidth }));
      await shot(page, info, `${phase}-${state}-${width}`);
      await save(info, `${phase}-${state}-geometry-${width}.json`, { boxes, document, design: designBoxes });
      const axe = await new AxeBuilder({ page }).include('[data-testid="network-panel-rebalance"]').withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
      await save(info, `${phase}-${state}-axe-${width}.json`, axe);
      if (phase !== "after") return;
      expect(document.scrollWidth).toBeLessThanOrEqual(width);
      await expect(page.getByRole("main")).toHaveCount(1);
      for (const [name, box] of Object.entries(boxes)) {
        expect(box.x, name).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width, name).toBeLessThanOrEqual(width);
        expect(box.scrollWidth, name).toBeLessThanOrEqual(Math.ceil(box.width));
      }
      if (width === 1440) {
        expect(boxes.list.width).toBe(330);
        expect(boxes.detail.x).toBe(boxes.list.x + 344);
      } else if (width === 1024) {
        expect(boxes.list.width).toBe(280);
        expect(boxes.detail.x).toBe(boxes.list.x + 294);
      } else expect(boxes.detail.y).toBeGreaterThanOrEqual(boxes.list.y + boxes.list.height);
      expect(axe.violations).toEqual([]);
    }
    await capture("rebalance");
    await primary.focus();
    await page.keyboard.press("Enter");
    await expect(primary).toContainText("完成 AVM job");
    await primary.click();
    await expect(primary).toContainText("建立 NetPlan Review");
    await expect(panel.getByTestId("rebalance-avm-RB-801")).toContainText("service output");
    await capture("avm");
    await primary.click();
    await expect(panel.getByTestId("rebalance-scenario-move")).toBeEnabled();
    await panel.getByTestId("rebalance-scenario-move").focus();
    await page.keyboard.press("Enter");
    await expect(panel.getByTestId("rebalance-scenario-move")).toHaveAttribute("aria-pressed", "true");
    await expect(panel.getByTestId("rebalance-blocked-alert")).toContainText("CONSTRUCTION");
    await expect(primary).toBeDisabled();
    await expect(panel.getByTestId("rebalance-acknowledgement-section")).toHaveCount(0);
    await capture("netplan");
    const snapshot = await request.get(`${api}/api/v1/operator/network-rebalance`, { headers });
    expect(snapshot.status()).toBe(200);
    const payload = await snapshot.json();
    const store = payload.stores.find((item: { id: string }) => item.id === "RB-801");
    expect(store).toMatchObject({ status: "netplanreview", selectedScenarioId: "move", relatedApprovalId: null, relocationExecuted: false });
    await save(info, `durable-selection-${width}.json`, payload);
    await page.reload();
    await page.getByTestId("network-tab-6").click();
    await expect(primary).toBeDisabled();
    await expect(panel.getByTestId("rebalance-selection-RB-801")).toContainText("EV-SEL-", { timeout: 30_000 });
    await expect(panel.getByTestId("rebalance-boundary-RB-801")).toHaveAttribute("data-relocation-executed", "false");
  });
}
