import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

// Explicit mocked read-projection fixtures, NOT Operator SLA provisioning,
// server authority, successful writes, clock policy or durable reload evidence.
test.describe.configure({ mode: "serial", timeout: 120_000 });
const states = ["ON_TRACK", "DUE_SOON", "OVERDUE", "BREACHED", "PAUSED", "COMPLETED", "UNAVAILABLE"] as const;
const labels = {
  ON_TRACK: "[✓ ON TRACK]", DUE_SOON: "[⚠ DUE SOON]", OVERDUE: "[‼ OVERDUE]",
  BREACHED: "[🔥 BREACHED]", PAUSED: "[⏸ PAUSED]", COMPLETED: "[✓ COMPLETED]", UNAVAILABLE: "[? UNAVAILABLE]",
};
for (const width of [1440, 390]) {
  test(`authoritative SLA states, terminal and missing authority at ${width}`, async ({ page }, info) => {
    const directory = process.env.NETWORK_PARITY_EVIDENCE_DIR ?? info.outputPath("sla-status");
    const phase = process.env.NETWORK_PARITY_CAPTURE_PHASE ?? "after";
    await mkdir(directory, { recursive: true });
    await page.setViewportSize({ width, height: 900 });
    page.setDefaultTimeout(30_000);
    await page.addInitScript(() => {
      sessionStorage.setItem("oday.operator.role", "expansion-manager");
      sessionStorage.setItem("oday.operator.subject", "operator-expansion-manager");
    });
    let record: Record<string, unknown>;
    await page.route(/\/api\/v1\/operator\/network-listings\/intake(?:\/IN-SLA-UI-FIXTURE)?(?:\?.*)?$/, (route) => {
      if (route.request().method() !== "GET") return route.abort();
      const isDetail = new URL(route.request().url()).pathname.endsWith("IN-SLA-UI-FIXTURE");
      return route.fulfill({ json: isDetail ? record : {
        items: [record], total: 1, page: 1, pageSize: 10,
        counts: { needsReview: 1, awaitingEntry: 0, processing: 0, blocked: 0, ready: 0 }, evidenceState: "partial",
      } });
    });
    await page.route(/\/api\/v1\/intakes\/IN-SLA-UI-FIXTURE\/promotion-decision/, (route) => route.fulfill({ status: 404, json: { code: "NOT_FOUND" } }));
    const writes: string[] = [];
    await page.route(/\/api\/v1\/(assignments|sla-instances)\//, (route) => {
      writes.push(route.request().url());
      return route.abort();
    });
    for (const slaState of states) {
      record = {
        id: "IN-SLA-UI-FIXTURE", sourceId: "fixture-source", originalUrl: "https://fixture.invalid/sla",
        canonicalUrl: "https://fixture.invalid/sla", stage: "NEEDS_REVIEW", policy: "APPROVED_RETRIEVAL",
        policyLabel: "Explicit SLA read fixture", policyReason: "No provisioning or policy proof",
        rawSnapshot: null, snapshotId: null, parserVersion: null, auditEvents: [],
        capturedAt: null, submitter: "Fixture submitter", owner: "Fixture owner", heatZoneId: "HZ-01",
        version: 71, assignmentId: null, assignmentStatus: null, slaInstanceId: null,
        matchResult: null, parsedFields: {}, slaState: slaState === "UNAVAILABLE" ? "UNKNOWN" : slaState,
        slaDueAt: slaState === "UNAVAILABLE" ? "invalid" : ["ON_TRACK", "PAUSED", "COMPLETED"].includes(slaState)
          ? "2000-01-01T00:00:00Z" : new Date(Date.now() + 90 * 60_000).toISOString(),
      };
      // Full navigation exercises the actual detail/container projection; the
      // fixture remains explicitly a mocked read after reload, not persistence.
      await page.goto("/operator?ws=network&tab=radar&selected=IN-SLA-UI-FIXTURE&dialog=detail");
      const summary = page.getByTestId("assignment-sla-summary");
      await expect(summary).toBeVisible();
      const status = page.getByTestId("asg-sla-status");
      if (phase === "after") {
        await expect(status).toContainText(labels[slaState]);
        if (slaState === "UNAVAILABLE") {
          await expect(summary).toContainText("到期時間：UNAVAILABLE");
          await expect(summary).not.toContainText("Invalid Date");
        }
      } else {
        const oldState = ["ON_TRACK", "COMPLETED"].includes(slaState) ? "OVERDUE"
          : ["DUE_SOON", "OVERDUE", "UNAVAILABLE"].includes(slaState) ? "ON_TRACK" : slaState;
        await expect(status).toContainText(labels[oldState as keyof typeof labels]);
      }
      for (const action of ["claim", "transfer", "pause", "resume"]) {
        await expect(page.getByTestId(`asg-btn-${action}`)).toHaveCount(0);
      }
      await summary.scrollIntoViewIfNeeded();
      const geometry = await summary.evaluate((element) => {
        const box = element.getBoundingClientRect();
        const css = getComputedStyle(element);
        return { documentWidth: innerWidth, documentScrollWidth: document.documentElement.scrollWidth,
          x: box.x, width: box.width, height: box.height, scrollWidth: element.scrollWidth,
          fontSize: css.fontSize, text: element.textContent };
      });
      expect(geometry.documentScrollWidth).toBeLessThanOrEqual(width);
      expect(geometry.x).toBeGreaterThanOrEqual(0);
      expect(geometry.x + geometry.width).toBeLessThanOrEqual(width);
      expect(geometry.scrollWidth).toBeLessThanOrEqual(Math.ceil(geometry.width));
      await page.screenshot({ path: path.join(directory, `${phase}-${slaState.toLowerCase()}-${width}.png`), animations: "disabled" });
      await writeFile(path.join(directory, `${phase}-${slaState.toLowerCase()}-${width}.json`), JSON.stringify({
        evidenceMode: "mocked-read-projection-no-provisioning-or-writes", record, geometry,
      }, null, 2));
      if (phase === "after") {
        const axe = await new AxeBuilder({ page }).include('[data-testid="assignment-sla-summary"]').analyze();
        await writeFile(path.join(directory, `axe-${slaState.toLowerCase()}-${width}.json`), JSON.stringify(axe, null, 2));
        expect(axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
      }
    }
    expect(writes).toEqual([]);
    await writeFile(path.join(directory, `${phase}-writes-${width}.json`), JSON.stringify({ evidenceMode: "mocked-read-projection", writes }, null, 2));
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
      const detail = design.locator('[data-screen-label="Intake 收件處理詳情頁"]');
      await expect(detail).toBeVisible();
      await design.screenshot({ path: path.join(directory, `design-detail-${width}.png`), animations: "disabled" });
      await writeFile(path.join(directory, `design-detail-${width}.json`), JSON.stringify(await detail.evaluate((element) => {
        const box = element.getBoundingClientRect();
        return { width: innerWidth, documentScrollWidth: document.documentElement.scrollWidth, x: box.x, detailWidth: box.width };
      }), null, 2));
      await design.close();
    }
  });
}
