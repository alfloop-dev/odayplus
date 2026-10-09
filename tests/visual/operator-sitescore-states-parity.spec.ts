import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

test.describe.configure({ timeout: 120_000 });
const before = process.env.SITESCORE_STATES_BEFORE === "1";
const phase = before ? "before" : "after";
async function box(locator: Locator) {
  return locator.evaluate((el) => {
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, width: r.width, height: r.height, scrollWidth: el.scrollWidth };
  });
}
async function save(info: TestInfo, name: string, content: unknown) {
  const target = path.join(process.env.NETWORK_PARITY_EVIDENCE_DIR ?? info.outputDir, name);
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, JSON.stringify(content, null, 2));
}
async function shot(page: Page, info: TestInfo, name: string) {
  const target = path.join(process.env.NETWORK_PARITY_EVIDENCE_DIR ?? info.outputDir, `${name}.png`);
  await mkdir(path.dirname(target), { recursive: true });
  await page.evaluate(() => scrollTo(0, 0));
  const clip = await page.evaluate(() => ({ x: 0, y: 0, width: innerWidth, height: document.documentElement.scrollHeight }));
  await page.screenshot({ path: target, fullPage: true, clip, animations: "disabled" });
}

for (const width of [1440, 390]) {
  test(`SiteScore risk / batch / scoped flow at ${width}`, async ({ page, request }, info) => {
    await page.setViewportSize({ width, height: 900 });
    page.setDefaultTimeout(15_000);
    await page.addInitScript(() => {
      sessionStorage.setItem("oday.operator.role", "expansion-manager");
      sessionStorage.setItem("oday.operator.subject", "operator-expansion-manager");
      sessionStorage.setItem("oday.operator.tenant", "tenant-a");
    });
    const api = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
    const headers = { "x-subject-id": "operator-expansion-manager", "x-roles": "expansion_user", "x-operator-role": "expansion-staff", "x-tenant-id": "tenant-a" };
    expect((await request.post(`${api}/api/v1/operator/network-scoring/reset`, { headers })).status()).toBe(200);
    expect((await request.post(`${api}/api/v1/operator/network-listings/reset`, { headers })).status()).toBe(200);
    // Warm the actual BFF/persona scoped reads; do not override the listings journey.
    expect((await request.get("/api/v1/operator/network-scoring", { headers })).status()).toBe(200);
    expect((await request.get("/api/v1/operator/network-listings", { headers })).status()).toBe(200);
    const response = await request.get(`${api}/api/v1/operator/network-scoring`, { headers });
    expect(response.status()).toBe(200);
    const payload = await response.json();
    // Freeze only an actual local fixture service receipt. Do not fabricate gates.
    await page.route("**/api/v1/operator/network-scoring", (route) => route.fulfill({ json: payload }));
    await page.goto("/operator?ws=network");
    await expect(page.getByLabel("Network Find Areas state")).toContainText("4 進行中候選", { timeout: 30_000 });
    await expect(page.getByRole("button", { name: "展店經理", exact: true })).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("network-tab-3").click();
    const panel = page.getByTestId("network-panel-sitescore");
    await expect(panel).toBeVisible();
    await expect(panel.getByTestId("sitescore-card-CS-1001")).toContainText("2026-07-04 06:10", { timeout: 30_000 });
    await panel.getByTestId("sitescore-pick-CS-1002").click();
    const report = panel.getByTestId("sitescore-card-CS-1002");
    await expect(report).toBeVisible();
    const risk = report.getByLabel("Risk breakdown");
    const flow = page.getByTestId("network-expansion-stepper");
    const measures: Record<string, unknown> = { flow: await box(flow), report: await box(report), risk: await box(risk) };
    await shot(page, info, `${phase}-risk-${width}`);
    if (!before) {
      await expect(flow).not.toContainText("Blocked until candidate exists");
      // Separate fixture journey L-2024 -> CS-1001 is not yet converted. Its
      // disabled steps must remain disabled despite four scoring candidates.
      await expect(flow.getByTestId("network-step-sitescore")).toBeDisabled();
      await expect(flow.getByRole("status")).toContainText("此流程");
      const values = risk.locator("dd");
      await expect(values).toHaveCount(6);
      expect(await values.nth(2).getAttribute("data-tone")).toBe("risk");
      expect(await values.nth(3).getAttribute("data-tone")).toBe("good");
      for (const value of await values.all()) {
        const r = await box(value);
        expect(r.scrollWidth).toBeLessThanOrEqual(Math.ceil(r.width));
      }
      const axe = await new AxeBuilder({ page }).include('[aria-label="Risk breakdown"]').withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
      await save(info, `axe-risk-${width}.json`, axe);
      expect(axe.violations).toEqual([]);
    }
    await panel.getByRole("button", { name: "批次評分", exact: true }).click();
    const table = panel.getByTestId("sitescore-batch-table");
    await expect(table).toBeVisible();
    measures.batch = await box(panel);
    measures.table = await box(table);
    measures.tableScroll = await box(table.locator(".."));
    await shot(page, info, `${phase}-batch-${width}`);
    await save(info, `${phase}-geometry-${width}.json`, measures);
    if (!before) {
      const scroll = table.locator("..");
      const scrollBox = await box(scroll);
      expect(scrollBox.x + scrollBox.width).toBeLessThanOrEqual(width);
      if (width === 390) expect(scrollBox.width).toBeGreaterThanOrEqual(300);
      await expect(scroll).toHaveAttribute("tabindex", "0");
      await scroll.focus();
      await expect(scroll).toBeFocused();
      const start = await scroll.evaluate((el) => el.scrollLeft);
      if (width === 390) {
        await page.keyboard.press("ArrowRight");
        await expect.poll(() => scroll.evaluate((el) => el.scrollLeft)).toBeGreaterThan(start);
      }
      const blocked = panel.getByRole("button", { name: /中壢中原候選點/ });
      await expect(blocked).toBeDisabled();
      const action = panel.getByTestId("sitescore-batch-run");
      await expect(action).toBeEnabled();
      for (const candidate of ["信義松仁", "板橋府中", "大安和平"]) {
        const button = panel.getByRole("button", { name: new RegExp(candidate + "候選點.*NT") });
        await expect(button).toHaveAttribute("aria-pressed", "true");
        await button.click();
      }
      await expect(action).toBeDisabled();
      await shot(page, info, `after-batch-no-selection-${width}`);
      const documentWidth = await page.evaluate(() => document.documentElement.scrollWidth);
      expect(documentWidth).toBeLessThanOrEqual(width);
      const batchBox = await box(panel);
      expect(batchBox.x + batchBox.width).toBeLessThanOrEqual(width);
      const axe = await new AxeBuilder({ page }).include('[data-testid="network-panel-sitescore"]').withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
      await save(info, `axe-batch-${width}.json`, axe);
      expect(axe.violations).toEqual([]);
    }
    if (process.env.NETWORK_PARITY_DESIGN === "1") {
      const design = await page.context().newPage();
      await design.setViewportSize({ width, height: 900 });
      await design.route(/^https?:\/\//, (route) => route.abort());
      await design.goto(pathToFileURL(path.resolve("docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/oday-plus-console-r7-standalone.html")).href, { waitUntil: "domcontentloaded" });
      await design.getByRole("button", { name: /林.*營運主管/ }).click();
      await design.getByRole("button", { name: /展店經理.*林曉青/ }).click();
      await design.getByRole("button", { name: /展店與店網.*Network/ }).click();
      await design.getByRole("button", { name: /^SiteScore\s+Score Lab/ }).dispatchEvent("click");
      const designPanel = design.locator('[data-screen-label="Network SiteScore Lab"]');
      await expect(designPanel).toBeVisible();
      await shot(design, info, `design-risk-${width}`);
      await designPanel.getByRole("button", { name: "批次評分", exact: true }).dispatchEvent("click");
      await shot(design, info, `design-batch-${width}`);
      measures.design = await box(designPanel);
      await design.close();
    }
    await save(info, `${phase}-geometry-${width}.json`, measures);
  });
}
