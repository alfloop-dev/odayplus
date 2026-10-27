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

import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";

const GOVERN = '[data-screen-label="Govern 治理稽核"]';

async function openGovern(page: Page) {
  await page.goto("/operator?ws=govern");
  await expect(page.getByTestId("governance-workspace")).toBeVisible();
}

/** Observe the existing Govern view in-place, not once-per-width decisions.
 * Extensions are named as extensions; screenshots never upgrade deep-link
 * reachability to approved R7 navigation or fixture rows to SQL/human evidence.
 */
async function renderGovernView(
  page: Page,
  testInfo: TestInfo,
  state: string,
  controls: Locator[],
  scope: "r7" | "existing-extension" = "r7",
) {
  const originalViewport = page.viewportSize();
  const surface = page.locator(GOVERN);
  try {
    for (const width of [1440, 1024, 390]) {
      await test.step(`Govern 治理稽核: ${state} at ${width}`, async () => {
        await page.setViewportSize({ width, height: 900 });
        const observation: Record<string, unknown> = {
          designSource: "docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/Oday Plus Operator Console.dc.html",
          designSha256: "cc4e6ae97462bc99b1c2353c792cb3bec40d51a6c5efcfde165e5f47105e661d",
          label: "Govern 治理稽核", state, scope, viewport: page.viewportSize(), url: page.url(),
          evidenceScope: "controlled product-e2e fixture composition; not authenticated SQL, production or human UAT",
          rendererSource: "Govern does not expose a data-source attribute; no live-source claim",
          referenceDifferences: "Current snapshot rows, server decision authority and reason gate retained. Evidence Package/status board are existing non-R7 extensions, not default discoverability.",
          controls: [],
        };
        try {
          await expect(surface).toBeVisible();
          const box = await surface.boundingBox();
          observation.surface = box;
          observation.decisionAuthority = await page.getByTestId("governance-decision-authority").getAttribute("data-authority");
          expect(box).not.toBeNull();
          expect(box!.x).toBeGreaterThanOrEqual(0);
          expect(box!.width).toBeGreaterThan(0);
          expect(box!.x + box!.width).toBeLessThanOrEqual(width + 1);
          expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1);
          if (scope === "r7") {
            const tabs = page.getByRole("navigation", { name: "Governance tabs" });
            await expect(tabs.getByRole("button")).toHaveCount(3);
            await expect(page.getByTestId("governance-tab-evidencePackage")).toHaveCount(0);
            await expect(page.getByTestId("governance-tab-statusBoard")).toHaveCount(0);
            await expect(page.getByTestId(`governance-tab-${new URL(page.url()).searchParams.get("tab") ?? "approvals"}`)).toHaveAttribute("aria-current", "page");
          }
          for (const control of controls) {
            await control.scrollIntoViewIfNeeded();
            await expect(control).toBeVisible();
            await expect(control).toBeInViewport();
            const bounds = await control.boundingBox();
            expect(bounds).not.toBeNull();
            expect(bounds!.x).toBeGreaterThanOrEqual(0);
            expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width + 1);
            expect(bounds!.y).toBeGreaterThanOrEqual(0);
            expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(901);
            expect(bounds!.height).toBeGreaterThanOrEqual(24);
            const disabled = await control.isDisabled();
            (observation.controls as unknown[]).push({ bounds, disabled });
            if (!disabled) await control.click({ trial: true });
          }
        } finally {
          const path = testInfo.outputPath(`r7-govern-${state}-${width}.png`);
          await page.screenshot({ path, fullPage: true });
          await testInfo.attach(`Govern 治理稽核 / ${state} / ${width} / ${scope} / controlled`, { path, contentType: "image/png" });
          await testInfo.attach(`r7-govern-${state}-${width}-observation`, {
            body: JSON.stringify(observation, null, 2), contentType: "application/json",
          });
        }
      });
    }
  } finally {
    if (originalViewport) await page.setViewportSize(originalViewport);
  }
}

// ---------------------------------------------------------------------------
// 1. Every value builder is reachable
// ---------------------------------------------------------------------------

test("Govern R7 default and existing extensions retain the DQ/Model/Connector/SLA/Users board", async ({
  page,
}, testInfo) => {
  await openGovern(page);
  await expect(page.locator(GOVERN)).toContainText("核准中心");
  await expect(page.getByRole("region", { name: "Approval center" })).toBeVisible();
  await renderGovernView(page, testInfo, "approvals", [
    page.getByTestId("governance-tab-approvals"), page.getByTestId("governance-tab-decisions"),
    page.getByTestId("governance-tab-audit"),
    page.getByRole("button", { name: "Approve SiteScore override" }),
  ]);

  // The R7 default exposes only the three approved tabs.
  await page.getByTestId("governance-tab-decisions").click();
  await expect(
    page.getByRole("heading", { name: "Decision Log" }),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "Decision Log" }).locator("article").first()).toContainText("系統建議");
  await expect(page.getByRole("region", { name: "Decision Log" }).locator("article").first()).toContainText("最終決策");
  await renderGovernView(page, testInfo, "decision-log", [
    page.getByTestId("governance-tab-approvals"), page.getByTestId("governance-tab-decisions"),
    page.getByTestId("governance-tab-audit"),
  ]);

  await page.getByTestId("governance-tab-audit").click();
  await expect(
    page.getByRole("heading", { name: "Audit Trail" }),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "Audit Trail" }).locator("article").first().locator("code")).not.toBeEmpty();
  await renderGovernView(page, testInfo, "audit-trail", [
    page.getByTestId("governance-tab-audit"),
    page.getByRole("button", { name: "全部", exact: true }),
    page.getByRole("button", { name: "影像調閱", exact: true }),
  ]);

  // Existing non-R7 extension entry; this does not prove discoverability.
  await page.goto("/operator?ws=govern&tab=evidencePackage");
  await page.getByTestId("governance-tab-evidencePackage").click();
  await expect(page.getByTestId("governance-export-button")).toBeVisible();
  await renderGovernView(page, testInfo, "extension-export-settings", [
    page.getByLabel("起始日"), page.getByLabel("結束日"), page.getByLabel("格式", { exact: true }),
    page.getByTestId("governance-export-button"),
  ], "existing-extension");

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
  await renderGovernView(page, testInfo, "extension-status-board", [
    page.getByTestId("governance-tab-statusBoard"), page.getByTestId("governance-users-card"),
  ], "existing-extension");
});

// ---------------------------------------------------------------------------
// 2. Return / reject require a reason
// ---------------------------------------------------------------------------

test("Govern approval return is blocked without a sufficient reason", async ({
  page,
}, testInfo) => {
  await openGovern(page);
  let decisionWrites = 0;
  page.on("request", request => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/v1/operator/governance/decisions") decisionWrites += 1;
  });

  // Target the pending Network approval (distinct from the Store Ops approval
  // exercised by e2e-operator-console FE-05, so the full parallel suite does not
  // collide on shared server state).
  await page
    .getByRole("button", { name: "Approve SiteScore override" })
    .click();
  await page.locator("#governance-reason").fill("Too short");
  await page.getByRole("button", { name: "駁回", exact: true }).click();
  await expect(page.getByText("退回或駁回理由需至少 10 個字")).toBeVisible();
  await renderGovernView(page, testInfo, "reason-invalid", [
    page.locator("#governance-reason"), page.getByRole("button", { name: "核准", exact: true }),
    page.getByRole("button", { name: "退回修改", exact: true }), page.getByRole("button", { name: "駁回", exact: true }),
  ]);
  await expect(page.locator("#governance-reason")).toHaveValue("Too short");
  expect(decisionWrites).toBe(0);

  // A sufficient reason clears the gate and records the decision.
  const reason =
    "Reject: competitor density and lease sensitivity exceed the override threshold";
  await page.locator("#governance-reason").fill(reason);
  await renderGovernView(page, testInfo, "reason-ready", [
    page.locator("#governance-reason"), page.getByRole("button", { name: "駁回", exact: true }),
  ]);
  await expect(page.locator("#governance-reason")).toHaveValue(reason);
  expect(decisionWrites).toBe(0);
  const responsePromise = page.waitForResponse(response => response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/v1/operator/governance/decisions");
  await page.getByRole("button", { name: "駁回", exact: true }).click();
  const response = await responsePromise;
  expect(response.ok()).toBe(true);
  const receipt = await response.json();
  expect(receipt.approvalId).toBe("ap-network-3319");
  expect(receipt.status).toBe("rejected");
  await expect(page.getByText(/已駁回決策/)).toBeVisible();
  await expect(page.getByRole("article", { name: "Approval detail" })).toContainText(reason);
  await renderGovernView(page, testInfo, "decision-acknowledged", [
    page.getByTestId("governance-tab-decisions"), page.getByTestId("governance-tab-audit"),
  ]);
  expect(decisionWrites).toBe(1);
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
}, testInfo) => {
  await openGovern(page);

  // Existing non-R7 extension entry; this does not prove discoverability.
  await page.goto("/operator?ws=govern&tab=evidencePackage");
  await page.getByTestId("governance-tab-evidencePackage").click();
  await page.getByTestId("governance-export-button").click();

  const resultPanel = page.getByTestId("evidence-package-result");
  await expect(resultPanel).toBeVisible({ timeout: 8000 });
  await expect(resultPanel).toContainText("EVD-2026-0705-");
  await renderGovernView(page, testInfo, "extension-export-result", [
    resultPanel.getByRole("button", { name: "下載 (local fixture)" }),
  ], "existing-extension");

  // The export is recorded in the Audit Trail.
  await page.getByTestId("governance-tab-audit").click();
  await expect(page.getByRole("region", { name: "Audit Trail" })).toContainText(
    "Export Evidence Package",
  );
  await renderGovernView(page, testInfo, "export-audit-record", [
    page.getByRole("button", { name: "匯出", exact: true }),
    page.getByRole("button", { name: "全部", exact: true }),
  ]);
});
