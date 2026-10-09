import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { acquireOperatorBackendLock, releaseOperatorBackendLock } from "../e2e/_operatorBackendLock";

// Supplemental continuous-detail evidence, not a substitute for modal/state
// acceptance. Missing assignment/SLA authority must remain fail-closed.
test.describe.configure({ mode: "serial", timeout: 120_000 });
test.beforeAll(acquireOperatorBackendLock);
test.afterAll(releaseOperatorBackendLock);
test.use({ extraHTTPHeaders: {} });
const expectReady = expect.configure({ timeout: 15_000 });
const headers = { "x-subject-id": "operator-expansion-manager", "x-roles": "expansion_user,site_reviewer", "x-operator-role": "expansion-manager", "x-tenant-id": "tenant-a" };
const sectionIds = ["intake-detail-header", "intake-submission-summary", "assignment-sla-summary", "intake-processing-stages", "intake-source-policy-evidence", "intake-parsed-lineage", "intake-match-evidence-section", "intake-comparison-decision-section", "intake-receipts-section", "intake-timeline-audit-section"];

async function measure(locator: Locator) {
  return locator.evaluate((element) => {
    const box = element.getBoundingClientRect();
    const css = getComputedStyle(element);
    return { x: box.x, y: box.y, width: box.width, height: box.height, scrollWidth: element.scrollWidth, padding: css.padding, gap: css.gap, radius: css.borderRadius };
  });
}
async function artifact(info: TestInfo, name: string) {
  const target = process.env.NETWORK_PARITY_EVIDENCE_DIR ? path.join(process.env.NETWORK_PARITY_EVIDENCE_DIR, name) : info.outputPath(name);
  await mkdir(path.dirname(target), { recursive: true });
  return target;
}
async function shot(page: Page, info: TestInfo, name: string) {
  const target = await artifact(info, `${name}.png`);
  await page.screenshot({ path: target, animations: "disabled" });
  await info.attach(name, { path: target, contentType: "image/png" });
}

for (const width of [1440, 390]) {
  test(`continuous detail geometry and unavailable authority at ${width}`, async ({ page, request }, info) => {
    page.setDefaultTimeout(15_000);
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => {
      sessionStorage.setItem("oday.operator.role", "expansion-manager");
      sessionStorage.setItem("oday.operator.subject", "operator-expansion-manager");
      sessionStorage.setItem("oday.operator.tenant", "tenant-a");
    });
    const api = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
    expect((await request.post(`${api}/api/v1/operator/network-listings/reset`, { headers })).status()).toBe(200);
    expect((await request.get("/api/v1/operator/network-listings/intake", { headers })).status()).toBe(200);
    await page.goto("/operator?ws=network");
    await expectReady(page.getByRole("button", { name: "展店經理", exact: true })).toBeVisible();
    await page.getByTestId("network-tab-1").click();
    await expectReady(page.getByTestId("intake-inbox-empty")).toBeVisible();
    await page.getByTestId("intake-add-button").click();
    await page.getByTestId("intake-url-input").fill("https://www.synthetic.example/detail-77120345.html");
    await page.getByTestId("intake-submit-button").click();
    await expectReady(page.getByTestId("intake-detail-stage")).toHaveText("可決策");
    const detail = page.getByTestId("intake-detail-dialog");
    const intakeId = (await page.getByTestId("intake-detail-id").innerText()).trim();
    const response = await request.get(`${api}/api/v1/operator/network-listings/intake/${intakeId}`, { headers });
    expect(response.status()).toBe(200);
    const record = await response.json();
    await writeFile(await artifact(info, `authority-${width}.json`), JSON.stringify({ status: response.status(), record }, null, 2));
    // Do not fabricate resource IDs/versions to make reference controls appear.
    expect(record.assignmentId ?? null).toBeNull();
    expect(record.slaInstanceId ?? null).toBeNull();
    for (const id of ["asg-btn-claim", "asg-btn-transfer", "asg-btn-pause", "asg-btn-resume"]) await expect(page.getByTestId(id)).toHaveCount(0);
    for (const id of ["assignment-action-unavailable", "sla-action-unavailable", "assignment-resource-version-unavailable", "sla-resource-version-unavailable"]) await expect(page.getByTestId(id)).toBeVisible();
    const phase = process.env.NETWORK_PARITY_CAPTURE_PHASE ?? "after";
    if (phase === "after") await expect(page.getByRole("main")).toHaveCount(1);
    let designBox;
    if (process.env.NETWORK_PARITY_DESIGN === "1") {
      const design = await page.context().newPage();
      await design.setViewportSize({ width, height: 900 });
      await design.route(/^https?:\/\//, (route) => route.abort());
      await design.goto(pathToFileURL(path.resolve("docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/oday-plus-console-r7-standalone.html")).href, { waitUntil: "domcontentloaded" });
      await design.getByRole("button", { name: /林.*營運主管/ }).click();
      await design.getByRole("button", { name: /展店經理.*林曉青/ }).click();
      await design.getByRole("button", { name: /展店與店網.*Network/ }).click();
      await design.getByRole("button", { name: /^物件雷達\s+Listing Radar/ }).dispatchEvent("click");
      await design.getByRole("listitem").filter({ hasText: "IN-3001" }).dispatchEvent("click");
      const reference = design.locator('[data-screen-label="Intake 收件處理詳情頁"]');
      await expect(reference).toBeVisible();
      designBox = await measure(reference.locator(":scope > div"));
      await shot(design, info, `design-detail-${width}`);
      // Preserve reference-only conditional-control comparisons even while the
      // product read model has no authority. These are NOT implementation pairs.
      for (const mode of ["transfer", "pause"]) {
        await reference.getByRole("button", { name: mode === "transfer" ? "轉交" : "暫停 SLA", exact: true }).dispatchEvent("click");
        const modal = design.locator('[data-screen-label="Dialog 轉交／暫停"]');
        await expect(modal).toBeVisible();
        await shot(design, info, `design-${mode}-${width}`);
        await modal.getByRole("button", { name: "關閉", exact: true }).dispatchEvent("click");
      }
      await design.close();
    }
    const boxes: Record<string, Awaited<ReturnType<typeof measure>>> = { detail: await measure(detail) };
    for (const id of sectionIds) boxes[id] = await measure(page.getByTestId(id));
    const document = await page.evaluate(() => ({ width: innerWidth, scrollWidth: window.document.documentElement.scrollWidth }));
    await writeFile(await artifact(info, `${phase}-geometry-detail-${width}.json`), JSON.stringify({ boxes, document, design: designBox }, null, 2));
    expect(document.scrollWidth).toBeLessThanOrEqual(width);
    for (const [id, box] of Object.entries(boxes)) {
      expect(box.x, id).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width, id).toBeLessThanOrEqual(width);
      if (phase === "after") expect(box.scrollWidth, id).toBeLessThanOrEqual(Math.ceil(box.width));
    }
    await page.getByTestId("intake-detail-header").scrollIntoViewIfNeeded();
    await shot(page, info, `${phase}-detail-${width}`);
    await page.getByTestId("intake-timeline-audit-section").scrollIntoViewIfNeeded();
    await shot(page, info, `${phase}-detail-audit-${width}`);
    if (phase === "after") {
      expect(boxes.detail.gap).toBe("12px");
      const results = await new AxeBuilder({ page }).include('[data-testid="intake-detail-dialog"]').analyze();
      await writeFile(await artifact(info, `axe-detail-${width}.json`), JSON.stringify(results, null, 2));
      expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
    }
    // VDC-004: persisted route restores the record; return exposes real inbox.
    await page.reload();
    await expectReady(page.getByTestId("intake-detail-id")).toHaveText(intakeId);
    await page.getByTestId("intake-return-button").click();
    await expectReady(page.getByTestId(`intake-inbox-row-${intakeId}`)).toBeVisible();
  });
}
