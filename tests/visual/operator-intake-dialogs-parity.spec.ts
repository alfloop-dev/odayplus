import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { acquireOperatorBackendLock, releaseOperatorBackendLock } from "../e2e/_operatorBackendLock";

// Supplemental acceptance evidence, outside the exact business inventory.
// Only the isolated local fixture API is reset/written. Reference navigation
// may dispatch clicks because Package 10 clips its mobile tab strip.
test.describe.configure({ mode: "serial", timeout: 120_000 });
test.beforeAll(acquireOperatorBackendLock);
test.afterAll(releaseOperatorBackendLock);
const headers = { "x-subject-id": "operator-expansion-manager", "x-roles": "expansion_user,site_reviewer", "x-operator-role": "expansion-manager", "x-tenant-id": "tenant-a" };
const screens = [
  { id: "field-fix", testId: "intake-fix-dialog", label: "Dialog 欄位修正", trigger: "fix-field-address", field: "intake-fix-value", expectedWidth: 460 },
  { id: "decision", testId: "intake-decide-dialog", label: "Dialog 收件決策確認", trigger: "decide-action-create", field: "intake-decide-reason", expectedWidth: 520 },
] as const;

async function measure(locator: Locator) {
  return locator.evaluate((element) => {
    const box = element.getBoundingClientRect();
    const css = getComputedStyle(element);
    return { x: box.x, y: box.y, width: box.width, height: box.height, scrollWidth: element.scrollWidth, scrollHeight: element.scrollHeight, radius: css.borderRadius, overflowY: css.overflowY, padding: css.padding, gap: css.gap };
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
  for (const screen of screens) {
    test(`${screen.id} paired geometry and focus at ${width}`, async ({ page, request }, info) => {
      page.setDefaultTimeout(15_000);
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript(() => {
        sessionStorage.setItem("oday.operator.role", "expansion-manager");
        sessionStorage.setItem("oday.operator.subject", "operator-expansion-manager");
      });
      const api = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
      expect((await request.post(`${api}/api/v1/operator/network-listings/reset`, { headers })).status()).toBe(200);
      // Warm the actual same-origin BFF route before the typed client's
      // bounded timeout; cold Next compilation is not an intake failure.
      const inbox = await request.get("/api/v1/operator/network-listings/intake", { headers });
      expect(inbox.status()).toBe(200);
      await page.goto("/operator?ws=network");
      await expect(page.getByRole("button", { name: "展店經理", exact: true })).toBeVisible({ timeout: 15_000 });
      await page.getByTestId("network-tab-1").click();
      await expect(page.getByTestId("intake-inbox-empty")).toBeVisible({ timeout: 15_000 });
      await page.getByTestId("intake-add-button").click();
      await page.getByTestId("intake-url-input").fill("https://www.synthetic.example/detail-77120345.html");
      await page.getByTestId("intake-submit-button").click();
      await expect(page.getByTestId("intake-detail-stage")).toHaveText("可決策", { timeout: 15_000 });
      const trigger = page.getByTestId(screen.trigger);
      await trigger.click();
      const overlay = page.getByTestId(screen.testId);
      const dialog = overlay.getByRole("dialog");
      await expect(dialog).toBeVisible();
      await expect(page.getByTestId(screen.field)).toBeFocused();
      const boxes = {
        dialog: await measure(dialog),
        head: await measure(dialog.locator(":scope > div").nth(0)),
        body: await measure(dialog.locator(":scope > div").nth(1)),
        footer: await measure(dialog.locator(":scope > div").nth(2)),
        field: await measure(page.getByTestId(screen.field)),
        close: await measure(dialog.getByRole("button", { name: "關閉", exact: true })),
      };
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
        await expect(detail).toBeVisible();
        if (screen.id === "field-fix") {
          // The archived prototype mistakenly uses the same inkFieldsMob
          // condition for both field grids. Navigate at mobile width, then
          // restore the requested width before measuring the untouched dialog.
          if (width === 1440) await design.setViewportSize({ width: 390, height: 900 });
          await detail.getByRole("button", { name: /修正欄位.*地址/ }).first().dispatchEvent("click");
          if (width === 1440) await design.setViewportSize({ width, height: 900 });
        } else {
          await detail.getByRole("button", { name: "建立新物件（加入收件匣）", exact: true }).dispatchEvent("click");
        }
        await design.addStyleTag({ content: "*, *::before, *::after { animation: none !important; transition: none !important; }" });
        const referenceDialog = design.locator(`[data-screen-label="${screen.label}"]`).getByRole("dialog");
        await expect(referenceDialog).toBeVisible();
        reference = await measure(referenceDialog);
        await shot(design, info, `design-${screen.id}-${width}`);
        await design.close();
      }
      const phase = process.env.NETWORK_PARITY_CAPTURE_PHASE ?? "after";
      await shot(page, info, `${phase}-${screen.id}-${width}`);
      const document = await page.evaluate(() => ({ width: innerWidth, scrollWidth: window.document.documentElement.scrollWidth }));
      await writeFile(await artifact(info, `${phase}-geometry-${screen.id}-${width}.json`), JSON.stringify({ boxes, document, design: reference }, null, 2));
      expect(document.scrollWidth).toBeLessThanOrEqual(width);
      expect(boxes.dialog.radius).toBe("14px");
      expect(boxes.dialog.overflowY).toBe("auto");
      expect(boxes.dialog.width).toBe(width === 1440 ? screen.expectedWidth : width - 40);
      expect(boxes.dialog.height).toBeLessThanOrEqual(860);
      for (const box of Object.values(boxes)) {
        expect(box.x).toBeGreaterThanOrEqual(20);
        expect(box.x + box.width).toBeLessThanOrEqual(width - 20);
        expect(box.scrollWidth).toBeLessThanOrEqual(Math.ceil(box.width));
      }
      if (reference && width === 1440) expect(boxes.dialog.width).toBe(reference.width);
      if (phase === "after") {
        expect(boxes.body.padding).toBe("12px 18px 4px");
        expect(boxes.body.gap).toBe("10px");
        expect(boxes.footer.padding).toBe("12px 18px 16px");
        const results = await new AxeBuilder({ page }).include(`[data-testid="${screen.testId}"]`).analyze();
        await writeFile(await artifact(info, `axe-${screen.id}-${width}.json`), JSON.stringify(results, null, 2));
        expect(results.violations.filter((v) => v.impact === "critical" || v.impact === "serious")).toEqual([]);
        const prefix = screen.id === "field-fix" ? "intake-fix" : "intake-decide";
        await expect(page.getByTestId(`${prefix}-risk-summary`)).toBeVisible();
        await expect(page.getByTestId(`${prefix}-risk-ack`)).not.toBeChecked();
        // Required reasons and risk gates survive density changes. This local
        // invalid submission must not close the dialog or invent a receipt.
        await page.getByTestId(`${prefix}-submit`).click();
        await expect(page.getByTestId(`${prefix}-error`)).toContainText("原因");
        await expect(dialog).toBeVisible();
        await shot(page, info, `after-required-reason-${screen.id}-${width}`);
      }
      // Actual keyboard traversal, no force clicks or implementation DOM events.
      const controls = dialog.locator('button:not(:disabled), input:not(:disabled), textarea:not(:disabled)');
      await controls.last().focus();
      await page.keyboard.press("Tab");
      await expect(controls.first()).toBeFocused();
      await page.keyboard.press("Shift+Tab");
      await expect(controls.last()).toBeFocused();
      await page.keyboard.press("Escape");
      await expect(overlay).toBeHidden();
      await expect(trigger).toBeFocused();
    });
  }
}
