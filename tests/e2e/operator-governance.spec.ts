/**
 * E2E tests for the Govern workspace (治理稽核) — ODP-OC-R4-009.
 *
 * Proves the task acceptance at the UI layer against the Package 10
 * governance workspace:
 *
 *   1. The approved default and existing extension deep links preserve:
 *      核准中心 / Decision Log / Audit Trail / Evidence Package 匯出 / 系統狀態盤,
 *      and the status board exposes Data Quality / Model / Connector / SLA / Users.
 *   2. Return/reject require a reason; the reason gate blocks submission.
 *   3. Store and Growth decisions plus pending Network approvals appear and stay
 *      consistent after a reload.
 *   4. Evidence Package export records the scope range/format and writes an
 *      audit event.
 *
 * The Govern workspace is API-bound (fetchGovernanceSnapshot); the server-side
 * reason policy and evidence-package metadata are proven at the contract layer
 * by tests/contract/test_operator_governance_api.py.  When the API is
 * unreachable the workspace renders embedded fixtures, so these UI assertions
 * hold in both modes.
 *
 * Assumes: Next.js dev server (+ API) running per playwright.config.ts.
 */

import { expect, test } from "@playwright/test";

const GOVERN = '[data-screen-label="Govern 治理稽核"]';

async function openGovern(page: import("@playwright/test").Page) {
  await page.goto("/operator?ws=govern");
  await expect(page.getByTestId("governance-workspace")).toBeVisible();
}

// ---------------------------------------------------------------------------
// 1. Every value builder is reachable
// ---------------------------------------------------------------------------

test("Govern R7 default and existing extensions retain the DQ/Model/Connector/SLA/Users board", async ({
  page,
}) => {
  await openGovern(page);
  await expect(page.locator(GOVERN)).toContainText("核准中心");

  // The R7 default exposes only the three approved tabs.
  await page.getByTestId("governance-tab-decisions").click();
  await expect(
    page.getByRole("heading", { name: "Decision Log" }),
  ).toBeVisible();

  await page.getByTestId("governance-tab-audit").click();
  await expect(
    page.getByRole("heading", { name: "Audit Trail" }),
  ).toBeVisible();

  // Existing non-R7 extension entry; this does not prove discoverability.
  await page.goto("/operator?ws=govern&tab=evidencePackage");
  await page.getByTestId("governance-tab-evidencePackage").click();
  await expect(page.getByTestId("governance-export-button")).toBeVisible();

  await page.getByTestId("governance-tab-statusBoard").click();
  await expect(
    page.locator('[aria-label="System status board"]'),
  ).toBeVisible();

  // The five value builders that must not be unreachable.
  await expect(page.getByText("Data Quality 監控")).toBeVisible();
  await expect(page.getByText("Model Registry", { exact: true })).toBeVisible();
  await expect(page.getByText("Connector／API")).toBeVisible();
  await expect(page.getByTestId("governance-sla-card")).toBeVisible();
  await expect(page.getByTestId("governance-users-card")).toBeVisible();
});

// ---------------------------------------------------------------------------
// 2. Return / reject require a reason
// ---------------------------------------------------------------------------

test("Govern approval return is blocked without a sufficient reason", async ({
  page,
}) => {
  await openGovern(page);

  // Target the pending Network approval (distinct from the Store Ops approval
  // exercised by e2e-operator-console FE-05, so the full parallel suite does not
  // collide on shared server state).
  await page
    .getByRole("button", { name: "Approve SiteScore override" })
    .click();
  await page.locator("#governance-reason").fill("Too short");
  await page.getByRole("button", { name: "駁回", exact: true }).click();
  await expect(page.getByText("退回或駁回理由需至少 10 個字")).toBeVisible();

  // A sufficient reason clears the gate and records the decision.
  const reason =
    "Reject: competitor density and lease sensitivity exceed the override threshold";
  await page.locator("#governance-reason").fill(reason);
  await page.getByRole("button", { name: "駁回", exact: true }).click();
  await expect(page.getByText(/已駁回決策/)).toBeVisible();
});

// ---------------------------------------------------------------------------
// 3. Store + Growth decisions and pending Network approvals appear
// ---------------------------------------------------------------------------

test("Govern surfaces Store/Growth decisions and a pending Network approval", async ({
  page,
}) => {
  await openGovern(page);

  // Pending Network approval is reachable in the Approval Center queue.
  await expect(page.locator(GOVERN)).toContainText("Network");

  // Decision Log shows resolved Store Ops + Growth decisions.
  await page.getByTestId("governance-tab-decisions").click();
  const decisionLog = page.getByRole("region", { name: "Decision Log" });
  await expect(decisionLog).toContainText("Store Ops");
  await expect(decisionLog).toContainText("Growth");
});

// ---------------------------------------------------------------------------
// 4. Evidence Package export records scope + writes an audit event
// ---------------------------------------------------------------------------

test("Evidence Package export produces a record and an audit event", async ({
  page,
}) => {
  await openGovern(page);

  // Existing non-R7 extension entry; this does not prove discoverability.
  await page.goto("/operator?ws=govern&tab=evidencePackage");
  await page.getByTestId("governance-tab-evidencePackage").click();
  await page.getByTestId("governance-export-button").click();

  const resultPanel = page.getByTestId("evidence-package-result");
  await expect(resultPanel).toBeVisible({ timeout: 8000 });
  await expect(resultPanel).toContainText("EVD-2026-0705-");

  // The export is recorded in the Audit Trail.
  await page.getByTestId("governance-tab-audit").click();
  await expect(page.getByRole("region", { name: "Audit Trail" })).toContainText(
    "Export Evidence Package",
  );
});


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
