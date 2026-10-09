import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { acquireOperatorBackendLock, releaseOperatorBackendLock } from "../e2e/_operatorBackendLock";

// Supplemental visual evidence, outside the business inventory. Actual isolated
// fixture writes and a distinct reviewer subject; no product route interception.
test.describe.configure({ mode: "serial", timeout: 120_000 });
test.beforeAll(acquireOperatorBackendLock);
test.afterAll(releaseOperatorBackendLock);
test.use({ extraHTTPHeaders: {} });
const ready = expect.configure({ timeout: 15_000 });
const headers = { "x-subject-id": "operator-expansion-manager", "x-roles": "expansion_user,site_reviewer", "x-operator-role": "expansion-manager", "x-tenant-id": "tenant-a" };
async function measure(locator: Locator) {
  return locator.evaluate((element) => {
    const b = element.getBoundingClientRect(), css = getComputedStyle(element);
    return { x: b.x, y: b.y, width: b.width, height: b.height, scrollWidth: element.scrollWidth, radius: css.borderRadius, padding: css.padding, gap: css.gap, overflowY: css.overflowY };
  });
}
async function artifact(info: TestInfo, name: string) {
  const target = process.env.NETWORK_PARITY_EVIDENCE_DIR ? path.join(process.env.NETWORK_PARITY_EVIDENCE_DIR, name) : info.outputPath(name);
  await mkdir(path.dirname(target), { recursive: true });
  return target;
}
async function shot(page: Page, info: TestInfo, name: string) {
  await page.screenshot({ path: await artifact(info, `${name}.png`), animations: "disabled" });
}
for (const width of [1440, 390]) {
  test(`Promotion paired geometry and second actor at ${width}`, async ({ page, request }, info) => {
    page.setDefaultTimeout(15_000);
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => {
      sessionStorage.setItem("oday.operator.role", "expansion-manager");
      if (!sessionStorage.getItem("oday.operator.subject")) sessionStorage.setItem("oday.operator.subject", "operator-expansion-manager");
      sessionStorage.setItem("oday.operator.tenant", "tenant-a");
    });
    const api = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
    expect((await request.post(`${api}/api/v1/operator/network-listings/reset`, { headers })).status()).toBe(200);
    expect((await request.get("/api/v1/operator/network-listings/intake", { headers })).status()).toBe(200);
    await page.goto("/operator?ws=network");
    await ready(page.getByRole("button", { name: "展店經理", exact: true })).toBeVisible();
    await page.getByTestId("network-tab-1").click();
    await ready(page.getByTestId("intake-inbox-empty")).toBeVisible();
    await page.getByTestId("intake-add-button").click();
    await page.getByTestId("intake-url-input").fill("https://www.synthetic.example/detail-77120345.html");
    await page.getByTestId("intake-submit-button").click();
    await ready(page.getByTestId("intake-detail-stage")).toHaveText("可決策");
    await page.getByTestId("decide-action-create").click();
    await page.getByTestId("intake-decide-reason").fill("已核對來源與物件身分，建立新物件供第二人晉升審查。");
    await page.getByTestId("intake-decide-risk-ack").click();
    await page.getByTestId("intake-decide-submit").click();
    await ready(page.getByTestId("promotion-request-form")).toBeVisible();
    await page.getByTestId("promotion-request-reason").fill("資料完整且符合展店門檻，申請晉升並等待獨立第二人審查。");
    await page.getByTestId("promotion-request-ack").click();
    await page.getByTestId("promotion-request-submit").click();
    await ready(page.getByTestId("promotion-status-badge")).toContainText("PENDING_REVIEW");
    await expect(page.getByTestId("promotion-self-review-denied")).toBeVisible();
    await expect(page.getByTestId("promotion-approve-btn")).toHaveCount(0);
    const decisionId = (await page.getByTestId("promotion-decision-id").innerText()).trim();
    const intakeId = (await page.getByTestId("intake-detail-id").innerText()).trim();
    const phase = process.env.NETWORK_PARITY_CAPTURE_PHASE ?? "after";
    await shot(page, info, `${phase}-self-review-${width}`);
    // A fresh page's session establishes an independent actor, same allowed role.
    await page.evaluate(() => sessionStorage.setItem("oday.operator.subject", "00000000-0000-4000-8000-000000000002"));
    await page.goto(`/operator?ws=network&tab=radar&selected=${encodeURIComponent(intakeId)}&dialog=detail`);
    await ready(page.getByTestId("promotion-second-actor-ok")).toBeVisible();
    const trigger = page.getByTestId("promotion-approve-btn");
    if (phase !== "after") {
      await page.getByTestId("promotion-review-reason").fill("資料完整、來源與比對唯一，同意建立候選點並排入評分。");
      await page.getByTestId("promotion-review-ack").click();
    }
    await trigger.click();
    const overlay = page.getByTestId("promotion-confirmation-dialog"), dialog = overlay.getByRole("dialog");
    await expect(dialog).toBeVisible();
    const boxes = { dialog: await measure(dialog), head: await measure(dialog.locator(":scope > header")), body: await measure(dialog.locator(":scope > div")), footer: await measure(dialog.locator(":scope > footer")) };
    let reference;
    if (process.env.NETWORK_PARITY_DESIGN === "1") {
      const design = await page.context().newPage();
      design.setDefaultTimeout(15_000);
      await design.setViewportSize({ width, height: 900 });
      await design.route(/^https?:\/\//, (route) => route.abort());
      await design.goto(pathToFileURL(path.resolve("docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/oday-plus-console-r7-standalone.html")).href, { waitUntil: "domcontentloaded" });
      await design.getByRole("button", { name: /林.*營運主管/ }).click();
      await design.getByRole("button", { name: /展店經理.*林曉青/ }).click();
      await design.getByRole("button", { name: /展店與店網.*Network/ }).click();
      await design.getByRole("button", { name: /^物件雷達\s+Listing Radar/ }).dispatchEvent("click");
      await design.getByRole("listitem").filter({ hasText: "IN-3001" }).dispatchEvent("click");
      const detail = design.locator('[data-screen-label="Intake 收件處理詳情頁"]');
      await detail.getByRole("button", { name: "建立新物件（加入收件匣）", exact: true }).dispatchEvent("click");
      const decision = design.locator('[data-screen-label="Dialog 收件決策確認"]');
      await decision.getByRole("textbox", { name: "決策原因", exact: true }).fill("資料完整、來源與比對唯一，建立新物件。");
      await decision.getByRole("button", { name: /^確認建立新物件/ }).dispatchEvent("click");
      await detail.getByRole("button", { name: /提出.*promotion|提出.*晉升/ }).dispatchEvent("click");
      await ready(detail).toContainText("PENDING_APPROVAL");
      await design.getByRole("button", { name: /展店經理/ }).dispatchEvent("click");
      await design.getByRole("button", { name: /展店主管/ }).dispatchEvent("click");
      await detail.getByRole("button", { name: "審查並核准 promotion（second actor）", exact: true }).dispatchEvent("click");
      await design.addStyleTag({ content: "*, *::before, *::after { animation: none !important; transition: none !important; }" });
      const refDialog = design.locator('[data-screen-label="Dialog Promotion 核准"]').getByRole("dialog");
      await expect(refDialog).toBeVisible();
      reference = await measure(refDialog);
      await shot(design, info, `design-promotion-${width}`);
      await design.close();
    }
    await shot(page, info, `${phase}-promotion-${width}`);
    const document = await page.evaluate(() => ({ width: innerWidth, scrollWidth: window.document.documentElement.scrollWidth }));
    await writeFile(await artifact(info, `${phase}-geometry-promotion-${width}.json`), JSON.stringify({ boxes, document, design: reference }, null, 2));
    if (phase !== "after") return;
    expect(document.scrollWidth).toBeLessThanOrEqual(width);
    expect(boxes.dialog.width).toBe(width === 1440 ? 520 : 350);
    expect(boxes.dialog.radius).toBe("14px");
    expect(boxes.body.padding).toBe("12px 18px 4px");
    expect(boxes.body.gap).toBe("10px");
    for (const box of Object.values(boxes)) {
      expect(box.x).toBeGreaterThanOrEqual(20);
      expect(box.x + box.width).toBeLessThanOrEqual(width - 20);
      expect(box.scrollWidth).toBeLessThanOrEqual(Math.ceil(box.width));
    }
    await expect(page.getByTestId("promotion-confirm-reason")).toBeFocused();
    const axe = await new AxeBuilder({ page }).include('[data-testid="promotion-confirmation-dialog"]').analyze();
    await writeFile(await artifact(info, `axe-promotion-${width}.json`), JSON.stringify(axe, null, 2));
    expect(axe.violations).toEqual([]);
    await page.getByTestId("promotion-confirm-approve-btn").click();
    await expect(page.getByTestId("promotion-confirmation-error")).toContainText("理由");
    await shot(page, info, `after-required-reason-${width}`);
    await page.getByTestId("promotion-confirm-reason").fill("資料完整、來源與比對唯一，同意建立候選點並排入評分。");
    await page.getByTestId("promotion-confirm-approve-btn").click();
    await expect(page.getByTestId("promotion-confirmation-error")).toContainText("風險");
    await shot(page, info, `after-required-ack-${width}`);
    await page.getByTestId("promotion-confirm-ack").click();
    const controls = dialog.locator('button:not(:disabled), textarea:not(:disabled)');
    await controls.last().focus(); await page.keyboard.press("Tab"); await expect(controls.first()).toBeFocused();
    await page.keyboard.press("Shift+Tab"); await expect(controls.last()).toBeFocused();
    await page.keyboard.press("Escape"); await expect(overlay).toBeHidden(); await expect(trigger).toBeFocused();
    await trigger.click();
    await expect(page.getByTestId("promotion-confirm-reason")).toHaveValue("資料完整、來源與比對唯一，同意建立候選點並排入評分。");
    const responsePromise = page.waitForResponse((r) => r.url().endsWith(`/api/v1/promotion-decisions/${decisionId}/actions/review`) && r.request().method() === "POST");
    await page.getByTestId("promotion-confirm-approve-btn").click();
    const response = await responsePromise;
    expect(response.status()).toBe(200);
    await ready(overlay).toBeHidden();
    await ready(page.getByTestId("promotion-candidate-id")).toBeVisible();
    const persisted = await request.get(`${api}/api/v1/promotion-decisions/${decisionId}`, { headers: { ...headers, "x-subject-id": "00000000-0000-4000-8000-000000000002" } });
    expect(persisted.status()).toBe(200);
    const receipt = await persisted.json();
    expect(receipt.reviewer_subject_id).toBe("00000000-0000-4000-8000-000000000002");
    expect(receipt.candidate_site_id).toBeTruthy();
    expect(receipt.site_score_job_id).toBeTruthy();
    expect(receipt.audit_event_id).toBeTruthy();
    await writeFile(await artifact(info, `receipt-${width}.json`), JSON.stringify({ browserPostStatus: response.status(), durableReadStatus: persisted.status(), receipt }, null, 2));
    await shot(page, info, `after-committed-${width}`);
    await page.reload();
    await ready(page.getByTestId("promotion-candidate-id")).toHaveText(receipt.candidate_site_id);
    await ready(page.getByTestId("promotion-score-job-id")).toHaveText(receipt.site_score_job_id);
  });
}
