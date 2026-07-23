import { expect, request as playwrightRequest, test, type Page } from "@playwright/test";
import {
  acquireOperatorBackendLock,
  releaseOperatorBackendLock,
} from "./_operatorBackendLock";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";

const ROLE_MATRIX = [
  {
    id: "expansion-staff",
    label: "展店專員",
    mode: "自有／已指派 · 提案者",
    apiRoles: "expansion_user",
    readOnly: false,
  },
  {
    id: "expansion-manager",
    label: "展店經理",
    mode: "管理範圍 · 獨立審查",
    apiRoles: "expansion_user,site_reviewer",
    readOnly: false,
  },
  {
    id: "data-steward",
    label: "資料管理員",
    mode: "來源／資料範圍 · 校正",
    apiRoles: "data_owner,expansion_user",
    readOnly: false,
  },
  {
    id: "governance-reviewer",
    label: "治理審查員",
    mode: "治理範圍 · 唯讀",
    apiRoles: "auditor",
    readOnly: true,
  },
  {
    id: "privacy-officer",
    label: "隱私主管",
    mode: "目的綁定 · Restricted",
    apiRoles: "finance_legal,auditor",
    readOnly: true,
  },
  {
    id: "permission-limited",
    label: "受限檢視者",
    mode: "FIELD_MASKED · 唯讀",
    apiRoles: "auditor",
    readOnly: true,
  },
] as const;

test.describe.configure({ mode: "serial", timeout: 120_000 });
test.use({ extraHTTPHeaders: {} });

test.beforeAll(async () => {
  await acquireOperatorBackendLock();
});

test.afterAll(() => {
  releaseOperatorBackendLock();
});

test.beforeEach(async () => {
  const api = await playwrightRequest.newContext({
    baseURL: API_BASE_URL,
    extraHTTPHeaders: {
      "x-operator-role": "expansion-manager",
      "x-roles": "expansion_user,site_reviewer",
      "x-subject-id": "role-matrix-reset",
      "x-tenant-id": "tenant-a",
    },
  });
  expect((await api.post("/api/v1/operator/network-listings/reset")).status()).toBe(200);
  await api.dispose();
});

async function setRoleSession(page: Page, roleId: string, subjectId: string) {
  await page.evaluate(
    ({ role, subject }) => {
      window.sessionStorage.setItem("oday.operator.role", role);
      window.sessionStorage.setItem("oday.operator.subject", subject);
    },
    { role: roleId, subject: subjectId },
  );
}

async function selectRole(page: Page, role: (typeof ROLE_MATRIX)[number]) {
  await page.getByRole("button", { name: /展店經理|展店專員|資料管理員|治理審查員|隱私主管|受限檢視者/ }).first().click();
  const menu = page.locator('[data-screen-label="Role Switch Menu"]');
  await expect(menu).toBeVisible();
  await expect(menu.getByTestId(`intake-role-mode-${role.id}`)).toHaveText(role.mode);

  const bootstrapRequest = page.waitForRequest((request) =>
    request.url().includes("/api/v1/operator/bootstrap") &&
    request.headers()["x-operator-role"] === role.id,
  );
  await menu.getByRole("button", { name: new RegExp(role.label) }).click();
  const request = await bootstrapRequest;

  expect(request.headers()["x-roles"]).toBe(role.apiRoles);
  expect(request.headers()["x-subject-id"]).toBe("role-matrix-human");
}

test("all six intake roles are selectable and preserve the current deep link", async ({ page }) => {
  await page.addInitScript(() => {
    window.sessionStorage.setItem("oday.operator.role", "expansion-manager");
    window.sessionStorage.setItem("oday.operator.subject", "role-matrix-human");
  });
  await page.goto(
    "/operator?ws=network&selected=INT-ROLE-001&dialog=detail&section=evidence#role-proof",
  );
  await expect(page.getByRole("button", { name: "展店經理" })).toBeVisible();

  const expectedUrl = new URL(page.url());
  for (const role of ROLE_MATRIX) {
    await selectRole(page, role);
    const currentUrl = new URL(page.url());
    expect(currentUrl.pathname).toBe(expectedUrl.pathname);
    expect(currentUrl.search).toBe(expectedUrl.search);
    expect(currentUrl.hash).toBe(expectedUrl.hash);
  }
});

test("write and read-only variants are distinct in the mounted intake inbox", async ({ page }) => {
  await page.addInitScript(() => {
    window.sessionStorage.setItem("oday.operator.role", "expansion-manager");
    window.sessionStorage.setItem("oday.operator.subject", "role-matrix-human");
  });
  await page.goto("/operator?ws=network");
  await page.getByTestId("network-tab-1").click();
  await expect(page.getByTestId("intake-inbox-view")).toBeVisible({ timeout: 15_000 });

  for (const role of ROLE_MATRIX) {
    await selectRole(page, role);
    await expect(page.getByTestId("intake-inbox-view")).toBeVisible({ timeout: 15_000 });
    if (role.readOnly) {
      await expect(page.getByTestId("intake-read-only")).toContainText("ROLE_DENIED");
      await expect(page.getByTestId("intake-add-button")).toHaveCount(0);
    } else {
      await expect(page.getByTestId("intake-read-only")).toHaveCount(0);
      await expect(page.getByTestId("intake-add-button")).toBeVisible();
    }
  }
});

test("staff sees own records, limited users see masked fields, and denied writes return codes", async ({
  page,
}) => {
  async function submitAs(
    roleId: string,
    apiRoles: string,
    subjectId: string,
    url: string,
  ) {
    const api = await playwrightRequest.newContext({
      baseURL: API_BASE_URL,
      extraHTTPHeaders: {
        "x-operator-role": roleId,
        "x-roles": apiRoles,
        "x-subject-id": subjectId,
        "x-tenant-id": "tenant-a",
        "idempotency-key": `role-matrix-${subjectId}`,
      },
    });
    const response = await api.post("/api/v1/operator/network-listings/intake/submit", {
      data: { url, heatZoneId: "HZ-01", actorRoleId: roleId },
    });
    expect(response.status()).toBe(200);
    const body = await response.json() as { id: string };
    await api.dispose();
    return body;
  }

  const managerRecord = await submitAs(
    "expansion-manager",
    "expansion_user,site_reviewer",
    "manager-owner",
    "https://www.synthetic.example/detail-99310418.html",
  );
  const staffRecord = await submitAs(
    "expansion-staff",
    "expansion_user",
    "staff-owner",
    "https://www.synthetic.example/detail-77120345.html",
  );

  const staffApi = await playwrightRequest.newContext({
    baseURL: API_BASE_URL,
    extraHTTPHeaders: {
      "x-operator-role": "expansion-staff",
      "x-roles": "expansion_user",
      "x-subject-id": "staff-owner",
      "x-tenant-id": "tenant-a",
    },
  });
  const staffList = await staffApi.get("/api/v1/operator/network-listings/intake");
  expect(staffList.status()).toBe(200);
  const staffListBody = await staffList.json() as { items: Array<{ id: string }> };
  expect(staffListBody.items.map(({ id }) => id)).toEqual([staffRecord.id]);
  await staffApi.dispose();

  await page.goto("/operator?ws=network");
  await setRoleSession(page, "permission-limited", "limited-viewer");
  await page.reload();
  await page.getByTestId("network-tab-1").click();
  await expect(page.getByTestId("intake-read-only")).toContainText("ROLE_DENIED");
  await page.getByTestId(`intake-inbox-row-${managerRecord.id}`).click();
  await expect(page.getByTestId("intake-detail-dialog")).toBeVisible();
  await expect(page.getByTestId("intake-masked-contactPhone").first()).toContainText(
    "FIELD_MASKED",
  );

  const governanceApi = await playwrightRequest.newContext({
    baseURL: API_BASE_URL,
    extraHTTPHeaders: {
      "x-operator-role": "governance-reviewer",
      "x-roles": "auditor",
      "x-subject-id": "governance-denied-writer",
      "x-tenant-id": "tenant-a",
      "idempotency-key": "role-matrix-governance-denied",
    },
  });
  const denied = await governanceApi.post("/api/v1/operator/network-listings/intake/submit", {
    data: {
      url: "https://www.synthetic.example/detail-50000001.html",
      actorRoleId: "governance-reviewer",
    },
  });
  expect(denied.status()).toBe(403);
  const deniedBody = await denied.json();
  expect(deniedBody.error.code).toBe("forbidden");
  expect(deniedBody.error.message).toContain("role does not permit");
  await governanceApi.dispose();
});
