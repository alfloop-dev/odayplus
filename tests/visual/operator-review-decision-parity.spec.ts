import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { acquireOperatorBackendLock, releaseOperatorBackendLock } from "../e2e/_operatorBackendLock";

// Supplemental visual/state evidence; no changes to the business inventory.
// Only the isolated local fixture backend is reset/written. No intercepted
// product requests, optimistic receipts, or implementation DOM navigation.
test.describe.configure({ mode: "serial", timeout: 120_000 });
test.beforeAll(acquireOperatorBackendLock);
test.afterAll(releaseOperatorBackendLock);
test.use({ extraHTTPHeaders: {} });
const ready = expect.configure({ timeout: 15_000 });
const headers = { "x-subject-id": "operator-expansion-manager", "x-roles": "expansion_user,site_reviewer", "x-operator-role": "expansion-manager", "x-tenant-id": "tenant-a" };
const actions = [
  { code: "GO", id: "RV-701", button: "核准 GO", result: "Approved" },
  { code: "WAIT", id: "RV-701", button: "核准 WAIT", result: "On Hold" },
  { code: "RETURN", id: "RV-701", button: "退回修改", result: "Need Data" },
  { code: "REJECT", id: "RV-701", button: "駁回", result: "Rejected" },
] as const;
async function measure(locator: Locator) {
  return locator.evaluate((element) => {
    const box = element.getBoundingClientRect();
    const css = getComputedStyle(element);
    return { x: box.x, y: box.y, width: box.width, height: box.height, scrollWidth: element.scrollWidth, radius: css.borderRadius, padding: css.padding, gap: css.gap, background: css.backgroundColor, overflowY: css.overflowY };
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
  for (const action of actions) {
    test(`${action.code} Review Decision geometry and durable state at ${width}`, async ({ page, request }, info) => {
      page.setDefaultTimeout(15_000);
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript(() => {
        sessionStorage.setItem("oday.operator.role", "expansion-manager");
        sessionStorage.setItem("oday.operator.subject", "operator-expansion-manager");
        sessionStorage.setItem("oday.operator.tenant", "tenant-a");
      });
      const api = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
      expect((await request.post(`${api}/api/v1/operator/network-reviews/reset`, { headers })).status()).toBe(200);
      expect((await request.get("/api/v1/operator/network-reviews", { headers })).status()).toBe(200);
      await page.goto("/operator?ws=network");
      await ready(page.getByRole("button", { name: "展店經理", exact: true })).toBeVisible();
      await page.getByTestId("network-tab-5").click();
      const card = page.getByTestId(`review-card-${action.id}`);
      // Wait for actual API projection, not the fallback card with the same ID.
      await ready(card).toContainText("王若寧（拓展）");
      await card.click();
      const trigger = page.getByTestId(`review-btn-${action.code.toLowerCase()}-${action.id}`);
      await trigger.click();
      const overlay = page.getByTestId("review-decision-dialog");
      const dialog = overlay.locator(":scope > div");
      await expect(dialog).toBeVisible();
      const boxes = {
        dialog: await measure(dialog), head: await measure(dialog.locator(":scope > div").nth(0)),
        body: await measure(dialog.locator(":scope > div").nth(1)), footer: await measure(dialog.locator(":scope > div").nth(2)),
        field: await measure(page.getByTestId("review-decision-reason")), submit: await measure(page.getByTestId("review-decision-submit")),
      };
      let reference;
      if (process.env.NETWORK_PARITY_DESIGN === "1") {
        const design = await page.context().newPage();
        design.setDefaultTimeout(15_000);
        await design.setViewportSize({ width, height: 900 });
        await design.route(/^https?:\/\//, (route) => route.abort());
        await design.goto(pathToFileURL(path.resolve("docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/oday-plus-console-r7-standalone.html")).href, { waitUntil: "domcontentloaded" });
        await design.getByRole("button", { name: /林.*營運主管/ }).click();
        await design.getByRole("button", { name: /PM.*稽核.*周明德/ }).click();
        await design.getByRole("button", { name: /展店與店網.*Network/ }).click();
        await design.getByRole("button", { name: /^審核\s+Review/ }).dispatchEvent("click");
        const panel = design.locator('[data-screen-label="Network 選址審核"]');
        // Reference queue cards are non-semantic divs; RV-701 is its
        // initial pending selection. Do not mutate prototype state/data.
        await expect(panel).toContainText("RV-701");
        await panel.getByRole("button", { name: action.button, exact: true }).dispatchEvent("click");
        const refDialog = design.locator('[data-screen-label="Dialog Review Decision"]').getByRole("dialog");
        await expect(refDialog).toBeVisible();
        await design.addStyleTag({ content: "*, *::before, *::after { animation: none !important; transition: none !important; }" });
        reference = { dialog: await measure(refDialog), submit: await measure(refDialog.getByRole("button", { name: action.code === "WAIT" ? "確認核准 WAIT（附條件）" : `確認${action.button}`, exact: true })) };
        await shot(design, info, `design-${action.code}-${width}`);
        await design.close();
      }
      const phase = process.env.NETWORK_PARITY_CAPTURE_PHASE ?? "after";
      await shot(page, info, `${phase}-${action.code}-${width}`);
      const document = await page.evaluate(() => ({ width: innerWidth, scrollWidth: window.document.documentElement.scrollWidth }));
      await writeFile(await artifact(info, `${phase}-geometry-${action.code}-${width}.json`), JSON.stringify({ boxes, document, design: reference }, null, 2));
      expect(document.scrollWidth).toBeLessThanOrEqual(width);
      // Baselines record defects rather than preventing remaining captures.
      if (phase !== "after") return;
      expect(boxes.dialog.width).toBe(width === 1440 ? 520 : width - 40);
      expect(boxes.dialog.radius).toBe("14px");
      expect(boxes.dialog.overflowY).toBe("auto");
      expect(boxes.dialog.height).toBeLessThanOrEqual(828);
      for (const box of Object.values(boxes)) {
        expect(box.x).toBeGreaterThanOrEqual(20);
        expect(box.x + box.width).toBeLessThanOrEqual(width - 20);
        expect(box.scrollWidth).toBeLessThanOrEqual(Math.ceil(box.width));
      }
      if (reference) expect(boxes.submit.background).toBe(reference.submit.background);
      await expect(page.getByTestId("review-decision-reason")).toBeFocused();
      const results = await new AxeBuilder({ page }).include('[data-testid="review-decision-dialog"]').analyze();
      await writeFile(await artifact(info, `axe-${action.code}-${width}.json`), JSON.stringify(results, null, 2));
      expect(results.violations).toEqual([]);
      await page.getByTestId("review-decision-submit").click();
      await expect(page.getByTestId("review-decision-error")).toContainText("原因");
      await shot(page, info, `after-required-reason-${action.code}-${width}`);
      const controls = dialog.locator('button:not(:disabled), input:not(:disabled), textarea:not(:disabled)');
      await controls.last().focus();
      await page.keyboard.press("Tab");
      await expect(controls.first()).toBeFocused();
      await page.keyboard.press("Shift+Tab");
      await expect(controls.last()).toBeFocused();
      await page.keyboard.press("Escape");
      await expect(overlay).toBeHidden();
      await expect(trigger).toBeFocused();
      await trigger.click();
      await page.getByTestId("review-decision-reason").fill("已核對選址資料與風險，將決策及原因寫入稽核紀錄。");
      if (action.code === "WAIT" || action.code === "RETURN") {
        await page.getByTestId("review-decision-submit").click();
        await expect(page.getByTestId("review-decision-error")).toContainText(action.code === "WAIT" ? "通過條件" : "需補資料");
        await page.getByTestId(action.code === "WAIT" ? "review-decision-conditions" : "review-decision-required").fill(action.code === "WAIT" ? "補齊晚間人流與現勘紀錄" : "現勘紀錄、晚間人流樣本");
      }
      const ack = page.getByTestId("review-decision-ack");
      if (await ack.count()) {
        await expect(ack).toHaveAttribute("aria-pressed", "false");
        await page.getByTestId("review-decision-submit").click();
        await expect(page.getByTestId("review-decision-error")).toContainText("風險確認");
        await shot(page, info, `after-override-required-${action.code}-${width}`);
        await ack.click();
        await expect(ack).toHaveAttribute("aria-pressed", "true");
      }
      const receiptPromise = page.waitForResponse((r) => r.url().endsWith(`/api/v1/operator/network-reviews/${action.id}/decide`) && r.request().method() === "POST");
      await page.getByTestId("review-decision-submit").click();
      const response = await receiptPromise;
      expect(response.status()).toBe(200);
      const receipt = await response.json();
      expect(receipt.decision.finalDecision).toBe(action.result);
      expect(receipt.auditEvent.action).toBe("review.decision");
      await writeFile(await artifact(info, `receipt-${action.code}-${width}.json`), JSON.stringify({ status: response.status(), receipt }, null, 2));
      await ready(overlay).toBeHidden();
      await ready(page.getByTestId(`review-decided-${action.id}`)).toContainText(action.result);
      await shot(page, info, `after-committed-${action.code}-${width}`);
      await page.reload();
      await page.getByTestId("network-tab-5").click();
      await ready(page.getByTestId(`review-card-${action.id}`)).toContainText(action.code === "WAIT" ? "On Hold" : action.code === "RETURN" ? "退回" : action.code === "REJECT" ? "駁回" : "核准");
      await page.getByTestId(`review-card-${action.id}`).click();
      await ready(page.getByTestId(`review-decided-${action.id}`)).toContainText(action.result);
    });
  }
}
