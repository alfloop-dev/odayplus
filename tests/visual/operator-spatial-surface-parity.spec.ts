import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

// Controlled read-model render coverage, NOT a durable-write or live-data receipt.
const proposal = {
  proposal_id: "11111111-2222-3333-4444-555555555555", zone_id: "MZ-0123456789abcdef",
  tenant_id: "tenant-a", composition_kind: "MERGED", member_cell_ids: ["cell-1", "cell-2"], member_count: 2,
  ndcg_gain: 0, cannibalization_variance_reduction: 0.24, correlation_rho: 0.88, disconnect_index: 0.12,
  confidence: 0.88, model_version: "heatzone-composition-v1", policy_version_id: "heatzone-merge-v1:tenant-a",
  status: "PROPOSED", reasons: ["adjacent_high_demand_correlation", "continuous_spatial_absorption"],
  warnings: ["Requires independent boundary review"], created_at: "2026-09-03T12:00:00Z",
};
const phase = process.env.NETWORK_PARITY_CAPTURE_PHASE ?? "after";
for (const width of [1440, 1024, 390]) {
  test(`Spatial later-spec integration and empty state at ${width}`, async ({ page }, info) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => {
      sessionStorage.setItem("oday.operator.role", "expansion-manager");
      sessionStorage.setItem("oday.operator.subject", "operator-expansion-manager");
      sessionStorage.setItem("oday.operator.tenant", "tenant-a");
    });
    const directory = process.env.NETWORK_PARITY_EVIDENCE_DIR ?? info.outputDir;
    await mkdir(directory, { recursive: true });
    async function save(name: string, value: unknown) { await writeFile(path.join(directory, name), JSON.stringify(value, null, 2)); }
    async function shot(target: typeof page, name: string) {
      await target.evaluate(() => scrollTo(0, 0));
      const clip = await target.evaluate(() => ({ x: 0, y: 0, width: innerWidth, height: document.documentElement.scrollHeight }));
      await target.screenshot({ path: path.join(directory, name), fullPage: true, clip, animations: "disabled" });
    }
    if (process.env.NETWORK_PARITY_DESIGN === "1" && width !== 1024) {
      const design = await page.context().newPage();
      await design.setViewportSize({ width, height: 900 });
      await design.route(/^https?:\/\//, (route) => route.abort());
      await design.goto(pathToFileURL(path.resolve("docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/oday-plus-console-r7-standalone.html")).href, { waitUntil: "domcontentloaded" });
      await design.getByRole("button", { name: /林.*營運主管/ }).click();
      await design.getByRole("button", { name: /展店經理.*林曉青/ }).click();
      await design.getByRole("button", { name: /展店與店網.*Network/ }).click();
      await expect(design.getByText(/已切換 Demo 角色/)).toBeHidden({ timeout: 15_000 });
      // Spatial/HZ-006 is a later-spec addition; never invent a reference screen.
      await expect(design.getByRole("button", { name: /空間治理|Merge & Split|Spatial/ })).toHaveCount(0);
      await shot(design, `design-network-no-spatial-${width}.png`);
      await save(`design-absence-${width}.json`, { spatialControls: 0, reference: "Package 10 Network landing, not a Spatial reference", width });
      await design.close();
    }
    let populated = false;
    await page.route("**/api/v1/heatzones/merge-split/proposals", (route) => route.fulfill({ json: { items: populated ? [proposal] : [] } }));
    await page.goto("/operator?ws=network");
    await page.getByTestId("network-tab-7").click();
    const panel = page.getByTestId("heatzone-merge-split-panel");
    await expect(panel.getByTestId("empty-proposals")).toBeVisible({ timeout: 30_000 });
    async function capture(state: string) {
      const boxes = await panel.evaluate((el) => {
        const measure = (node: Element) => {
          const r = node.getBoundingClientRect();
          return { x: r.x, y: r.y, width: r.width, height: r.height, scrollWidth: node.scrollWidth };
        };
        return { panel: measure(el), children: [...el.querySelectorAll('[data-testid="proposal-list"], [data-testid="proposal-detail"]')].map(measure), documentWidth: document.documentElement.scrollWidth };
      });
      await save(`${phase}-${state}-geometry-${width}.json`, boxes);
      await shot(page, `${phase}-${state}-${width}.png`);
      const axe = await new AxeBuilder({ page }).include('[data-testid="heatzone-merge-split-panel"]').withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
      await save(`${phase}-${state}-axe-${width}.json`, axe);
      if (phase !== "after") return;
      expect(boxes.documentWidth).toBeLessThanOrEqual(width);
      for (const box of [boxes.panel, ...boxes.children]) {
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(width);
        expect(box.scrollWidth).toBeLessThanOrEqual(Math.ceil(box.width));
      }
      if (boxes.children.length && width === 390) expect(boxes.children[1].y).toBeGreaterThanOrEqual(boxes.children[0].y + boxes.children[0].height);
      expect(axe.violations).toEqual([]);
    }
    await capture("empty");
    populated = true;
    await page.reload();
    await page.getByTestId("network-tab-7").click();
    await expect(panel.getByTestId("proposal-detail")).toContainText(proposal.zone_id, { timeout: 30_000 });
    await capture("proposal");
    if (phase === "after") {
      const row = panel.getByTestId(`proposal-item-${proposal.proposal_id}`);
      await row.focus();
      await page.keyboard.press("Enter");
      await expect(row).toHaveAttribute("aria-pressed", "true");
      await expect(panel.getByTestId("proposal-detail")).toContainText(proposal.warnings[0]);
      await expect(panel.getByTestId("proposal-detail")).toContainText("+0.00%");
      await panel.getByTestId("proposal-status-filter").selectOption("REJECTED");
      await expect(panel.getByTestId("empty-proposals")).toBeVisible();
      await expect(panel.getByTestId("proposal-detail")).toHaveCount(0);
    }
  });
}
