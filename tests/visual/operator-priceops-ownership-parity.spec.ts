/** Real two-actor HTTP flow on isolated canonical fixtures; no real-role admission. */
import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { FIXTURE_FRESHNESS, GROWTH_ITEMS, SEGMENTS } from "../../apps/web/features/operator/growthViewModel";

for (const width of [1440, 1024, 390]) {
  test(`canonical ownership and independent pricing approval at ${width}px`, async ({ page, request }, info) => {
    test.setTimeout(60_000);
    await page.setViewportSize({ width, height: 1000 });
    await page.addInitScript(() => sessionStorage.setItem("oday.operator.role", "marketing-manager"));
    const api = `http://127.0.0.1:${Number(process.env.ODP_API_PORT ?? 8099)}`;
    const planId = `canonical-ui-${width}`;
    const headers = { "x-subject-id": "verified-creator", "x-roles": "pricing_manager", "x-tenant-id": "tenant-a" };
    const comparison = await (await request.get(`${api}/priceops/plans/${planId}/comparison`, { headers })).json();
    const item = comparison.items[0];
    const recId = `synthetic-ownership-rec-${width}`;
    let actor = "verified-creator";
    await page.route("**/api/v1/operator/growth/**", route => {
      const pathname = new URL(route.request().url()).pathname;
      const rec = { id: recId, segmentId: SEGMENTS[0].id, planId, itemId: item.item_id,
        currentPrice: item.current_price, recommendedPrice: item.candidate_price, constraintStatus: item.constraint_status, storeId: "store-a" };
      const body = pathname.endsWith("/freshness") ? FIXTURE_FRESHNESS : { items: pathname.endsWith("/segments") ? SEGMENTS : pathname.endsWith("/recommendations") ? [rec] : GROWTH_ITEMS };
      return route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
    });
    await page.route("**/api/v1/priceops/**", async route => {
      const pathname = new URL(route.request().url()).pathname.replace(/^\/api\/v1/, "");
      const response = await route.fetch({ url: `${api}${pathname}`, headers: { ...route.request().headers(), ...headers, "x-subject-id": actor } });
      return route.fulfill({ response });
    });
    await page.goto(`/operator?ws=growth&gtab=priceops&prec=${recId}`);
    const pane = page.getByTestId("priceops-scenario-workbench");
    await expect(page.getByTestId("growth-recommendation-table")).toContainText(recId);
    await expect(page.getByTestId("priceops-simulate-btn")).toBeEnabled();
    await page.getByTestId("priceops-simulate-btn").click();
    await expect(page.getByTestId("priceops-alternative-band")).not.toContainText("尚未取得");
    await page.getByLabel("決策原因").fill("Synthetic creator approval attempt");
    await page.getByLabel("決策", { exact: true }).selectOption("approved");
    await expect(page.getByTestId("priceops-writeback-btn")).toBeDisabled();
    await expect(pane).toContainText("建立者不能核准自己的調價計畫");
    const ownAxe = await new AxeBuilder({ page }).include('[data-testid="priceops-scenario-workbench"]').analyze();
    expect(ownAxe.violations).toEqual([]);
    const evidence = process.env.STORE_OPS_PARITY_EVIDENCE_DIR ?? info.outputDir;
    await mkdir(evidence, { recursive: true });
    await pane.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(evidence, `priceops-creator-denied-${width}.png`), animations: "disabled", fullPage: true });
    const denied = await request.post(`${api}/priceops/plans/${planId}/approve`, { headers, data: { actor_id: "verified-reviewer", decision: "approved", reason: "forged reviewer" } });
    expect(denied.status()).toBe(403);
    const before = await (await request.get(`${api}/_synthetic/evidence`)).json();
    expect(before.approvals.filter((row: { plan_id: string }) => row.plan_id === planId)).toHaveLength(0);
    actor = "verified-reviewer";
    await page.reload();
    await expect(page.getByTestId("priceops-simulate-btn")).toBeEnabled();
    await page.getByTestId("priceops-simulate-btn").click();
    await expect(page.getByTestId("priceops-alternative-band")).not.toContainText("尚未取得");
    await page.getByLabel("決策原因").fill(`Synthetic independent approval ${width}`);
    await page.getByLabel("決策", { exact: true }).selectOption("approved");
    await expect(page.getByTestId("priceops-writeback-btn")).toBeEnabled();
    await page.getByTestId("priceops-writeback-btn").click();
    await expect(page.getByTestId("priceops-audit-status")).toContainText("verified-reviewer");
    const after = await (await request.get(`${api}/_synthetic/evidence`)).json();
    const plan = after.plans.find((row: { plan_id: string }) => row.plan_id === planId);
    const approvals = after.approvals.filter((row: { plan_id: string }) => row.plan_id === planId);
    const decisions = after.decisions.filter((row: { plan_id: string }) => row.plan_id === planId);
    expect(plan.created_by).toBe("verified-creator"); expect(plan.status).toBe("approved_for_pilot");
    expect(approvals).toHaveLength(1); expect(approvals[0].actor_id).toBe("verified-reviewer");
    expect(decisions).toHaveLength(1); expect(decisions[0].actor).toBe("verified-reviewer");
    expect(decisions[0].reason).toBe(`Synthetic independent approval ${width}`);
    const axe = await new AxeBuilder({ page }).include('[data-testid="priceops-scenario-workbench"]').analyze();
    expect(axe.violations).toEqual([]);
    const geometry = await pane.evaluate(element => ({ viewport: innerWidth, documentWidth: document.documentElement.scrollWidth, width: element.getBoundingClientRect().width, scrollWidth: element.scrollWidth }));
    expect(geometry.documentWidth).toBeLessThanOrEqual(width);
    await pane.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(evidence, `priceops-independent-approved-${width}.png`), animations: "disabled", fullPage: true });
    await writeFile(path.join(evidence, `priceops-ownership-${width}.json`), JSON.stringify({ scope: "ISOLATED_SYNTHETIC_NOT_REAL_ROLE_OR_PRODUCTION_ACCEPTANCE", plan, approvals, decisions, ownAttemptStatus: denied.status(), ownAxe: ownAxe.violations, reviewerAxe: axe.violations, geometry }, null, 2));
  });
}
