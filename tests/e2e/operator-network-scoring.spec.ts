import { expect, request as playwrightRequest, test } from "@playwright/test";

// ODP-OC-R4-006 — Candidate data gate, SiteScore Lab, and Compare.
// Screens verified against package 6 (sha db3ea3d…): data-screen-label values
// "Network 候選點工作台" / "Network SiteScore Lab" / "Network 候選點比較".

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
const SCORING_HEADERS = {
  "x-subject-id": "operator-expansion-manager",
  "x-roles": "expansion_user",
  "x-operator-role": "expansion-manager",
  "x-tenant-id": "tenant-a",
};

test.describe.configure({ mode: "serial" });

test.describe("ODP-OC-R4-006 Network SiteScore scoring", () => {
  test.beforeEach(async () => {
    const api = await apiContext();
    const reset = await api.post("/api/v1/operator/network-scoring/reset");
    expect(reset.status()).toBe(200);
    await api.dispose();
  });

  test("Candidate gate blocks CS-1003 and exposes the golden-flow candidates", async ({ page }) => {
    await page.goto("/operator?ws=network");
    await expect(page.getByTestId("network-find-areas-workspace")).toBeVisible();

    await page.getByTestId("network-tab-2").click();
    await expect(page.getByTestId("network-panel-candidates")).toBeVisible();
    const table = page.getByTestId("network-candidate-table");
    await expect(table).toContainText("CS-1001", { timeout: 15_000 });
    await expect(table).toContainText("SiteScore v2.3");

    // CS-1001 scored GO 82; CS-1003 gate-blocked ("缺資料 — 無法評分").
    await expect(page.getByTestId("candidate-score-value-CS-1001")).toContainText("GO 82");
    await expect(page.getByTestId("candidate-gate-block-CS-1003")).toContainText("缺資料 — 無法評分");
    await expect(page.getByTestId("candidate-blocked-CS-1003")).toBeDisabled();

    // The gate is enforced server-side: scoring CS-1003 returns 422.
    const api = await apiContext();
    const blocked = await api.post("/api/v1/operator/network-scoring/candidates/CS-1003/score", {
      data: { actorRoleId: "expansionManager" },
    });
    expect(blocked.status()).toBe(422);
    await api.dispose();
  });

  test("SiteScore Lab renders GO/WAIT/REJECT scorecards with conditions and reasons", async ({ page }) => {
    await page.goto("/operator?ws=network");
    await page.getByTestId("network-tab-3").click();
    await expect(page.getByTestId("network-panel-sitescore")).toBeVisible();

    const cs1001 = page.getByTestId("sitescore-card-CS-1001");
    await expect(cs1001).toContainText("SiteScore v2.3", { timeout: 15_000 });
    await expect(cs1001).toContainText("FS-20260704-0600");
    await expect(cs1001).toContainText("GO 82");

    // WAIT conditions and REJECT reasons are exposed on the scorecards.
    await expect(page.getByTestId("sitescore-conditions-CS-1002")).toContainText("站前施工");
    await expect(page.getByTestId("sitescore-conditions-CS-1004")).toContainText("回本期 41 個月");

    // CS-1003 has no scorecard — it is surfaced in the gate banner instead.
    await expect(page.getByTestId("sitescore-blocked-CS-1003")).toContainText("缺資料 — 無法評分");
  });

  test("Compare recommends primary / alternate / avoid consistently", async ({ page }) => {
    await page.goto("/operator?ws=network");
    await page.getByTestId("network-tab-4").click();
    await expect(page.getByTestId("network-panel-compare")).toBeVisible();

    await expect(page.getByTestId("compare-primary")).toContainText("信義松仁", { timeout: 15_000 });
    await expect(page.getByTestId("compare-primary")).toContainText("GO 82");
    await expect(page.getByTestId("compare-alternate")).toContainText("板橋府中");
    await expect(page.getByTestId("compare-alternate")).toContainText("WAIT 76");
    await expect(page.getByTestId("compare-avoid")).toContainText("大安和平");
    await expect(page.getByTestId("compare-avoid")).toContainText("REJECT 49");

    const compareTable = page.getByTestId("network-compare-table");
    await expect(compareTable).toContainText("SiteScore");
    await expect(compareTable).toContainText("82 GO");
  });

  test("batch SiteScore job sorts persisted results and skips gated candidate", async () => {
    const api = await apiContext();
    const response = await api.post("/api/v1/operator/network-scoring/score", {
      headers: { "idempotency-key": "e2e-r4-006-batch" },
      data: { actorRoleId: "expansionManager", actorName: "王若寧" },
    });
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body.scoredCandidateIds).toEqual(["CS-1001", "CS-1002", "CS-1004"]);
    expect(body.skipped.map((item: { candidateId: string }) => item.candidateId)).toEqual(["CS-1003"]);
    expect(body.batchResults.map((row: { id: string }) => row.id)).toEqual([
      "CS-1001",
      "CS-1002",
      "CS-1004",
    ]);
    await api.dispose();
  });
});

async function apiContext() {
  return playwrightRequest.newContext({
    baseURL: API_BASE_URL,
    extraHTTPHeaders: SCORING_HEADERS,
  });
}
