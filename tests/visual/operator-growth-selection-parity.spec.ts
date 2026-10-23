import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { FIXTURE_FRESHNESS, GROWTH_ITEMS, PRICEOPS_RECOMMENDATIONS, SEGMENTS } from "../../apps/web/features/operator/growthViewModel";

// Explicit synthetic navigation checks outside the fixed business inventory.
for (const width of [1440, 1024, 390]) {
  test(`Growth URL target never substitutes another record at ${width}`, async ({ page }, info) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => sessionStorage.setItem("oday.operator.role", "marketing-manager"));
    const first = { ...GROWTH_ITEMS[4], id: "synthetic-selection-first", status: "DRAFT", kind: "offpeak" };
    const second = { ...GROWTH_ITEMS[4], id: "synthetic-selection-second", status: "OUTCOME_READY", kind: "winback" };
    const empty = { ...SEGMENTS[0], id: "synthetic-empty-segment", name: "Synthetic empty segment" };
    let writes = 0;
    await page.route("**/api/v1/operator/growth/**", async route => {
      const request = route.request();
      if (request.method() === "POST") { writes += 1; return route.fulfill({ status: 503, body: "Unexpected synthetic write" }); }
      const url = new URL(request.url());
      const body = url.pathname.endsWith("/freshness") ? FIXTURE_FRESHNESS
        : { items: url.pathname.endsWith("/segments") ? [...SEGMENTS, empty] : url.pathname.endsWith("/recommendations") ? PRICEOPS_RECOMMENDATIONS : [first, second] };
      return route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
    });
    const missing = "/operator?ws=growth&item=missing-action&campaign_ref=handoff&campaign_ref=audit&empty_marker=";
    await page.goto(missing);
    await expect(page.getByTestId(`growth-item-${first.id}`)).toBeVisible();
    await expect(page.getByTestId("growth-action-unavailable")).toContainText("指定活動未取得");
    await expect(page.getByTestId("growth-item-detail")).toHaveCount(0);
    const axe = await new AxeBuilder({ page }).include('[data-testid="growth-action-unavailable"]').analyze();
    expect(axe.violations).toEqual([]);
    const evidence = process.env.STORE_OPS_PARITY_EVIDENCE_DIR ?? info.outputDir;
    await mkdir(evidence, { recursive: true });
    await page.getByTestId("growth-action-unavailable").scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(evidence, `growth-missing-target-${width}.png`), animations: "disabled" });
    await Promise.all([page.waitForURL(url => url.searchParams.get("item") === second.id), page.getByTestId(`growth-item-${second.id}`).click()]);
    await expect(page.getByTestId("growth-item-detail")).toContainText(second.id);
    await expect(page).toHaveURL(/campaign_ref=handoff/);
    expect(new URL(page.url()).searchParams.getAll("campaign_ref")).toEqual(["handoff", "audit"]);
    expect(new URL(page.url()).searchParams.has("empty_marker")).toBe(true);
    await page.reload();
    await expect(page.getByTestId("growth-item-detail")).toContainText(second.id);
    await page.goBack();
    await expect(page.getByTestId("growth-action-unavailable")).toBeVisible();
    await expect(page.getByTestId("growth-item-detail")).toHaveCount(0);
    await page.goForward();
    await expect(page.getByTestId("growth-item-detail")).toContainText(second.id);
    await page.goto("/operator?ws=growth&gstatus=out");
    await expect(page.getByTestId("growth-item-detail")).toContainText(second.id);
    await expect(page.getByTestId(`growth-item-${first.id}`)).toHaveCount(0);
    await page.goto(`/operator?ws=growth&segment=${empty.id}`);
    await expect(page.getByTestId("growth-action-unavailable")).toContainText("沒有符合目前條件");
    await expect(page.getByTestId("growth-item-detail")).toHaveCount(0);
    await page.goto("/operator?ws=growth&segment=missing-segment&campaign_ref=handoff");
    await expect(page.getByTestId("growth-segment-unavailable")).toBeVisible();
    await page.getByTestId("growth-tab-segments").click();
    await expect(page).toHaveURL(/segment=missing-segment/);
    await expect(page).toHaveURL(/campaign_ref=handoff/);
    await page.goto("/operator?ws=growth&draft=missing-recommendation");
    await expect(page.getByTestId("growth-draft-unavailable")).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(writes).toBe(0);
    const geometry = await page.getByTestId("growth-draft-unavailable").evaluate(element => ({ width: element.getBoundingClientRect().width, scrollWidth: element.scrollWidth, documentWidth: document.documentElement.scrollWidth, viewport: innerWidth }));
    expect(geometry.documentWidth).toBeLessThanOrEqual(width);
    await writeFile(path.join(evidence, `growth-selection-${width}.json`), JSON.stringify({ scope: "Synthetic local navigation; not canonical source or real-role acceptance", geometry, missingTargetHasNoOtherDetail: true, exactValidTargetReloadBackForward: true, defaultRespectsFilter: true, emptySegmentNotBroadened: true, unknownSegmentPreserved: true, missingDraftNoModal: true, unrelatedQueryPreserved: true, writes, axeViolations: axe.violations }, null, 2));
  });
}
