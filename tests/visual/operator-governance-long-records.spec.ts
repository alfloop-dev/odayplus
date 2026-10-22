/**
 * Supplemental synthetic HTTP geometry controls, not canonical product E2E or
 * native-owner / human UAT evidence. Run only through the explicit visual config.
 */
import { expect, test } from "@playwright/test";


// Explicit synthetic HTTP contract controls, not native evidence or human UAT.
// Preserve every original workflow test above; exercise actual browser layout
// with the long canonical values that must remain readable in Decision Log.
for (const width of [390, 1024]) {
  test(`Decision Log preserves long canonical fields within its ${width}px region`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => sessionStorage.setItem("oday.operator.role", "ops-lead"));
    const decision = {
      id: `dec-${"1234567890".repeat(14)}`, module: "Growth",
      item: `活動核准：${"unbroken-canonical-action-title-".repeat(12)}`,
      systemRecommendation: "Recorded recommendation", finalDecision: "Rejected",
      reason: `Recorded reason ${"review-evidence-".repeat(15)}`,
      actor: `actual-canonical-subject-${"a".repeat(120)}`,
      decidedAt: "2026-10-15T20:16:21.665178+00:00", model: null,
      datasetSnapshot: `explicit-local-manual-priceops-input:sha256:${"b".repeat(64)}`,
      approvalId: `APR-${"c".repeat(100)}`,
    };
    await page.route("**/api/v1/operator/governance/snapshot", route => route.fulfill({
      status: 200, contentType: "application/json", body: JSON.stringify({
        source: "canonical", approvals: [], decisions: [decision], auditRows: [], evidencePackages: [],
        statusBoard: { dataQuality: [], models: [], connectors: [], sla: [], users: [], runbooks: [] },
        actionAuthority: { verified: true, systemRoles: ["operations_manager"], decide: true, exportEvidence: false },
      }),
    }));
    await page.goto("/operator?ws=govern&tab=decisions");
    const region = page.getByRole("region", { name: "Decision Log", exact: true });
    const card = region.locator("article");
    await expect(card).toHaveCount(1);
    for (const text of [decision.id, decision.item, decision.reason, decision.actor, decision.datasetSnapshot, decision.approvalId]) {
      await expect(card).toContainText(text);
    }
    await expect(card.locator("footer span").filter({ hasText: /^模型：/ })).toHaveText("模型：n/a");
    const boxes = await region.evaluate(element => {
      const inside = Array.from(element.querySelectorAll("article, article header > *, article footer > *, article p, article section"));
      const rangeBoxes: Array<{ left: number; right: number }> = [];
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const node = walker.currentNode;
        if (!node.textContent?.trim()) continue;
        const range = document.createRange(); range.selectNodeContents(node);
        for (const rect of Array.from(range.getClientRects())) rangeBoxes.push({ left: rect.left, right: rect.right });
      }
      const rect = element.getBoundingClientRect();
      return { region: { left: rect.left, right: rect.right, client: element.clientWidth, scroll: element.scrollWidth },
        elements: inside.map(node => { const r = node.getBoundingClientRect(); return { left: r.left, right: r.right,
          client: node.clientWidth, scroll: node.scrollWidth }; }),
        text: rangeBoxes, document: { client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth } };
    });
    expect(boxes.region.scroll).toBeLessThanOrEqual(boxes.region.client);
    expect(boxes.document.scroll).toBeLessThanOrEqual(width);
    for (const box of boxes.elements) {
      expect(box.left).toBeGreaterThanOrEqual(boxes.region.left);
      expect(box.right).toBeLessThanOrEqual(boxes.region.right);
      expect(box.scroll).toBeLessThanOrEqual(box.client);
    }
    for (const box of boxes.text) {
      expect(box.left).toBeGreaterThanOrEqual(boxes.region.left);
      expect(box.right).toBeLessThanOrEqual(boxes.region.right);
    }
  });
}
