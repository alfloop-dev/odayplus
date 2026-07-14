import { expect, request as playwrightRequest, test } from "@playwright/test";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
const NETWORK_HEADERS = {
  "x-subject-id": "operator-expansion-manager",
  "x-roles": "expansion_user",
  "x-operator-role": "expansion-manager",
  "x-tenant-id": "tenant-a",
};
const OPS_HEADERS = {
  "x-subject-id": "operator-ops-lead",
  "x-roles": "operations_manager",
  "x-operator-role": "ops-lead",
  "x-tenant-id": "tenant-a",
};

test.describe.configure({ mode: "serial" });

test.describe("ODP-OC-R4-008 Network Rebalance", () => {
  test.beforeEach(async () => {
    const api = await apiContext(NETWORK_HEADERS);
    const reset = await api.post("/api/v1/operator/network-rebalance/reset");
    expect(reset.status()).toBe(200);
    await api.dispose();
  });

  test("AVM + NetPlan workflow persists selected scenario and creates Govern approval without execution", async ({ page }) => {
    await page.goto("/operator?ws=network");
    await expect(page.getByTestId("network-find-areas-workspace")).toBeVisible();

    await page.getByTestId("network-tab-6").click();
    await expect(page.getByTestId("network-panel-rebalance")).toBeVisible();
    await expect(page.getByTestId("rebalance-card-RB-801")).toContainText("新北板橋文化");
    await expect(page.getByTestId("rebalance-primary-action")).toContainText("建立 AVM 估值請求", { timeout: 15_000 });
    await expect(page.getByTestId("rebalance-boundary-RB-801")).toContainText("relocationExecuted=false");

    await page.getByTestId("rebalance-primary-action").click();
    await expect(page.getByTestId("rebalance-primary-action")).toContainText("完成 AVM job");

    await page.getByTestId("rebalance-primary-action").click();
    await expect(page.getByTestId("rebalance-avm-RB-801")).toBeVisible();
    await expect(page.getByTestId("rebalance-avm-RB-801")).toContainText("service output");
    await expect(page.getByTestId("rebalance-avm-RB-801")).toContainText("avm-rebalance-income-market-v1.0.0");
    await expect(page.getByTestId("rebalance-avm-RB-801")).toContainText("AVM-SNAP-20260714-0600");
    await expect(page.getByTestId("rebalance-primary-action")).toContainText("建立 NetPlan Review");

    await page.getByTestId("rebalance-primary-action").click();
    await expect(page.getByTestId("rebalance-netplan-RB-801")).toBeVisible();
    await expect(page.getByTestId("rebalance-scenario-keep")).toContainText("Keep / Improve");
    await expect(page.getByTestId("rebalance-scenario-move")).toContainText("Move (移轉新址)");
    await expect(page.getByTestId("rebalance-scenario-move")).toContainText("系統建議");
    await expect(page.getByTestId("rebalance-scenario-exit")).toContainText("Exit (關店止損)");
    await expect(page.getByTestId("rebalance-scenario-move")).toContainText("NP-SNAP-20260714-0615");

    await page.getByTestId("rebalance-scenario-move").click();
    await expect(page.getByTestId("rebalance-selection-RB-801")).toContainText("Selected: Move (移轉新址)");
    await expect(page.getByTestId("rebalance-selection-RB-801")).toContainText("Owner 王若寧");
    await expect(page.getByTestId("rebalance-selection-RB-801")).toContainText("EV-SEL-");
    await expect(page.getByTestId("rebalance-primary-action")).toContainText("送審");

    await page.getByTestId("rebalance-primary-action").click();
    await expect(page.getByTestId("rebalance-boundary-RB-801")).toContainText("Govern approval APR-NET-RB-801");
    await expect(page.getByTestId("rebalance-boundary-RB-801")).toContainText("relocationExecuted=false");
    await expect(page.getByTestId("rebalance-primary-action")).toContainText("等待 Govern 核准中");

    await page.reload();
    await expect(page.getByTestId("network-find-areas-workspace")).toBeVisible();
    await page.getByTestId("network-tab-6").click();
    await expect(page.getByTestId("rebalance-primary-action")).toContainText("等待 Govern 核准中", { timeout: 15_000 });
    await expect(page.getByTestId("rebalance-selection-RB-801")).toContainText("Owner 王若寧");
    await expect(page.getByTestId("rebalance-selection-RB-801")).toContainText("EV-SEL-");

    const networkApi = await apiContext(NETWORK_HEADERS);
    const snapshot = await networkApi.get("/api/v1/operator/network-rebalance");
    expect(snapshot.status()).toBe(200);
    const body = await snapshot.json();
    const store = body.stores.find((item: { id: string }) => item.id === "RB-801");
    expect(store).toMatchObject({
      status: "pendingapproval",
      selectedScenarioId: "move",
      relatedApprovalId: "APR-NET-RB-801",
      relocationExecuted: false,
    });
    expect(store.selectedScenarioOwner.actorName).toBe("王若寧");
    expect(store.selectedScenarioEvidenceId).toMatch(/^EV-SEL-/);
    await networkApi.dispose();

    const opsApi = await apiContext(OPS_HEADERS);
    const approvals = await opsApi.get("/api/v1/operator/approvals");
    expect(approvals.status()).toBe(200);
    const approvalBody = await approvals.json();
    expect(approvalBody.items.some((item: { id: string }) => item.id === "APR-NET-RB-801")).toBe(true);
    await opsApi.dispose();
  });
});

async function apiContext(headers: Record<string, string>) {
  return playwrightRequest.newContext({
    baseURL: API_BASE_URL,
    extraHTTPHeaders: headers,
  });
}
