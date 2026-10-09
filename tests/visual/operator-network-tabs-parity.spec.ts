import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

test.describe.configure({ timeout: 90_000 });
const screens = [
  { id: "candidates", tab: 2, label: "Network 候選點工作台", designTab: /^候選點\s+Candidates/ },
  { id: "sitescore", tab: 3, label: "Network SiteScore Lab", designTab: /^SiteScore\s+Score Lab/ },
  { id: "compare", tab: 4, label: "Network 候選點比較", designTab: /^比較\s+Compare/ },
] as const;

async function measure(locator: Locator) {
  return locator.evaluate((element) => {
    const r = element.getBoundingClientRect();
    const s = getComputedStyle(element);
    return { x: r.x, y: r.y, width: r.width, height: r.height, scrollWidth: element.scrollWidth, columns: s.gridTemplateColumns, overflowX: s.overflowX };
  });
}

async function file(info: TestInfo, name: string) {
  const target = process.env.NETWORK_PARITY_EVIDENCE_DIR
    ? path.join(process.env.NETWORK_PARITY_EVIDENCE_DIR, name) : info.outputPath(name);
  await mkdir(path.dirname(target), { recursive: true });
  return target;
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  await page.evaluate(() => scrollTo(0, 0));
  const target = await file(info, `${name}.png`);
  await page.screenshot({ path: target, fullPage: true, animations: "disabled" });
  await info.attach(name, { path: target, contentType: "image/png" });
}

function contained(box: { x: number; width: number }, width: number) {
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(width);
}

for (const width of [1440, 1024, 390]) {
  for (const screen of screens) {
    test(`${screen.id} content geometry at ${width}`, async ({ page, request }, info) => {
      await page.setViewportSize({ width, height: 900 });
      // Reset only the isolated local test backend. No writes are made by UI capture.
      const api = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
      const headers = { "x-subject-id": "operator-expansion-manager", "x-roles": "expansion_user", "x-operator-role": "expansion-staff", "x-tenant-id": "tenant-a" };
      expect((await request.post(`${api}/api/v1/operator/network-scoring/reset`, { headers })).status()).toBe(200);
      const snapshot = await request.get(`${api}/api/v1/operator/network-scoring`, { headers });
      expect(snapshot.status()).toBe(200);
      // Use the real service's deterministic fixture payload; freeze it for capture.
      const payload = await snapshot.json();
      await page.route("**/api/v1/operator/network-scoring", (route) => route.fulfill({ json: payload }));
      await page.goto("/operator?ws=network");
      await expect(page.getByLabel("Network Find Areas state")).toContainText("4 進行中候選");
      await page.getByTestId(`network-tab-${screen.tab}`).click();
      const panel = page.getByTestId(`network-panel-${screen.id}`);
      await expect(panel).toBeVisible();
      await expect(page.getByRole("main")).toHaveCount(1);
      const boxes: Record<string, Awaited<ReturnType<typeof measure>>> = { panel: await measure(panel) };
      let reference: Awaited<ReturnType<typeof measure>> | undefined;
      if (process.env.NETWORK_PARITY_DESIGN === "1" && width !== 1024) {
        const design = await page.context().newPage();
        await design.setViewportSize({ width, height: 900 });
        await design.route(/^https?:\/\//, (route) => route.abort());
        await design.goto(pathToFileURL(path.resolve("docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/oday-plus-console-r7-standalone.html")).href, { waitUntil: "domcontentloaded" });
        await design.getByRole("button", { name: /展店與店網.*Network/ }).click();
        // Reference prototype clips mobile tabs (known VDC-002 defect). Only
        // reference navigation uses DOM events; implementation uses real clicks.
        await design.getByRole("button", { name: screen.designTab }).dispatchEvent("click");
        await design.addStyleTag({ content: "*, *::before, *::after { animation: none !important; transition: none !important; }" });
        const referencePanel = design.locator(`[data-screen-label="${screen.label}"]`);
        await expect(referencePanel).toBeVisible();
        reference = await measure(referencePanel);
        await screenshot(design, info, `design-${screen.id}-${width}`);
        await design.close();
      }
      if (screen.id === "candidates") {
        const pipeline = panel.getByLabel("Candidate pipeline");
        const board = panel.getByLabel("Candidate board");
        const detail = panel.getByLabel("候選點詳情");
        boxes.pipeline = await measure(pipeline);
        boxes.board = await measure(board);
        boxes.detail = await measure(detail);
        boxes.photo = await measure(panel.getByTestId("candidate-photo-placeholder"));
        boxes.facts = await measure(panel.getByLabel("候選點鍵值資訊"));
        await expect(panel.getByTestId("candidate-blocked-CS-1003")).toBeDisabled();
        if (width === 1440) {
          expect(boxes.pipeline.width).toBe(180);
          expect(boxes.detail.width).toBe(348);
          expect(boxes.board.x).toBe(boxes.pipeline.x + 194);
          expect(boxes.detail.x).toBe(boxes.board.x + boxes.board.width + 14);
        } else if (width === 390) {
          expect(boxes.board.y).toBeGreaterThanOrEqual(boxes.pipeline.y + boxes.pipeline.height);
          expect(boxes.detail.y).toBeGreaterThanOrEqual(boxes.board.y + boxes.board.height);
        }
      } else if (screen.id === "sitescore") {
        const picker = panel.getByLabel("選擇候選點");
        const report = panel.getByTestId("sitescore-card-CS-1001");
        boxes.picker = await measure(picker);
        boxes.report = await measure(report);
        boxes.map = await measure(panel.getByTestId("sitescore-mini-map"));
        boxes.revenue = await measure(report.getByLabel("月營收路徑（P50）"));
        boxes.risks = await measure(report.getByLabel("Risk breakdown"));
        await expect(report.getByLabel("月營收路徑（P50）").locator("i")).toHaveCount(4);
        await expect(report.getByLabel("Risk breakdown").locator(":scope > div")).toHaveCount(6);
        if (width === 1440) {
          expect(boxes.picker.width).toBe(250);
          expect(boxes.report.x).toBe(boxes.picker.x + 264);
          expect(boxes.map.y).toBeGreaterThan(boxes.picker.y);
        }
        await panel.getByTestId("sitescore-pick-CS-1003").click();
        await expect(panel.getByTestId("sitescore-blocked-CS-1003").getByRole("button")).toBeDisabled();
        await expect(report).toBeHidden();
        await panel.getByTestId("sitescore-pick-CS-1001").click();
      } else {
        const table = panel.getByTestId("network-compare-table");
        boxes.tableScroll = await measure(table.locator(".."));
        boxes.map = await measure(panel.getByTestId("compare-map"));
        boxes.recommendation = await measure(panel.getByTestId("compare-recommendation"));
        expect(boxes.map.y).toBeGreaterThanOrEqual(boxes.tableScroll.y + boxes.tableScroll.height);
        await expect(panel.getByRole("button", { name: /送審首選/ })).toBeVisible();
        await expect(panel.getByRole("button", { name: /產生比較報告/ })).toBeVisible();
        if (width === 1440) {
          expect(boxes.recommendation.width).toBe(300);
          expect(boxes.recommendation.x).toBe(boxes.map.x + boxes.map.width + 14);
        } else if (width === 390) {
          expect(boxes.tableScroll.overflowX).toBe("auto");
          expect(boxes.tableScroll.scrollWidth).toBeGreaterThan(boxes.tableScroll.width);
          expect(boxes.recommendation.y).toBeGreaterThanOrEqual(boxes.map.y + boxes.map.height);
        }
      }
      const document = await page.evaluate(() => ({ width: innerWidth, scrollWidth: window.document.documentElement.scrollWidth }));
      const target = await file(info, `geometry-${screen.id}-${width}.json`);
      await writeFile(target, JSON.stringify({ boxes, document, design: reference }, null, 2));
      await info.attach("geometry", { path: target, contentType: "application/json" });
      if (width !== 1024) await screenshot(page, info, `after-${screen.id}-${width}`);
      expect(document.scrollWidth).toBeLessThanOrEqual(width);
      for (const [name, box] of Object.entries(boxes)) {
        contained(box, width);
        // Intentional picker/pipeline/table scrolling must remain locally reachable.
        if (!["pipeline", "picker", "tableScroll"].includes(name)) {
          expect(box.scrollWidth, `${name} must not clip content`).toBeLessThanOrEqual(Math.ceil(box.width));
        }
      }
      if (width === 1440) {
        expect(boxes.panel.x).toBe(20);
        expect(boxes.panel.width).toBe(1400);
        if (reference) expect(boxes.panel.width).toBe(reference.width);
      }
    });
  }
}
