/** Real PriceOps HTTP mutations on an isolated synthetic server, outside formal role acceptance. */
import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { FIXTURE_FRESHNESS, GROWTH_ITEMS, SEGMENTS } from "../../apps/web/features/operator/growthViewModel";

for (const width of [1440, 1024, 390]) {
  test(`synthetic real PriceOps acknowledgments and uncertain retry at ${width}px`, async ({ page, request }, info) => {
    test.setTimeout(60_000);
    await page.setViewportSize({ width, height: 1000 });
    await page.addInitScript(() => sessionStorage.setItem("oday.operator.role", "marketing-manager"));
    const api = `http://127.0.0.1:${Number(process.env.ODP_API_PORT ?? 8099)}`;
    const before = await (await request.get(`${api}/_synthetic/evidence`)).json();
    const rec = { id: "synthetic-price-rec", segmentId: SEGMENTS[0].id, planId: "plan-a", itemId: "item-plan-a",
      currentPrice: 80, recommendedPrice: 85, expectedIncrementalGrossMargin: 17, constraintStatus: "SAFE", storeId: "store-a" };
    await page.route("**/api/v1/operator/growth/**", route => {
      const pathname = new URL(route.request().url()).pathname;
      const body = pathname.endsWith("/freshness") ? FIXTURE_FRESHNESS : { items: pathname.endsWith("/segments") ? SEGMENTS : pathname.endsWith("/recommendations") ? [rec] : GROWTH_ITEMS };
      return route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
    });
    const writes: { body: unknown; key: string; correlation: string; status: number; payload: unknown }[] = [];
    let loseResponse = true;
    await page.route("**/api/v1/priceops/**", async route => {
      const browserRequest = route.request();
      const pathname = new URL(browserRequest.url()).pathname.replace(/^\/api\/v1/, "");
      const headers = { ...browserRequest.headers(), "x-subject-id": "verified-pricer", "x-roles": "pricing_manager", "x-tenant-id": "tenant-a" };
      const response = await route.fetch({ url: `${api}${pathname}`, headers });
      if (pathname.endsWith("decision-writeback")) {
        writes.push({ body: browserRequest.postDataJSON(), key: headers["idempotency-key"], correlation: headers["x-correlation-id"], status: response.status(), payload: await response.json() });
        if (loseResponse) { loseResponse = false; return route.abort("failed"); }
      }
      return route.fulfill({ response });
    });
    await page.goto("/operator?ws=growth&gtab=priceops&handoff=review");
    await expect(page.getByTestId("growth-recommendation-table")).toContainText("synthetic-price-rec");
    await expect(page.getByTestId("priceops-scenario-workbench")).toHaveCount(0);
    const evidence = process.env.STORE_OPS_PARITY_EVIDENCE_DIR ?? info.outputDir;
    await mkdir(evidence, { recursive: true });
    await page.screenshot({ path: path.join(evidence, `priceops-r7-default-${width}.png`), animations: "disabled", fullPage: true });
    await page.goto("/operator?ws=growth&gtab=priceops&prec=missing&handoff=review");
    await expect(page.getByTestId("growth-recommendation-table")).toContainText("synthetic-price-rec");
    await expect(page.getByTestId("priceops-selection-unavailable")).toBeVisible();
    await expect(page.getByTestId("priceops-scenario-workbench")).toHaveCount(0);
    await page.getByRole("link", { name: "查看 synthetic-price-rec 定價情境", exact: true }).click();
    await expect(page).toHaveURL(/prec=synthetic-price-rec/);
    await expect(page).toHaveURL(/handoff=review/);
    await page.reload();
    const workbench = page.getByTestId("priceops-scenario-workbench");
    await expect(workbench).toBeVisible();
    await expect(page.getByTestId("priceops-simulate-btn")).toBeEnabled();
    await page.getByLabel("調整情境價:").fill("85");
    await page.getByTestId("priceops-simulate-btn").click();
    await expect(page.getByTestId("priceops-alternative-band")).not.toContainText("尚未取得");
    await page.getByLabel("決策原因").fill(`Synthetic acceptance reason ${width}`);
    await page.getByTestId("priceops-writeback-btn").click();
    await expect(workbench).toContainText("尚未確認決策寫回");
    await expect(page.getByTestId("priceops-audit-status")).toHaveCount(0);
    await page.getByTestId("priceops-writeback-btn").click();
    await expect(page.getByTestId("priceops-audit-status")).toContainText("verified-pricer");
    expect(writes).toHaveLength(2);
    expect(writes[0].status).toBe(200); expect(writes[1].status).toBe(200);
    expect(writes[1].key).toBe(writes[0].key); expect(writes[1].correlation).toBe(writes[0].correlation);
    expect(writes[1].body).toEqual(writes[0].body);
    const after = await (await request.get(`${api}/_synthetic/evidence`)).json();
    expect(after.decisions.length).toBe(before.decisions.length + 1);
    const added = after.decisions.at(-1);
    expect(added.actor).toBe("verified-pricer"); expect(added.reason).toBe(`Synthetic acceptance reason ${width}`);
    expect(after.events.filter((event: { event_type: string }) => event.event_type === "priceops.decision_written_back.v1").length).toBe(before.events.filter((event: { event_type: string }) => event.event_type === "priceops.decision_written_back.v1").length + 1);
    await expect(page.getByTestId("priceops-audit-status")).toContainText(added.decision_id);
    const axe = await new AxeBuilder({ page }).include('[data-testid="priceops-scenario-workbench"]').analyze();
    expect(axe.violations).toEqual([]);
    const geometry = await workbench.evaluate(element => ({ width: element.getBoundingClientRect().width, scrollWidth: element.scrollWidth, viewport: innerWidth, documentWidth: document.documentElement.scrollWidth }));
    expect(geometry.documentWidth).toBeLessThanOrEqual(width);
    await workbench.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(evidence, `priceops-real-command-${width}.png`), animations: "disabled", fullPage: true });
    await writeFile(path.join(evidence, `priceops-real-command-${width}.json`), JSON.stringify({ scope: "ISOLATED_SYNTHETIC_NOT_PRODUCTION_ROLE_ACCEPTANCE", writes, added, geometry, axeViolations: axe.violations }, null, 2));
  });
}
