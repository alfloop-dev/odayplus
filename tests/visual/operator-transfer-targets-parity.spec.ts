import { expect, test, type Locator } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

// Mocked READS only: production Operator Intake cannot yet provision these
// resources. This proves the target-unavailable modal UI, NOT provisioning,
// directory authorization, durable writes, or full Transfer/Pause acceptance.
test.describe.configure({ mode: "serial", timeout: 120_000 });
const record = {
  id: "IN-TARGET-UI-FIXTURE", sourceId: "fixture-source", originalUrl: "https://fixture.invalid/target",
  canonicalUrl: "https://fixture.invalid/target", stage: "NEEDS_REVIEW", policy: "APPROVED_RETRIEVAL",
  policyLabel: "Explicit UI fixture", policyReason: "Read projection only; no API provisioning proof",
  rawSnapshot: null, snapshotId: null, parserVersion: null, auditEvents: [],
  capturedAt: null, submitter: "Fixture submitter", owner: "Fixture owner", heatZoneId: "HZ-01",
  version: 71, assignmentId: "00000000-0000-0000-0000-000000000301", assignmentStatus: "CLAIMED",
  assignmentVersion: 14, slaInstanceId: null, matchResult: null, parsedFields: {},
};
async function box(locator: Locator) {
  return locator.evaluate((element) => {
    const bounds = element.getBoundingClientRect();
    const css = getComputedStyle(element);
    return { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height, scrollWidth: element.scrollWidth, radius: css.borderRadius, overflowY: css.overflowY };
  });
}
for (const width of [1440, 390]) {
  test(`Transfer target-unavailable design/before/after UI boundary at ${width}`, async ({ page }, info) => {
    const directory = process.env.NETWORK_PARITY_EVIDENCE_DIR ?? info.outputPath("targets");
    const phase = process.env.NETWORK_PARITY_CAPTURE_PHASE ?? "after";
    await mkdir(directory, { recursive: true });
    await page.setViewportSize({ width, height: 900 });
    page.setDefaultTimeout(30_000);
    await page.addInitScript(() => {
      sessionStorage.setItem("oday.operator.role", "expansion-manager");
      sessionStorage.setItem("oday.operator.subject", "operator-expansion-manager");
    });
    await page.route(/\/api\/v1\/operator\/network-listings\/intake(?:\/IN-TARGET-UI-FIXTURE)?(?:\?.*)?$/, (route) => {
      if (route.request().method() !== "GET") return route.abort();
      const isDetail = new URL(route.request().url()).pathname.endsWith(record.id);
      return route.fulfill({ json: isDetail ? record : {
        items: [record], total: 1, page: 1, pageSize: 10,
        counts: { needsReview: 1, awaitingEntry: 0, processing: 0, blocked: 0, ready: 0 }, evidenceState: "partial",
      } });
    });
    await page.route(/\/api\/v1\/intakes\/IN-TARGET-UI-FIXTURE\/promotion-decision/, (route) => route.fulfill({ status: 404, json: { code: "NOT_FOUND" } }));
    const writes: string[] = [];
    await page.route(/\/api\/v1\/assignments\//, (route) => {
      writes.push(route.request().url());
      return route.abort(); // no mocked successful mutations
    });
    await page.goto("/operator?ws=network");
    await expect(page.getByRole("button", { name: "展店經理", exact: true })).toBeVisible();
    await page.getByTestId("network-tab-1").click();
    const url = new URL(page.url());
    url.searchParams.set("selected", record.id);
    url.searchParams.set("dialog", "assignmentSla");
    url.searchParams.set("decision", "transfer");
    await page.goto(url.href);
    const dialog = page.getByTestId("transfer-intake-dialog").getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(page.getByTestId("transfer-record-version")).toHaveText("v14");
    await page.getByTestId("transfer-handoff-note").fill("Explicit UI fixture draft — no real write");
    await page.screenshot({ path: path.join(directory, `${phase}-transfer-${width}.png`), animations: "disabled" });
    const geometry = {
      evidenceMode: "mocked-read-ui-boundary-no-writes", record,
      document: await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth })),
      dialog: await box(dialog), target: await box(page.getByTestId("transfer-target-select")),
      note: await box(page.getByTestId("transfer-handoff-note")), footer: await box(dialog.locator(":scope > div").last()),
    };
    await writeFile(path.join(directory, `${phase}-geometry-${width}.json`), JSON.stringify(geometry, null, 2));
    expect(geometry.document.scrollWidth).toBeLessThanOrEqual(width);
    expect(geometry.dialog.width).toBe(width === 1440 ? 460 : width - 40);
    expect(geometry.dialog.radius).toBe("14px");
    expect(geometry.dialog.height).toBeLessThanOrEqual(860);
    for (const item of [geometry.dialog, geometry.target, geometry.note, geometry.footer]) {
      expect(item.x).toBeGreaterThanOrEqual(20);
      expect(item.x + item.width).toBeLessThanOrEqual(width - 20);
      expect(item.scrollWidth).toBeLessThanOrEqual(Math.ceil(item.width));
    }
    if (phase === "after") {
      await expect(page.getByTestId("transfer-targets-unavailable")).toBeVisible();
      await expect(page.getByTestId("transfer-target-select")).toBeDisabled();
      await expect(page.getByTestId("transfer-risk-ack")).toBeDisabled();
      await expect(page.getByTestId("transfer-submit-btn")).toBeDisabled();
      const axe = await new AxeBuilder({ page }).include('[data-testid="transfer-intake-dialog"]').analyze();
      await writeFile(path.join(directory, `after-axe-${width}.json`), JSON.stringify(axe, null, 2));
      expect(axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
      // Native keyboard traversal stays within enabled controls when authority
      // disables select/risk/submit. Escape returns to the continuous detail.
      const controls = dialog.locator("button:not(:disabled), textarea:not(:disabled)");
      await controls.last().focus();
      await page.keyboard.press("Tab");
      await expect(controls.first()).toBeFocused();
      await page.keyboard.press("Shift+Tab");
      await expect(controls.last()).toBeFocused();
      await page.keyboard.press("Escape");
      await expect(dialog).toBeHidden();
    } else {
      await expect(page.getByTestId("transfer-target-select")).toHaveValue("actor-mgr");
      await expect(page.getByTestId("transfer-submit-btn")).toBeEnabled();
    }
    expect(writes).toEqual([]);
    await writeFile(path.join(directory, `${phase}-writes-${width}.json`), JSON.stringify({ evidenceMode: "mocked-read-ui-boundary", writes }, null, 2));

    if (process.env.NETWORK_PARITY_DESIGN === "1") {
      const design = await page.context().newPage();
      design.setDefaultTimeout(30_000);
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
      await detail.getByRole("button", { name: "轉交", exact: true }).dispatchEvent("click");
      const reference = design.locator('[data-screen-label="Dialog 轉交／暫停"]').getByRole("dialog");
      await expect(reference).toBeVisible();
      await design.addStyleTag({ content: "*, *::before, *::after { animation: none !important; transition: none !important; }" });
      await design.screenshot({ path: path.join(directory, `design-transfer-${width}.png`), animations: "disabled" });
      await writeFile(path.join(directory, `design-geometry-${width}.json`), JSON.stringify(await box(reference), null, 2));
      await design.close();
    }
  });
}
