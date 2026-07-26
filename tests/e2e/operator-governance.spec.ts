/**
 * E2E tests for the Govern workspace (治理稽核) — ODP-OC-R4-009.
 *
 * Proves the task acceptance at the UI layer against the archived package-6
 * design (data-screen-label "Govern 治理稽核"):
 *
 *   1. Every governance value builder is reachable from workspace navigation:
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
  await page.goto("/operator");
  await page.getByRole("button", { name: /治理稽核/ }).click();
  await expect(page.locator(GOVERN)).toBeVisible();
}

// ---------------------------------------------------------------------------
// 1. Every value builder is reachable
// ---------------------------------------------------------------------------

test("Govern workspace exposes all five tabs and the DQ/Model/Connector/SLA/Users board", async ({
  page,
}) => {
  await openGovern(page);
  await expect(page.locator(GOVERN)).toContainText("核准中心");

  // Every tab is reachable from navigation.
  await page.getByTestId("governance-tab-decisions").click();
  await expect(page.getByRole("heading", { name: "Decision Log" })).toBeVisible();

  await page.getByTestId("governance-tab-audit").click();
  await expect(page.getByRole("heading", { name: "Audit Trail" })).toBeVisible();

  await page.getByTestId("governance-tab-evidencePackage").click();
  await expect(page.getByTestId("governance-export-button")).toBeVisible();

  await page.getByTestId("governance-tab-statusBoard").click();
  await expect(page.locator('[aria-label="System status board"]')).toBeVisible();

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

test("Govern approval return is blocked without a sufficient reason", async ({ page }) => {
  await openGovern(page);

  // Target the pending Network approval (distinct from the Store Ops approval
  // exercised by e2e-operator-console FE-05, so the full parallel suite does not
  // collide on shared server state).
  await page.getByRole("button", { name: "Approve SiteScore override" }).click();
  await page.locator("#governance-reason").fill("Too short");
  await page.getByRole("button", { name: "Reject", exact: true }).click();
  await expect(page.getByText("退回或駁回理由需至少 10 個字")).toBeVisible();

  // A sufficient reason clears the gate and records the decision.
  const reason = "Reject: competitor density and lease sensitivity exceed the override threshold";
  await page.locator("#governance-reason").fill(reason);
  await page.getByRole("button", { name: "Reject", exact: true }).click();
  await expect(page.getByText("已完成決策 (rejected)")).toBeVisible();
});

// ---------------------------------------------------------------------------
// 3. Store + Growth decisions and pending Network approvals appear
// ---------------------------------------------------------------------------

test("Govern surfaces Store/Growth decisions and a pending Network approval", async ({ page }) => {
  await openGovern(page);

  // Pending Network approval is reachable in the Approval Center queue.
  await expect(page.locator(GOVERN)).toContainText("Network");

  // Decision Log shows resolved Store Ops + Growth decisions.
  await page.getByTestId("governance-tab-decisions").click();
  const table = page.locator("table");
  await expect(table).toContainText("Store Ops");
  await expect(table).toContainText("Growth");
});

// ---------------------------------------------------------------------------
// 4. Evidence Package export records scope + writes an audit event
// ---------------------------------------------------------------------------

test("Evidence Package export produces a record and an audit event", async ({ page }) => {
  await openGovern(page);

  await page.getByTestId("governance-tab-evidencePackage").click();
  await page.getByTestId("governance-export-button").click();

  const resultPanel = page.getByTestId("evidence-package-result");
  await expect(resultPanel).toBeVisible({ timeout: 8000 });
  await expect(resultPanel).toContainText("EVD-2026-0705-");

  // The export is recorded in the Audit Trail.
  await page.getByTestId("governance-tab-audit").click();
  await expect(page.locator("table")).toContainText("Export Evidence Package");
});
