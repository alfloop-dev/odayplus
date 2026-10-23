import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { FIXTURE_FRESHNESS, GROWTH_ITEMS, PRICEOPS_RECOMMENDATIONS, SEGMENTS } from "../../apps/web/features/operator/growthViewModel";

// Supplemental synthetic UI checks; not real-account or production acceptance.
for (const width of [1440, 1024, 390]) {
  test(`Growth submit and transition retry retain record ownership at ${width}`, async ({ page }, info) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => sessionStorage.setItem("oday.operator.role", "marketing-manager"));
    const draft = { ...GROWTH_ITEMS[4], id: "synthetic-draft", status: "DRAFT", approvalId: null as string | null };
    const first = { ...GROWTH_ITEMS[4], id: "synthetic-first", status: "APPROVED" };
    const second = { ...GROWTH_ITEMS[4], id: "synthetic-second", status: "APPROVED" };
    const rows = [draft, first, second];
    const submitKeys: string[] = [];
    const transitionKeys: string[] = [];
    let release!: () => void;
    const held = new Promise<void>(resolve => { release = resolve; });
    await page.route("**/api/v1/operator/growth/**", async route => {
      const url = new URL(route.request().url());
      if (route.request().method() === "POST") {
        const key = route.request().headers()["idempotency-key"];
        if (url.pathname.endsWith("/submit")) {
          submitKeys.push(key);
          if (submitKeys.length === 1) return route.fulfill({ status: 503, body: "Synthetic uncertain submission" });
          draft.status = "PENDING_APPROVAL";
          draft.approvalId = "synthetic-approval";
          return route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: draft.id, status: draft.status, approval: { id: draft.approvalId, ref: draft.id, module: "Growth", status: "pending" } }) });
        }
        transitionKeys.push(key);
        if (transitionKeys.length === 1) return route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: "wrong-action", status: "SCHEDULED" }) });
        await held;
        first.status = "SCHEDULED";
        return route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: first.id, status: first.status }) });
      }
      const body = url.pathname.endsWith("/freshness") ? FIXTURE_FRESHNESS
        : { items: url.pathname.endsWith("/segments") ? SEGMENTS : url.pathname.endsWith("/recommendations") ? PRICEOPS_RECOMMENDATIONS : rows };
      await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
    });
    await page.goto(`/operator?ws=growth&item=${draft.id}`);
    await page.getByTestId("growth-submit-approval").click();
    await expect(page.getByTestId("growth-approval-error")).toContainText("尚未確認");
    await expect(page.getByTestId("growth-approval-created")).toHaveCount(0);
    const errorAxe = await new AxeBuilder({ page }).include('[data-testid="growth-approval-panel"]').analyze();
    expect(errorAxe.violations).toEqual([]);
    await page.getByTestId("growth-submit-approval").click();
    await expect(page.getByTestId("growth-approval-created")).toContainText("synthetic-approval");
    expect(submitKeys).toHaveLength(2);
    expect(submitKeys[1]).toBe(submitKeys[0]);
    const evidence = process.env.STORE_OPS_PARITY_EVIDENCE_DIR ?? info.outputDir;
    await mkdir(evidence, { recursive: true });
    await page.getByTestId("growth-approval-created").scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(evidence, `growth-submit-confirmed-${width}.png`), animations: "disabled" });
    await Promise.all([
      page.waitForURL(url => url.searchParams.get("item") === first.id),
      page.getByTestId(`growth-item-${first.id}`).click(),
    ]);
    await page.getByTestId("growth-transition-action").click();
    await expect(page.getByTestId("growth-transition-panel").getByRole("alert")).toContainText("尚未確認生命週期");
    await expect(page.getByTestId("growth-item-detail")).toContainText("已核准");
    const transitionAxe = await new AxeBuilder({ page }).include('[data-testid="growth-transition-panel"]').analyze();
    expect(transitionAxe.violations).toEqual([]);
    await page.getByTestId("growth-transition-panel").scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(evidence, `growth-transition-uncertain-${width}.png`), animations: "disabled" });
    await page.getByTestId("growth-transition-action").click();
    await expect(page.getByTestId("growth-transition-action")).toBeDisabled();
    await expect.poll(() => transitionKeys.length).toBe(2);
    await Promise.all([
      page.waitForURL(url => url.searchParams.get("item") === second.id),
      page.getByTestId(`growth-item-${second.id}`).click(),
    ]);
    release();
    await expect(page.getByTestId("growth-item-detail")).toContainText(second.id);
    await expect(page.getByTestId("growth-transition-action")).toHaveText("排程上線");
    await expect(page.getByTestId("growth-transition-action")).toBeEnabled();
    expect(transitionKeys[1]).toBe(transitionKeys[0]);
    const geometry = await page.getByTestId("growth-item-detail").evaluate(element => ({
      viewport: innerWidth, documentWidth: document.documentElement.scrollWidth,
      width: element.getBoundingClientRect().width, scrollWidth: element.scrollWidth,
    }));
    expect(geometry.documentWidth).toBeLessThanOrEqual(width);
    expect(geometry.scrollWidth).toBeLessThanOrEqual(Math.ceil(geometry.width));
    await page.getByTestId("growth-transition-panel").scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(evidence, `growth-record-ownership-${width}.png`), animations: "disabled" });
    await writeFile(path.join(evidence, `growth-lifecycle-${width}.json`), JSON.stringify({
      geometry, submitAttempts: submitKeys.length, transitionAttempts: transitionKeys.length,
      submitRetryKeyStable: true, transitionRetryKeyStable: true, oldResponseDidNotOverwriteSelectedRecord: true,
      submitErrorAxeViolations: errorAxe.violations, transitionErrorAxeViolations: transitionAxe.violations,
      scope: "Synthetic UI requests; no production or real-account approval",
    }, null, 2));
  });
}
