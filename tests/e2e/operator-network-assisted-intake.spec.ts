import { expect, request as playwrightRequest, test } from "@playwright/test";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
const NETWORK_HEADERS = {
  "x-subject-id": "operator-expansion-manager",
  "x-roles": "expansion_user",
  "x-operator-role": "expansion-manager",
  "x-tenant-id": "tenant-a",
};

test.describe.configure({ mode: "serial" });

test.describe("Assisted Listing Intake E2E", () => {
  test.beforeEach(async () => {
    const api = await apiContext();
    const reset = await api.post("/api/v1/operator/network-listings/reset");
    expect(reset.status()).toBe(200);
    await api.dispose();
  });

  test("Submit valid synthetic URL via UI", async ({ page }) => {
    await page.goto("/operator?ws=network");
    await page.getByTestId("network-tab-1").click();

    const input = page.getByTestId("intake-url-input");
    await expect(input).toBeVisible();

    const submitBtn = page.getByTestId("intake-submit-button");
    await expect(submitBtn).toBeDisabled();

    // 1. Submit a valid URL
    const url = "https://www.synthetic.example/detail-77120345.html";
    await input.fill(url);
    await expect(submitBtn).toBeEnabled();
    await submitBtn.click();

    const msg = page.getByTestId("intake-status-message");
    await expect(msg).toContainText("成功");
    await expect(msg).toContainText("IN-");

    // 2. Submit a duplicate URL
    await input.fill(url);
    await submitBtn.click();
    await expect(msg).toContainText("成功");
  });
});

async function apiContext() {
  return playwrightRequest.newContext({
    baseURL: API_BASE_URL,
    extraHTTPHeaders: NETWORK_HEADERS,
  });
}
