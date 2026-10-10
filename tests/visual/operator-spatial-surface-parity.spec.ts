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

// Controlled HTTP outcomes isolate UI acknowledgement/keyboard semantics.
// They are deliberately NOT generation, backend durability or live-write proof.
for (const width of [1440, 390]) {
  for (const kind of ["approve", "reject"] as const) {
    test(`Spatial ${kind} decision acknowledgement at ${width}`, async ({ page }, info) => {
      test.setTimeout(120_000);
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript(() => {
        sessionStorage.setItem("oday.operator.role", "expansion-manager");
        sessionStorage.setItem("oday.operator.subject", "operator-expansion-manager");
        sessionStorage.setItem("oday.operator.tenant", "tenant-a");
      });
      const directory = process.env.NETWORK_PARITY_EVIDENCE_DIR ?? info.outputDir;
      await mkdir(directory, { recursive: true });
      let readStatus = 200;
      let terminal = false;
      let writes = 0;
      let releaseWrite: (() => void) | undefined;
      await page.route("**/api/v1/heatzones/merge-split/proposals", (route) => route.fulfill({
        status: readStatus, json: readStatus === 200 ? { items: [{ ...proposal, status: terminal ? kind === "approve" ? "APPLIED" : "REJECTED" : "PROPOSED" }] } : { detail: "controlled readback failure" },
      }));
      await page.route(`**/api/v1/heatzones/merge-split/proposals/*/${kind}`, async (route) => {
        writes += 1;
        if (writes === 1) {
          await new Promise<void>((resolve) => { releaseWrite = resolve; });
          await route.fulfill({ status: 409, json: { detail: "controlled decision conflict" } });
        } else {
          readStatus = 500;
          await route.fulfill({ status: 200, json: { proposal: { ...proposal, status: kind === "approve" ? "APPLIED" : "REJECTED" } } });
        }
      });
      await page.goto("/operator?ws=network&tab=composition");
      await page.getByTestId("network-tab-7").click();
      const panel = page.getByTestId("heatzone-merge-split-panel");
      await expect(panel.getByTestId("proposal-detail")).toBeVisible({ timeout: 30_000 });
      const invoker = panel.getByTestId(`btn-open-${kind}`);
      await invoker.click();
      const modal = page.getByTestId(`${kind}-modal`);
      const input = modal.locator("textarea");
      const confirm = modal.getByTestId(`btn-confirm-${kind}`);
      const cancel = modal.getByRole("button", { name: "取消" });
      if (phase === "after") {
        await expect(input).toBeFocused();
        if (kind === "reject") await expect(confirm).toBeDisabled();
        await cancel.focus();
        if (kind === "reject") { await page.keyboard.press("Tab"); await expect(input).toBeFocused(); }
        else { await confirm.focus(); await page.keyboard.press("Tab"); await expect(input).toBeFocused(); }
        await page.keyboard.press("Escape");
        await expect(modal).toBeHidden();
        await expect(invoker).toBeFocused();
        await invoker.click();
      }
      await input.fill("保留原始決策說明；不可因衝突或讀回失敗重送。");
      async function capture(state: string, withModal = true) {
        const target = withModal ? modal : panel;
        const geometry = await target.evaluate((el) => {
          const box = (node: Element) => { const r = node.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height, scrollWidth: node.scrollWidth }; };
          return { box: box(withModalNode(el)), children: [...el.querySelectorAll("textarea, button")].map(box), documentWidth: document.documentElement.scrollWidth };
          function withModalNode(node: Element) { return node.querySelector('[role="dialog"]') ?? (node.matches('[data-testid$="-modal"]') ? node.firstElementChild! : node); }
        });
        const axe = await new AxeBuilder({ page }).include(withModal ? `[data-testid="${kind}-modal"]` : '[data-testid="heatzone-merge-split-panel"]').withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
        const name = `${phase}-${kind}-${state}-${width}`;
        await writeFile(path.join(directory, `${name}-geometry.json`), JSON.stringify(geometry, null, 2));
        await writeFile(path.join(directory, `${name}-axe.json`), JSON.stringify({ violations: axe.violations, incomplete: axe.incomplete, passes: axe.passes.map((rule) => rule.id), timestamp: axe.timestamp, url: axe.url }, null, 2));
        await page.screenshot({ path: path.join(directory, `${name}.png`), fullPage: true, animations: "disabled" });
        if (phase === "after") {
          expect(geometry.documentWidth).toBeLessThanOrEqual(width);
          for (const box of [geometry.box, ...geometry.children]) {
            expect(box.x).toBeGreaterThanOrEqual(0);
            expect(box.x + box.width).toBeLessThanOrEqual(width);
            expect(box.scrollWidth).toBeLessThanOrEqual(Math.ceil(box.width));
          }
          if (withModal) {
            expect(geometry.box.y).toBeGreaterThanOrEqual(0);
            expect(geometry.box.y + geometry.box.height).toBeLessThanOrEqual(900);
            await expect(modal.getByRole("dialog")).toHaveAccessibleName(kind === "approve" ? "確認核准熱區拓撲提案" : "拒絕熱區拓撲提案");
          }
          expect(axe.violations).toEqual([]);
        }
      }
      await capture("modal");
      await confirm.click();
      await expect.poll(() => writes).toBe(1);
      if (phase === "after") {
        await expect(input).toBeDisabled();
        await expect(confirm).toBeDisabled();
        await expect(cancel).toBeDisabled();
        await page.keyboard.press("Escape");
        await expect(modal).toBeVisible();
      }
      await capture("pending");
      releaseWrite!();
      await expect(panel.getByTestId("feedback-message")).toBeVisible();
      await expect(input).toHaveValue("保留原始決策說明；不可因衝突或讀回失敗重送。");
      await capture("conflict");
      await confirm.click();
      await expect(modal).toBeHidden();
      await expect(panel.getByTestId("proposal-read-error")).toBeVisible();
      if (phase === "after") await expect(panel.getByTestId("feedback-message")).toContainText("最新狀態尚未確認");
      await capture("ack-read-failed", false);
      expect(writes).toBe(2);
      readStatus = 200;
      terminal = true;
      await panel.getByRole("button", { name: "重新載入提案" }).click();
      await expect(panel.getByTestId("proposal-detail")).toContainText(proposal.zone_id);
      await expect(panel.getByTestId(`btn-open-${kind}`)).toHaveCount(0);
      await capture("recovered-terminal", false);
      expect(writes).toBe(2);
    });
  }
}

for (const width of [1440, 390]) {
  test(`Spatial list availability and retry at ${width}`, async ({ page }, info) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => {
      sessionStorage.setItem("oday.operator.role", "expansion-manager");
      sessionStorage.setItem("oday.operator.subject", "operator-expansion-manager");
      sessionStorage.setItem("oday.operator.tenant", "tenant-a");
    });
    const directory = process.env.NETWORK_PARITY_EVIDENCE_DIR ?? info.outputDir;
    await mkdir(directory, { recursive: true });
    let hold = true;
    const pending: Array<() => void> = [];
    function release() {
      hold = false;
      pending.splice(0).forEach((resolve) => resolve());
    }
    let response: { status: number; json: unknown } = { status: 500, json: { detail: "controlled read failure" } };
    let received = 0;
    await page.route("**/api/v1/heatzones/merge-split/proposals", async (route) => {
      received += 1;
      // Hydration may issue more than one read. Release all observed reads,
      // and let later reads finish too; do not orphan a resolver on reload.
      if (hold) await new Promise<void>((resolve) => { pending.push(resolve); });
      await route.fulfill(response);
    });
    await page.goto("/operator?ws=network&tab=composition");
    await page.getByTestId("network-tab-7").click();
    const panel = page.getByTestId("heatzone-merge-split-panel");
    await expect(panel).toBeVisible({ timeout: 30_000 });
    await expect.poll(() => received).toBeGreaterThan(0);
    async function capture(state: string) {
      await page.evaluate(() => scrollTo(0, 0));
      const boxes = await panel.evaluate((el) => {
        const r = el.getBoundingClientRect();
        return { x: r.x, width: r.width, scrollWidth: el.scrollWidth, documentWidth: document.documentElement.scrollWidth };
      });
      const axe = await new AxeBuilder({ page }).include('[data-testid="heatzone-merge-split-panel"]').withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
      await writeFile(path.join(directory, `${phase}-${state}-geometry-${width}.json`), JSON.stringify(boxes, null, 2));
      await writeFile(path.join(directory, `${phase}-${state}-axe-${width}.json`), JSON.stringify(axe, null, 2));
      await page.screenshot({ path: path.join(directory, `${phase}-${state}-${width}.png`), fullPage: true, animations: "disabled" });
      if (phase === "after") {
        expect(boxes.x).toBeGreaterThanOrEqual(0);
        expect(boxes.x + boxes.width).toBeLessThanOrEqual(width);
        expect(boxes.scrollWidth).toBeLessThanOrEqual(Math.ceil(boxes.width));
        expect(boxes.documentWidth).toBeLessThanOrEqual(width);
        expect(axe.violations).toEqual([]);
      }
    }
    if (phase === "after") {
      await expect(panel.getByTestId("loading-proposals")).toBeVisible();
      await expect(panel.getByTestId("empty-proposals")).toHaveCount(0);
      await expect(panel.getByTestId("proposal-status-filter")).toBeDisabled();
    }
    await capture("list-pending");
    const completed = page.waitForResponse("**/api/v1/heatzones/merge-split/proposals");
    release();
    await completed;
    if (phase === "after") await expect(panel.getByTestId("proposal-read-error")).toBeVisible();
    else await expect(panel.getByTestId("empty-proposals")).toBeVisible();
    await capture("list-failed-500");
    if (phase === "after") {
      await expect(panel.getByTestId("empty-proposals")).toHaveCount(0);
      await expect(panel.getByTestId("proposal-detail")).toHaveCount(0);
      const count = received;
      hold = true;
      await panel.getByRole("button", { name: "重新載入提案" }).click();
      await expect.poll(() => received).toBeGreaterThan(count);
      await expect(panel.getByTestId("loading-proposals")).toBeVisible();
      response = { status: 200, json: { items: [proposal] } };
      release();
      await expect(panel.getByTestId("proposal-detail")).toContainText(proposal.zone_id);
      await capture("list-recovered");
    }
    // Controlled negative reads, not authorization grants or backend write proof.
    for (const [state, next] of [
      ["list-denied-403", { status: 403, json: { detail: "controlled scope denial" } }],
      ["list-malformed", { status: 200, json: { not_items: [] } }],
    ] as const) {
      response = next;
      const count = received;
      const done = page.waitForResponse("**/api/v1/heatzones/merge-split/proposals");
      await page.reload();
      await page.getByTestId("network-tab-7").click();
      await expect.poll(() => received).toBeGreaterThan(count);
      await done;
      if (phase === "after") await expect(panel.getByTestId("proposal-read-error")).toBeVisible();
      else await expect(panel.getByTestId("empty-proposals")).toBeVisible();
      await capture(state);
    }
  });
}

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
