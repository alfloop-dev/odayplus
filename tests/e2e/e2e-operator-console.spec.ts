import { expect, test, type Locator } from "@playwright/test";

test("ODP-OC-PREVIEW-001 design-preview-only smoke mounts iframe prototype and Store Ops dialog", async ({
  page,
}) => {
  test.info().annotations.push({
    type: "scope",
    description: "design-preview-only; this is not API-backed Operator Console product proof",
  });

  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      browserErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  await page.addInitScript(() => {
    sessionStorage.removeItem("oday-plus-r3");
  });

  await page.goto("/operator");

  await expect(page.locator('[data-screen-label="Top Navigation"]')).toBeVisible();
  await expect(page.locator('[data-screen-label="Today 今日工作"]')).toBeVisible();
  await expect(page.getByText(/林承翰 — 營運主管/)).toBeVisible();
  await expect(page.getByText("今天最需要處理")).toBeVisible();

  await page.getByRole("button", { name: /門市營運/ }).click();
  await expect(page.locator('[data-screen-label="Store Ops 門市營運"]')).toBeVisible();
  await expect(page.locator('[data-screen-label="Store Ops 門市營運"]')).toContainText("ISS-1024");
  await page.getByRole("button", { exact: true, name: "完成 Triage" }).last().click();
  await expect(page.locator('[data-screen-label="Dialog Triage"]')).toBeVisible();
  await page.getByRole("button", { exact: true, name: "取消" }).click();

  await page.getByRole("button", { name: /治理稽核/ }).click();
  await expect(page.locator('[data-screen-label="Govern 治理稽核"]')).toBeVisible();
  await expect(page.locator('[data-screen-label="Govern 治理稽核"]')).toContainText("核准中心");

  expect(browserErrors).toEqual([]);
});

test("ODP-OC-PROD-014 productization gate rejects iframe-only or non-API-backed /operator", async ({
  page,
}) => {
  test.skip(
    process.env.ODP_OPERATOR_PRODUCT_GATE !== "1",
    "Set ODP_OPERATOR_PRODUCT_GATE=1 to run the go/no-go Operator Console productization gate.",
  );

  const operatorApiRequests: OperatorApiRequest[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (!url.pathname.startsWith("/api/v1/operator/")) return;

    operatorApiRequests.push({
      headers: request.headers(),
      method: request.method(),
      pathname: url.pathname,
    });
  });

  await page.goto("/operator");
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(1_000);

  await tryClick(page.getByRole("button", { name: /Store Ops|門市營運/ }));
  await tryClick(page.getByRole("button", { name: /完成 Triage|Triage|triage/i }));
  await page.waitForTimeout(500);

  const failures: string[] = [];
  const designFrameCount = await page.getByTestId("operator-design-frame").count();
  const designArchiveFrameCount = await page.locator('iframe[src*="/operator-design/"]').count();

  if (designFrameCount > 0 || designArchiveFrameCount > 0) {
    failures.push(
      "/operator still renders the design iframe (operator-design-frame or /operator-design/), so it is preview-only.",
    );
  }

  const hasReadProof = operatorApiRequests.some(
    ({ method, pathname }) => method === "GET" && requiredOperatorReadPaths.has(pathname),
  );
  if (!hasReadProof) {
    failures.push(
      "No Operator Console read API proof observed; expected a GET to /api/v1/operator/bootstrap, /today, /issues, or /approvals.",
    );
  }

  const workflowWrites = operatorApiRequests.filter(
    ({ method, pathname }) => method === "POST" && operatorWorkflowWritePathPattern.test(pathname),
  );
  if (workflowWrites.length === 0) {
    failures.push(
      "No API-backed workflow proof observed; expected a POST to an Operator Console workflow endpoint during the gate.",
    );
  }

  const workflowWriteMissingRequiredHeaders = workflowWrites.some(
    ({ headers }) => !headers["idempotency-key"] || !headers["x-correlation-id"],
  );
  if (workflowWriteMissingRequiredHeaders) {
    failures.push("Operator Console workflow writes must include Idempotency-Key and X-Correlation-Id headers.");
  }

  expect(failures).toEqual([]);
});

type OperatorApiRequest = {
  headers: Record<string, string>;
  method: string;
  pathname: string;
};

const requiredOperatorReadPaths = new Set([
  "/api/v1/operator/bootstrap",
  "/api/v1/operator/today",
  "/api/v1/operator/issues",
  "/api/v1/operator/approvals",
]);

const operatorWorkflowWritePathPattern =
  /^\/api\/v1\/operator\/(?:issues\/[^/]+\/(?:triage|assign|actions|field-report|outcome|escalate)|approvals\/[^/]+\/decision|evidence\/[^/]+\/purpose)$/;

async function tryClick(locator: Locator) {
  if ((await locator.count()) === 0) return;
  await locator.first().click();
}
