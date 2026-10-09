import { expect, request as playwrightRequest, test } from "@playwright/test";

// ODP-OC-R4-006 — Candidate data gate, SiteScore Lab, and Compare.
// Screens verified against package 6 (sha db3ea3d…): data-screen-label values
// "Network 候選點工作台" / "Network SiteScore Lab" / "Network 候選點比較".

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
const SCORING_HEADERS = {
  "x-subject-id": "operator-expansion-manager",
  "x-roles": "expansion_user",
  "x-operator-role": "expansion-staff",
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

  test("Candidate gate blocks CS-1003 and exposes the golden-flow candidates", async ({
    page,
  }) => {
    await page.goto("/operator?ws=network");
    await expect(
      page.getByTestId("network-find-areas-workspace"),
    ).toBeVisible();

    await page.getByTestId("network-tab-2").click();
    await expect(page.getByTestId("network-panel-candidates")).toBeVisible({ timeout: 15_000 });
    const table = page.getByTestId("network-candidate-table");
    await expect(table).toContainText("CS-1001", { timeout: 15_000 });
    await expect(table).toContainText("SiteScore v2.3");

    // CS-1001 scored GO 82; CS-1003 gate-blocked ("缺資料 — 無法評分").
    await expect(
      page.getByTestId("candidate-score-value-CS-1001"),
    ).toContainText("GO 82");
    await expect(
      page.getByTestId("candidate-gate-block-CS-1003"),
    ).toContainText("缺資料 — 無法評分");
    await expect(page.getByTestId("candidate-blocked-CS-1003")).toBeDisabled();

    // The gate is enforced server-side: scoring CS-1003 returns 422.
    const api = await apiContext();
    const blocked = await api.post(
      "/api/v1/operator/network-scoring/candidates/CS-1003/score",
      {
        data: { actorRoleId: "expansionManager" },
      },
    );
    expect(blocked.status()).toBe(422);
    await api.dispose();
  });

  test("SiteScore Lab renders GO/WAIT/REJECT scorecards with conditions and reasons", async ({
    page,
  }) => {
    await page.goto("/operator?ws=network");
    await page.getByTestId("network-tab-3").click();
    await expect(page.getByTestId("network-panel-sitescore")).toBeVisible({ timeout: 15_000 });

    const cs1001 = page.getByTestId("sitescore-card-CS-1001");
    await expect(cs1001).toContainText("SiteScore v2.3", { timeout: 15_000 });
    await expect(cs1001).toContainText("FS-20260704-0600");
    await expect(cs1001).toContainText("GO");
    await expect(cs1001).toContainText("82");

    // WAIT conditions and REJECT reasons are exposed on the scorecards.
    await expect(
      page.getByTestId("sitescore-conditions-CS-1002"),
    ).toContainText("站前施工");
    await expect(
      page.getByTestId("sitescore-conditions-CS-1004"),
    ).toContainText("回本期 41 個月");

    // CS-1003 has no scorecard: selecting it shows the data gate, not an
    // unrelated scored candidate's report.
    await page.getByTestId("sitescore-pick-CS-1003").click();
    const blocked = page.getByTestId("sitescore-blocked-CS-1003");
    await expect(blocked).toBeVisible();
    await expect(blocked).toContainText("缺資料");
    await expect(blocked.getByRole("button")).toBeDisabled();
    await expect(cs1001).toBeHidden();
  });

  test("Compare recommends primary / alternate / avoid consistently", async ({
    page,
  }) => {
    await page.goto("/operator?ws=network");
    await page.getByTestId("network-tab-4").click();
    await expect(page.getByTestId("network-panel-compare")).toBeVisible({ timeout: 15_000 });

    await expect(page.getByTestId("compare-primary")).toContainText(
      "信義松仁",
      { timeout: 15_000 },
    );
    await expect(page.getByTestId("compare-primary")).toContainText("GO 82");
    await expect(page.getByTestId("compare-alternate")).toContainText(
      "板橋府中",
    );
    await expect(page.getByTestId("compare-alternate")).toContainText(
      "WAIT 76",
    );
    await expect(page.getByTestId("compare-avoid")).toContainText("大安和平");
    await expect(page.getByTestId("compare-avoid")).toContainText("REJECT 49");

    const compareTable = page.getByTestId("network-compare-table");
    await expect(compareTable).toContainText("SiteScore");
    await expect(compareTable).toContainText("82 GO");
  });

  test("batch SiteScore job sorts persisted results and skips gated candidate", async ({ page }) => {
    test.setTimeout(60_000);
    const api = await apiContext();
    const response = await api.post("/api/v1/operator/network-scoring/score", {
      headers: { "idempotency-key": "e2e-r4-006-batch" },
      data: { actorRoleId: "expansionManager", actorName: "王若寧" },
    });
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body.scoredCandidateIds).toEqual(["CS-1001", "CS-1002", "CS-1004"]);
    expect(
      body.skipped.map((item: { candidateId: string }) => item.candidateId),
    ).toEqual(["CS-1003"]);
    expect(body.batchResults.map((row: { id: string }) => row.id)).toEqual([
      "CS-1001",
      "CS-1002",
      "CS-1004",
    ]);
    await api.dispose();

    // The restored UI must submit the selection to the existing durable API,
    // not merely emit a completion toast. Stay in the same business inventory.
    await page.addInitScript(() => sessionStorage.setItem("oday.operator.role", "expansion-manager"));
    await page.goto("/operator?ws=network&tab=sitescore");
    // Wait for persona hydration plus the API-backed write affordance; the
    // initial fixture report can precede the role-keyed workspace remount.
    await expect(page.getByRole("button", { name: "展店經理", exact: true })).toBeVisible();
    await expect(page.getByTestId("sitescore-rescore-CS-1001")).toBeEnabled({ timeout: 15_000 });
    await page.getByRole("button", { name: "批次評分", exact: true }).click();
    await expect(page.getByRole("button", { name: "批次評分", exact: true })).toHaveAttribute("aria-pressed", "true");
    const selection = page.getByRole("button", { name: /信義松仁候選點.*NT\$58,000/ });
    await expect(selection).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("button", { name: /中壢中原候選點.*—/ })).toBeDisabled();
    // Leave only CS-1001 selected.
    await page.getByRole("button", { name: /板橋府中候選點.*NT\$52,000/ }).click();
    await page.getByRole("button", { name: /大安和平候選點.*NT\$64,000/ }).click();
    const batchResponse = page.waitForResponse((response) => response.url().endsWith("/network-scoring/score") && response.request().method() === "POST");
    await page.getByTestId("sitescore-batch-run").click();
    const selectedBatch = await batchResponse;
    expect(selectedBatch.status()).toBe(200);
    expect(selectedBatch.request().postDataJSON()).toMatchObject({ actorRoleId: "expansion-manager", candidateIds: ["CS-1001"] });
    const persistedApi = await apiContext();
    const persistedBatch = await (await persistedApi.get("/api/v1/operator/network-scoring")).json();
    expect(persistedBatch.auditEvents[0]).toMatchObject({
      action: "sitescore.batch", actorRoleId: "expansion-manager",
      metadata: { scored: ["CS-1001"], skipped: [] },
    });
    await expect(page.getByTestId("sitescore-batch-run")).toBeEnabled();

    // Compare updates must also survive a subsequent authoritative GET.
    await page.getByRole("button", { name: "單點評分", exact: true }).click();
    const compareResponse = page.waitForResponse((response) => response.url().endsWith("/network-scoring/compare") && response.request().method() === "POST");
    await page.getByTestId("sitescore-card-CS-1001").getByRole("button", { name: "加入／移出比較" }).click();
    expect((await compareResponse).status()).toBe(200);
    await expect.poll(async () => (await (await persistedApi.get("/api/v1/operator/network-scoring")).json()).compareSet).toEqual(["CS-1002", "CS-1004"]);
    await persistedApi.dispose();
    await page.getByTestId("network-tab-4").click();
    await expect(page.getByRole("button", { name: "移除 信義松仁候選點" })).toHaveCount(0);
    const removeResponse = page.waitForResponse((response) => response.url().endsWith("/network-scoring/compare") && response.request().method() === "POST");
    await page.getByRole("button", { name: "移除 板橋府中候選點" }).click();
    expect((await removeResponse).status()).toBe(200);
    await expect(page.getByRole("button", { name: "移除 板橋府中候選點" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /送審首選/ })).toBeDisabled();
    await expect(page.getByRole("button", { name: /產生比較報告/ })).toBeDisabled();
  });
});

async function apiContext() {
  return playwrightRequest.newContext({
    baseURL: API_BASE_URL,
    extraHTTPHeaders: SCORING_HEADERS,
  });
}
