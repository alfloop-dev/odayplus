import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { acquireOperatorBackendLock, releaseOperatorBackendLock } from "../e2e/_operatorBackendLock";

// Supplemental full-tab evidence, separate from Review Decision dialog coverage.
// Capture authoritative local fixture reads; do not invent reviews or decisions.
test.describe.configure({ timeout: 120_000 });
test.beforeAll(acquireOperatorBackendLock);
test.afterAll(releaseOperatorBackendLock);
test.use({ extraHTTPHeaders: {} });
const phase = process.env.NETWORK_PARITY_CAPTURE_PHASE ?? "after";
const headers = { "x-subject-id": "operator-expansion-manager", "x-roles": "expansion_user,site_reviewer", "x-operator-role": "expansion-manager", "x-tenant-id": "tenant-a" };
async function measure(locator: Locator) {
  return locator.evaluate((el) => {
    const r = el.getBoundingClientRect();
    const css = getComputedStyle(el);
    return { x: r.x, y: r.y, width: r.width, height: r.height, scrollWidth: el.scrollWidth, columns: css.gridTemplateColumns, radius: css.borderRadius };
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
  test(`Review queue and detail at ${width}`, async ({ page, request }, info) => {
    await page.setViewportSize({ width, height: 900 });
    page.setDefaultTimeout(30_000);
    await page.addInitScript(() => {
      sessionStorage.setItem("oday.operator.role", "expansion-manager");
      sessionStorage.setItem("oday.operator.subject", "operator-expansion-manager");
      sessionStorage.setItem("oday.operator.tenant", "tenant-a");
    });
    const api = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
    expect((await request.post(`${api}/api/v1/operator/network-reviews/reset`, { headers })).status()).toBe(200);
    expect((await request.post(`${api}/api/v1/operator/network-listings/reset`, { headers })).status()).toBe(200);
    expect((await request.get("/api/v1/operator/network-reviews", { headers })).status()).toBe(200);
    expect((await request.get("/api/v1/operator/network-listings", { headers })).status()).toBe(200);
    await page.goto("/operator?ws=network");
    await expect(page.getByRole("button", { name: "展店經理", exact: true })).toBeVisible();
    await page.getByTestId("network-tab-5").click();
    const panel = page.getByTestId("network-panel-review");
    const queue = panel.getByTestId("review-queue");
    const card = panel.getByTestId("review-card-RV-701");
    await expect(card).toContainText("王若寧（拓展）");
    await card.click();
    await expect(panel.getByTestId("review-metrics-RV-701")).toBeVisible();
    await expect(page.getByTestId("network-expansion-stepper").getByTestId("network-step-sitescore")).toBeDisabled();
    await page.evaluate(() => scrollTo(0, 0));
    const detail = queue.locator("..").locator(":scope > div").nth(1);
    const metrics = panel.getByTestId("review-metrics-RV-701");
    const boxes = { panel: await measure(panel), queue: await measure(queue), card: await measure(card), detail: await measure(detail), metrics: await measure(metrics) };
    await shot(page, info, `${phase}-review-${width}`);
    let designGeometry;
    if (process.env.NETWORK_PARITY_DESIGN === "1" && width !== 1024) {
      const design = await page.context().newPage();
      await design.setViewportSize({ width, height: 900 });
      await design.route(/^https?:\/\//, (route) => route.abort());
      await design.goto(pathToFileURL(path.resolve("docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/oday-plus-console-r7-standalone.html")).href, { waitUntil: "domcontentloaded" });
      await design.getByRole("button", { name: /林.*營運主管/ }).click();
      await design.getByRole("button", { name: /PM.*稽核.*周明德/ }).click();
      await design.getByRole("button", { name: /展店與店網.*Network/ }).click();
      await design.getByRole("button", { name: /^審核\s+Review/ }).dispatchEvent("click");
      // Let the prototype's transient persona toast disappear without changing
      // its state or clipping the actual content underneath it.
      await expect(design.getByText(/已切換 Demo 角色/)).toBeHidden({ timeout: 15_000 });
      const reference = design.locator('[data-screen-label="Network 選址審核"]');
      await expect(reference).toContainText("RV-701");
      await design.addStyleTag({ content: "*, *::before, *::after { animation: none !important; transition: none !important; }" });
      await shot(design, info, `design-review-${width}`);
      designGeometry = await measure(reference);
      await design.close();
    }
    const document = await page.evaluate(() => ({ width: innerWidth, scrollWidth: window.document.documentElement.scrollWidth }));
    await save(info, `${phase}-geometry-${width}.json`, { boxes, document, design: designGeometry });
    if (phase !== "after") return;
    expect(document.scrollWidth).toBeLessThanOrEqual(width);
    await expect(page.getByRole("main")).toHaveCount(1);
    await expect(card).toHaveAttribute("aria-pressed", "true");
    await expect(panel.getByRole("button", { name: "要求現勘（審核前補件）" })).toBeDisabled();
    await expect(panel.getByTestId("review-recommendation-note-RV-701")).toContainText("系統建議為 WAIT");
    await expect(page.getByTestId("network-expansion-stepper").getByRole("status")).toContainText("此流程尚無候選點審核資料");
    await expect(panel.getByLabel("審核候選點資料").locator("dd")).toHaveCount(7);
    for (const [name, box] of Object.entries(boxes)) {
      expect(box.x, name).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width, name).toBeLessThanOrEqual(width);
      expect(box.scrollWidth, name).toBeLessThanOrEqual(Math.ceil(box.width));
    }
    if (width === 1440) {
      expect(boxes.queue.width).toBe(390);
      expect(boxes.detail.x).toBe(boxes.queue.x + boxes.queue.width + 14);
      if (designGeometry) expect(boxes.panel.width).toBe(designGeometry.width);
    } else if (width === 390) {
      expect(boxes.detail.y).toBeGreaterThanOrEqual(boxes.queue.y + boxes.queue.height);
      expect(boxes.metrics.columns.split(" ")).toHaveLength(2);
    } else {
      expect(boxes.queue.width).toBe(300);
      expect(boxes.detail.x).toBe(boxes.queue.x + boxes.queue.width + 14);
      expect(boxes.metrics.columns.split(" ")).toHaveLength(4);
    }
    const axe = await new AxeBuilder({ page }).include('[data-testid="network-panel-review"]').withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
    await save(info, `axe-review-${width}.json`, axe);
    expect(axe.violations).toEqual([]);
    // Selection is a keyboard-operable control, not a click-only reference div.
    await card.focus();
    await expect(card).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(panel.getByTestId("review-metrics-RV-701")).toBeVisible();
    const trigger = panel.getByTestId("review-btn-go-RV-701");
    await trigger.click();
    await expect(page.getByTestId("review-decision-dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("review-decision-dialog")).toBeHidden();
    await expect(trigger).toBeFocused();
    const snapshot = await request.get(`${api}/api/v1/operator/network-reviews`, { headers });
    expect(snapshot.status()).toBe(200);
    const payload = await snapshot.json();
    expect(payload.decisions).toHaveLength(0);
    expect(payload.auditEvents).toHaveLength(0);
    await save(info, `unchanged-receipt-${width}.json`, payload);
  });
}
