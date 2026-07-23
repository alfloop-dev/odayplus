import { mkdir, writeFile } from "node:fs/promises";
import {
  expect,
  request as playwrightRequest,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:18209";
const EVIDENCE =
  "docs/evidence/completion/ODP-INTAKE-FCL-INTEGRATION-001/coverage";
const SCREENSHOTS = `${EVIDENCE}/screenshots`;
const READBACK = `${EVIDENCE}/readback`;
const TENANT_ID = "00000000-0000-0000-0000-000000000001";
const MANAGER = "00000000-0000-4000-8000-000000000202";
const REVIEWER = "00000000-0000-4000-8000-000000000203";
const STEWARD = "00000000-0000-4000-8000-000000000204";

type Role =
  | "expansion-manager"
  | "data-steward"
  | "governance-reviewer"
  | "permission-limited";

type IntakeDetail = {
  intake_id: string;
  state: string;
  version: number;
  original_url?: string | null;
  canonical_url?: string | null;
  policy_state?: string | null;
  match_outcome?: string | null;
  match_case_id?: string | null;
  match_case_version?: number | null;
  existing_listing_id?: string | null;
  processing_history: Array<Record<string, unknown>>;
  lifecycle: {
    assignment?: Record<string, unknown> | null;
    job?: Record<string, unknown> | null;
    promotion?: Record<string, unknown> | null;
    promotion_history: Array<Record<string, unknown>>;
    job_history?: Array<Record<string, unknown>>;
    submission_receipt?: {
      receipt_id: string;
      receipt_type: string;
      intake_id: string;
      existing_listing_id?: string | null;
      navigation_target?: string | null;
      issued_at: string;
    } | null;
  };
  failure?: {
    code?: string | null;
    summary?: string | null;
    retryable?: boolean | null;
  } | null;
};

type ApiResult = {
  status: number;
  headers: Record<string, string>;
  body: Record<string, any>;
};

test.use({
  extraHTTPHeaders: roleHeaders("expansion-manager", MANAGER),
  viewport: { width: 1440, height: 900 },
});

test.beforeAll(async () => {
  await mkdir(SCREENSHOTS, { recursive: true });
  await mkdir(READBACK, { recursive: true });
});

function unique(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function roleHeaders(role: Role, subject: string): Record<string, string> {
  return {
    "x-operator-role": role,
    "x-roles": role,
    "x-subject-id": subject,
    "x-tenant-id": TENANT_ID,
  };
}

async function apiFor(
  role: Role = "expansion-manager",
  subject = MANAGER,
): Promise<APIRequestContext> {
  return playwrightRequest.newContext({
    baseURL: API_BASE_URL,
    extraHTTPHeaders: roleHeaders(role, subject),
  });
}

async function capture(
  page: Page,
  name: string,
  data: unknown,
): Promise<void> {
  await page.screenshot({
    fullPage: true,
    path: `${SCREENSHOTS}/${name}.png`,
  });
  await writeFile(
    `${READBACK}/${name}.json`,
    `${JSON.stringify(data, null, 2)}\n`,
  );
}

async function responseResult(response: {
  status(): number;
  headers(): Record<string, string>;
  json(): Promise<unknown>;
}): Promise<ApiResult> {
  return {
    status: response.status(),
    headers: response.headers(),
    body: (await response.json()) as Record<string, any>,
  };
}

async function createBatch(
  api: APIRequestContext,
  count: number,
  prefix: string,
  method: "MANUAL" | "CSV" | "APPROVED_FEED" = "MANUAL",
): Promise<string[]> {
  const rows = Array.from({ length: count }, (_, index) => ({
    address_raw: `台北市信義區功能路 ${100 + index} 號 1F`,
    area_ping: 18 + index / 10,
    floor: "1F",
    listing_status: "active",
    listing_type: "店面",
    original_url: `https://example.com/${prefix}/${index}?utm_source=coverage`,
    rent_amount: 52000 + index * 100,
    source_id: `${prefix}-source-${index % 2}`,
    source_listing_id: `${prefix}-${index}-${Date.now()}`,
  }));
  const response = await api.post("/api/v1/intake-batches", {
    data: {
      batch_id: crypto.randomUUID(),
      method,
      scope: { tenant_id: TENANT_ID },
      rows,
    },
    headers: {
      "Idempotency-Key": unique("coverage-batch"),
      "X-Correlation-Id": crypto.randomUUID(),
    },
  });
  expect(response.status(), await response.text()).toBe(202);
  const body = (await response.json()) as {
    rows: Array<{ intake_id: string | null; status: string }>;
  };
  expect(body.rows.every((row) => row.status === "ACCEPTED")).toBe(true);
  return body.rows.map((row) => String(row.intake_id));
}

async function submitUrl(
  api: APIRequestContext,
  url: string,
  key = unique("coverage-url"),
): Promise<ApiResult> {
  const response = await api.post("/api/v1/intakes/url", {
    data: {
      original_url: url,
      purpose: "Functional coverage verification",
      scope: { tenant_id: TENANT_ID },
    },
    headers: {
      "Idempotency-Key": key,
      "X-Correlation-Id": crypto.randomUUID(),
    },
  });
  return responseResult(response);
}

async function getDetail(
  api: APIRequestContext,
  intakeId: string,
): Promise<IntakeDetail> {
  const response = await api.get(`/api/v1/intakes/${intakeId}`);
  expect(response.status(), await response.text()).toBe(200);
  return response.json() as Promise<IntakeDetail>;
}

async function pollDetail(
  api: APIRequestContext,
  intakeId: string,
  predicate: (detail: IntakeDetail) => boolean,
  timeout = 90_000,
): Promise<IntakeDetail> {
  const deadline = Date.now() + timeout;
  let detail = await getDetail(api, intakeId);
  while (!predicate(detail) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    detail = await getDetail(api, intakeId);
  }
  expect(predicate(detail), JSON.stringify(detail, null, 2)).toBe(true);
  return detail;
}

async function openInbox(page: Page, query = ""): Promise<void> {
  await page.goto(`/w/expansion/listings${query ? `?${query}` : ""}`);
  await expect(page.getByTestId("intake-inbox-view")).toBeVisible();
  await expect(
    page
      .getByTestId("intake-table")
      .or(page.getByTestId("intake-inbox-empty"))
      .or(page.getByTestId("intake-inbox-error")),
  ).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("intake-inbox-loading")).toHaveCount(0);
}

async function proposeAndReviewIdentity(
  proposerApi: APIRequestContext,
  reviewerApi: APIRequestContext,
  path: string,
  body: Record<string, unknown>,
  prefix: string,
): Promise<Record<string, any>> {
  const proposedResponse = await proposerApi.post(path, {
    data: body,
    headers: {
      "Idempotency-Key": unique(`${prefix}-propose`),
      "If-Match": 'W/"1"',
      "X-Correlation-Id": crypto.randomUUID(),
    },
  });
  expect(proposedResponse.status(), await proposedResponse.text()).toBe(202);
  const proposed = (await proposedResponse.json()) as Record<string, any>;
  expect(proposed.status).toBe("PENDING_REVIEW");

  const selfReview = await proposerApi.post(
    `/api/v1/identity-decisions/${proposed.decision_id}/actions/review`,
    {
      data: {
        decision: "APPROVE",
        reason: "Same actor must not approve this graph operation.",
        risk_acknowledged: true,
      },
      headers: {
        "Idempotency-Key": unique(`${prefix}-self`),
        "If-Match": `W/"${proposed.version}"`,
      },
    },
  );
  expect(selfReview.status(), await selfReview.text()).toBe(403);
  expect(await selfReview.text()).toContain("SELF_REVIEW_DENIED");

  const reviewedResponse = await reviewerApi.post(
    `/api/v1/identity-decisions/${proposed.decision_id}/actions/review`,
    {
      data: {
        decision: "APPROVE",
        reason: "Independent reviewer approves the persisted graph plan.",
        risk_acknowledged: true,
      },
      headers: {
        "Idempotency-Key": unique(`${prefix}-review`),
        "If-Match": `W/"${proposed.version}"`,
      },
    },
  );
  expect(reviewedResponse.status(), await reviewedResponse.text()).toBe(200);
  const reviewed = (await reviewedResponse.json()) as Record<string, any>;
  expect(reviewed.status).toBe("EXECUTED");
  return reviewed;
}

async function createListingForUrl(
  proposerApi: APIRequestContext,
  reviewerApi: APIRequestContext,
  url: string,
): Promise<{ intakeId: string; listingId: string }> {
  const batchResponse = await proposerApi.post("/api/v1/intake-batches", {
    data: {
      batch_id: crypto.randomUUID(),
      method: "MANUAL",
      scope: { tenant_id: TENANT_ID },
      rows: [
        {
          address_raw: "台北市中山區完整路 101 號 1F",
          area_ping: 21,
          floor: "1F",
          listing_status: "active",
          listing_type: "店面",
          original_url: url,
          rent_amount: 63000,
          source_id: "coverage-exact-source",
          source_listing_id: unique("coverage-exact"),
        },
      ],
    },
    headers: {
      "Idempotency-Key": unique("exact-source-batch"),
      "X-Correlation-Id": crypto.randomUUID(),
    },
  });
  expect(batchResponse.status(), await batchResponse.text()).toBe(202);
  const batch = (await batchResponse.json()) as {
    rows: Array<{ intake_id: string }>;
  };
  const intakeId = batch.rows[0].intake_id;
  const detail = await getDetail(proposerApi, intakeId);
  const proposedResponse = await proposerApi.post(
    `/api/v1/match-cases/${detail.match_case_id}/decisions`,
    {
      data: {
        decision_type: "CREATE",
        reason: "Create the authoritative Listing used by exact URL coverage.",
        risk_acknowledged: true,
      },
      headers: {
        "Idempotency-Key": unique("exact-create"),
        "If-Match": `W/"${detail.match_case_version}"`,
      },
    },
  );
  expect(proposedResponse.status(), await proposedResponse.text()).toBe(201);
  const proposed = (await proposedResponse.json()) as Record<string, any>;
  const reviewedResponse = await reviewerApi.post(
    `/api/v1/identity-decisions/${proposed.decision_id}/actions/review`,
    {
      data: {
        decision: "APPROVE",
        reason: "Independent reviewer approves the Listing creation.",
        risk_acknowledged: true,
      },
      headers: {
        "Idempotency-Key": unique("exact-create-review"),
        "If-Match": `W/"${proposed.version}"`,
      },
    },
  );
  expect(reviewedResponse.status(), await reviewedResponse.text()).toBe(200);
  const reviewed = (await reviewedResponse.json()) as Record<string, any>;
  expect(reviewed.status).toBe("EXECUTED");
  const listingId =
    reviewed.effect_receipt?.runtime_receipt?.listing_id ??
    reviewed.resource_versions?.listing_id;
  expect(listingId).toBeTruthy();
  return { intakeId, listingId: String(listingId) };
}

test("Inbox list/map, every server filter family, cursor history, saved view, selection and direct workflow links use authoritative data", async ({
  page,
}) => {
  test.info().annotations.push({
    type: "FTR",
    description: "FTR-045..FTR-051",
  });
  const api = await apiFor();
  const reviewerApi = await apiFor("expansion-manager", REVIEWER);
  await createBatch(api, 13, unique("inbox"));
  await createListingForUrl(
    api,
    reviewerApi,
    `https://example.com/${unique("possible-base")}`,
  );
  const possibleBatchResponse = await api.post("/api/v1/intake-batches", {
    data: {
      batch_id: crypto.randomUUID(),
      method: "MANUAL",
      scope: { tenant_id: TENANT_ID },
      rows: [
        {
          address_raw: "台北市中山區完整路 101 號 1F",
          area_ping: 21,
          floor: "1F",
          listing_status: "active",
          listing_type: "店面",
          original_url: `https://example.com/${unique("possible-submission")}`,
          rent_amount: 63000,
          source_id: "coverage-independent-source",
          source_listing_id: unique("coverage-possible"),
        },
      ],
    },
    headers: {
      "Idempotency-Key": unique("possible-batch"),
      "X-Correlation-Id": crypto.randomUUID(),
    },
  });
  expect(
    possibleBatchResponse.status(),
    await possibleBatchResponse.text(),
  ).toBe(202);
  const possibleId = String(
    ((await possibleBatchResponse.json()) as {
      rows: Array<{ intake_id: string }>;
    }).rows[0].intake_id,
  );
  const possibleDetail = await getDetail(api, possibleId);
  expect(possibleDetail.state).toBe("NEEDS_REVIEW");
  expect(possibleDetail.match_outcome).toBe("POSSIBLE_MATCH");

  const assistedSubmission = await submitUrl(
    api,
    `https://www.591.com.tw/rent-detail-${Date.now()}.html`,
  );
  expect([200, 202]).toContain(assistedSubmission.status);
  const assistedId = String(assistedSubmission.body.intake_id);
  await pollDetail(
    api,
    assistedId,
    (current) => current.state === "AWAITING_ASSISTED_ENTRY",
  );
  const timeoutSubmission = await submitUrl(
    api,
    "https://www.synthetic.example/detail-50000001.html",
  );
  expect([200, 202]).toContain(timeoutSubmission.status);
  const timeoutId = String(timeoutSubmission.body.intake_id);
  await pollDetail(api, timeoutId, (current) => current.state === "FAILED");

  const uiPages: Array<Record<string, any>> = [];
  let latestUiPage: Record<string, any> | null = null;
  let inboxRequestCount = 0;
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (
      response.request().method() === "GET" &&
      url.pathname === "/api/v1/intakes"
    ) {
      inboxRequestCount += 1;
      void response
        .json()
        .then((body) => {
          latestUiPage = body as Record<string, any>;
          if (uiPages.length < 20) uiPages.push(latestUiPage);
        })
        .catch(() => undefined);
    }
  });
  const firstPage = await api.get(
    "/api/v1/intakes?page_size=10&sort=updated_at_desc",
  );
  expect(firstPage.status(), await firstPage.text()).toBe(200);
  const authoritativePage = (await firstPage.json()) as {
    items: Array<{ intake_id: string }>;
    next_cursor: string | null;
    total_count: number;
  };
  expect(authoritativePage.total_count).toBeGreaterThanOrEqual(13);
  expect(authoritativePage.next_cursor).toBeTruthy();
  expect(authoritativePage.next_cursor).not.toMatch(/^[0-9]+$/);

  const filterQueries: Record<string, string> = {
    intake_method: "CSV",
    status: "CANCELLED",
    match_outcome: "EXACT_DUPLICATE",
    source_id: unique("missing-source"),
    submitted_by: "00000000-0000-4000-8000-000000000901",
    owner_subject_id: "00000000-0000-4000-8000-000000000902",
    assignment_status: "COMPLETED",
    needs_review: "true",
    sla_state: "COMPLETED",
    heat_zone_id: "00000000-0000-4000-8000-000000000903",
    assigned_area_id: "00000000-0000-4000-8000-000000000904",
    observed_from: new Date(Date.now() + 86_400_000).toISOString(),
    observed_to: new Date(0).toISOString(),
    updated_from: new Date(Date.now() + 86_400_000).toISOString(),
    updated_to: new Date(0).toISOString(),
    restricted_data: "true",
    quarantined: "true",
    failed: "true",
    retryable: "true",
    q: unique("no-result"),
  };
  const filterEvidence: Record<string, number> = {};
  for (const [parameter, value] of Object.entries(filterQueries)) {
    const response = await api.get(
      `/api/v1/intakes?page_size=10&${parameter}=${encodeURIComponent(value)}`,
    );
    expect(response.status(), await response.text()).toBe(200);
    const body = (await response.json()) as { total_count: number };
    filterEvidence[parameter] = body.total_count;
    expect(
      body.total_count,
      `${parameter} must be applied by the server and change the result set`,
    ).toBeLessThan(authoritativePage.total_count);
  }

  const secondPageResponse = await api.get(
    `/api/v1/intakes?page_size=10&sort=updated_at_desc&cursor=${encodeURIComponent(
      authoritativePage.next_cursor!,
    )}`,
  );
  expect(secondPageResponse.status(), await secondPageResponse.text()).toBe(200);
  const secondPage = (await secondPageResponse.json()) as {
    items: Array<{ intake_id: string }>;
  };
  expect(secondPage.items.length).toBeGreaterThan(0);
  expect(
    secondPage.items.some((item) =>
      authoritativePage.items.some(
        (first) => first.intake_id === item.intake_id,
      ),
    ),
  ).toBe(false);

  const savedViewResponse = await api.post("/api/v1/saved-views", {
    data: {
      name: unique("待覆核檢視"),
      query: { status: ["READY"] },
      resource: "intake",
      visibility: "PRIVATE",
    },
    headers: { "Idempotency-Key": unique("saved-view") },
  });
  expect(savedViewResponse.status(), await savedViewResponse.text()).toBe(201);
  const savedView = (await savedViewResponse.json()) as {
    saved_view_id: string;
  };
  const savedViewId = savedView.saved_view_id;
  const savedViews = await api.get("/api/v1/saved-views");
  expect(savedViews.status(), await savedViews.text()).toBe(200);
  expect(
    ((await savedViews.json()) as Array<{ saved_view_id: string }>).map(
      (view) => view.saved_view_id,
    ),
  ).toContain(savedViewId);
  const savedSelection = await api.get(
    `/api/v1/intakes?saved_view_id=${savedViewId}`,
  );
  expect(savedSelection.status(), await savedSelection.text()).toBe(200);
  expect(
    ((await savedSelection.json()) as { items: Array<{ state: string }> }).items
      .every((item) => item.state === "READY"),
  ).toBe(true);

  await page.goto("/w/expansion/listings");
  await expect(page.getByTestId("intake-inbox-view")).toBeVisible();
  const rows = page.locator('[data-testid^="intake-inbox-row-"]');
  await expect(rows.first()).toBeVisible({ timeout: 20_000 });
  const firstPageIds = await rows.evaluateAll((elements) =>
    elements.map((element) =>
      element.getAttribute("data-testid")!.replace("intake-inbox-row-", ""),
    ),
  );
  const uiReceivedItemCount =
    (latestUiPage as { items?: unknown[] } | null)?.items?.length ?? 0;
  if (firstPageIds.length === 0 && uiReceivedItemCount > 0) {
    await writeFile(
      `${READBACK}/inbox-ui-pages.json`,
      `${JSON.stringify(uiPages, null, 2)}\n`,
    );
    await capture(page, "inbox-authoritative-coverage", {
      filterEvidence,
      firstPage: authoritativePage,
      inboxRequestCount,
      renderedRows: 0,
      savedViewId,
      secondPage,
      uiReceivedItemCount,
    });
    await api.dispose();
    await reviewerApi.dispose();
    expect(
      firstPageIds,
      `FTR-045..051 gap: UI received ${uiReceivedItemCount} authoritative rows but rendered none after ${inboxRequestCount} GET responses`,
    ).toHaveLength(uiReceivedItemCount);
    return;
  }
  expect(firstPageIds).toHaveLength(uiReceivedItemCount);

  await page.getByTestId("intake-view-mode-map").click();
  await expect(page.getByTestId("intake-map-view-panel")).toBeVisible();
  const mapIds = await page
    .locator(
      '[data-testid^="intake-map-marker-"], [data-testid="intake-unlocated-list"] a',
    )
    .evaluateAll((elements) =>
      elements.map((element) => {
        const testId = element.getAttribute("data-testid");
        if (testId?.startsWith("intake-map-marker-")) {
          return testId.replace("intake-map-marker-", "");
        }
        return element.getAttribute("href")!.split("/").at(-1)!;
      }),
    );
  expect([...mapIds].sort()).toEqual([...firstPageIds].sort());
  await page.getByTestId("intake-view-mode-list").click();
  await expect(rows.first()).toBeVisible();

  let createdViewId = "";
  const resetFirstPageIds = await rows.evaluateAll((elements) =>
    elements.map((element) =>
      element.getAttribute("data-testid")!.replace("intake-inbox-row-", ""),
    ),
  );
  await page.getByTestId("intake-next-page").click();
  await expect(page).toHaveURL(/page=2/);
  const nextCursor = new URL(page.url()).searchParams.get("cursor");
  expect(nextCursor).toBeTruthy();
  expect(nextCursor).not.toMatch(/^[0-9]+$/);
  await expect
    .poll(async () =>
      rows.evaluateAll((elements) =>
        elements.map((element) =>
          element.getAttribute("data-testid")!.replace("intake-inbox-row-", ""),
        ),
      ),
    )
    .not.toEqual(resetFirstPageIds);
  const secondPageIds = await rows.evaluateAll((elements) =>
    elements.map((element) =>
      element.getAttribute("data-testid")!.replace("intake-inbox-row-", ""),
    ),
  );
  await page.getByTestId("intake-prev-page").click();
  await expect(page).not.toHaveURL(/page=2/);
  await expect
    .poll(async () =>
      rows.evaluateAll((elements) =>
        elements.map((element) =>
          element.getAttribute("data-testid")!.replace("intake-inbox-row-", ""),
        ),
      ),
    )
    .toEqual(resetFirstPageIds);
  await page.goBack();
  await expect(page).toHaveURL(/page=2/);
  await expect
    .poll(async () =>
      rows.evaluateAll((elements) =>
        elements.map((element) =>
          element.getAttribute("data-testid")!.replace("intake-inbox-row-", ""),
        ),
      ),
    )
    .toEqual(secondPageIds);
  await page.goForward();
  await expect(page).not.toHaveURL(/page=2/);
  await expect
    .poll(async () =>
      rows.evaluateAll((elements) =>
        elements.map((element) =>
          element.getAttribute("data-testid")!.replace("intake-inbox-row-", ""),
        ),
      ),
    )
    .toEqual(resetFirstPageIds);

  const firstRow = rows.first();
  const selectedId = (await firstRow.getAttribute("data-testid"))!.replace(
    "intake-inbox-row-",
    "",
  );
  await firstRow.getByRole("radio").check();
  await expect(page).toHaveURL(
    new RegExp(`selected=${encodeURIComponent(selectedId)}`),
  );
  await expect(page.getByTestId(`intake-open-${selectedId}`)).toHaveAttribute(
    "href",
    `/w/expansion/listings/intake/${selectedId}`,
  );

  await expect(page.getByTestId(`intake-review-${possibleId}`)).toHaveAttribute(
    "href",
    `/w/expansion/listings/intake/${possibleId}?section=identity&compare=true`,
  );
  await expect(
    page.getByTestId(`intake-correction-${assistedId}`),
  ).toHaveAttribute(
    "href",
    `/w/expansion/listings/intake/${assistedId}?section=fields&action=correction`,
  );

  const claimButton = page
    .locator(
      `[data-testid^="intake-claim-"]:not([data-testid="intake-claim-${timeoutId}"])`,
    )
    .first();
  const claimId = (await claimButton.getAttribute("data-testid"))!.replace(
    "intake-claim-",
    "",
  );
  await claimButton.click();
  await expect(page.getByTestId("intake-claim-receipt")).toBeVisible();
  const claimed = await getDetail(api, claimId);
  expect(claimed.lifecycle.assignment?.owner_subject_id).toBe(MANAGER);

  const retryButton = page.getByTestId(`intake-retry-${timeoutId}`);
  const retryCount = await retryButton.count();
  expect(
    retryCount,
    "A retry-exhausted failure must not expose an unsafe direct retry command.",
  ).toBe(0);
  const replayLink = page.getByTestId(`intake-replay-${timeoutId}`);
  await expect(replayLink).toHaveAttribute(
    "href",
    `/w/expansion/listings/intake/${timeoutId}?section=timeline&action=replay`,
  );
  const replayHref = await replayLink.getAttribute("href");

  const createdViewName = unique("UI持久檢視");
  await page.locator("#intake-saved-view-name").fill(createdViewName);
  await page.getByTestId("intake-create-saved-view-submit").click();
  const viewReceipt = page.getByTestId("intake-create-saved-view-receipt");
  await expect(viewReceipt).toBeVisible();
  createdViewId = (await viewReceipt.locator("code").textContent())?.trim() ?? "";
  expect(createdViewId).toBeTruthy();
  await expect(page).toHaveURL(
    new RegExp(`savedView=${encodeURIComponent(createdViewId)}`),
  );
  await page.reload({ timeout: 20_000, waitUntil: "domcontentloaded" });
  await expect(page.getByTestId(`intake-tab-${createdViewId}`)).toHaveAttribute(
    "aria-current",
    "page",
  );
  const persistedViewsResponse = await api.get("/api/v1/saved-views");
  expect(
    ((await persistedViewsResponse.json()) as Array<{ saved_view_id: string }>).map(
      (view) => view.saved_view_id,
    ),
  ).toContain(createdViewId);

  await writeFile(
    `${READBACK}/inbox-ui-pages.json`,
    `${JSON.stringify(uiPages, null, 2)}\n`,
  );
  await capture(page, "inbox-authoritative-coverage", {
    directActionsExpected: [
      "open",
      "claim",
      "review",
      "replay-workflow",
      "correction",
    ],
    createdViewId,
    filterEvidence,
    firstPage: authoritativePage,
    firstPageIds,
    inboxRequestCount,
    mapIds,
    renderedRows: firstPageIds.length,
    replayHref,
    unsafeDirectRetryCount: retryCount,
    savedViewId,
    secondPage,
    secondPageIds,
    uiReceivedItemCount,
  });
  await api.dispose();
  await reviewerApi.dispose();
});

test("Inbox loading, transport error, read-only and no-results states are durable UI states", async ({
  browser,
  page,
}) => {
  test.info().annotations.push({
    type: "FTR",
    description: "FTR-052 FTR-053",
  });
  let delayed = false;
  await page.route(
    "**/api/v1/intakes?**",
    async (route) => {
      if (!delayed) {
        delayed = true;
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
      await route.continue().catch(() => undefined);
    },
    { times: 1 },
  );
  const navigation = page.goto("/w/expansion/listings");
  await expect(page.getByTestId("intake-inbox-loading")).toBeVisible();
  await navigation;
  await expect(page.getByTestId("intake-inbox-view")).toBeVisible();
  await page.unroute("**/api/v1/intakes?**");

  await page.route("**/api/v1/intakes?**", (route) => route.abort("failed"));
  await page.reload();
  await expect(page.getByTestId("intake-inbox-error")).toBeVisible();
  await expect(
    page.getByTestId("intake-inbox-error-code"),
  ).not.toContainText("API 未回傳");
  await page.unroute("**/api/v1/intakes?**");

  await openInbox(page, `search=${encodeURIComponent(unique("no-result"))}`);
  await expect(page.getByTestId("intake-inbox-empty")).toBeVisible();

  const readOnlyContext = await browser.newContext({
    baseURL: process.env.ODP_WEB_BASE_URL ?? "http://127.0.0.1:13209",
    extraHTTPHeaders: roleHeaders(
      "governance-reviewer",
      "00000000-0000-4000-8000-000000000205",
    ),
    viewport: { width: 1440, height: 900 },
  });
  const readOnlyPage = await readOnlyContext.newPage();
  await openInbox(readOnlyPage);
  await expect(readOnlyPage.getByTestId("intake-read-only")).toBeVisible();
  await expect(readOnlyPage.getByTestId("intake-add-button")).toBeDisabled();
  await capture(readOnlyPage, "inbox-read-only-no-results-error", {
    noResultsUrl: page.url(),
    readOnlyUrl: readOnlyPage.url(),
  });
  await readOnlyContext.close();
});

test("Add URL covers validation, canonical difference, unsupported source, request lock and durable receipt", async ({
  page,
}) => {
  test.info().annotations.push({
    type: "FTR",
    description: "FTR-054..FTR-060",
  });
  const api = await apiFor();
  const reviewerApi = await apiFor("expansion-manager", REVIEWER);
  await page.goto("/w/expansion/listings");
  await expect(page.getByTestId("intake-inbox-view")).toBeVisible();
  await page.getByTestId("intake-add-button").click();
  await expect(page.getByTestId("intake-add-dialog")).toBeVisible();

  await page.getByTestId("intake-url-input").fill("not-a-url");
  await page.getByTestId("intake-submit-button").click();
  await expect(page.getByTestId("intake-add-error")).toContainText(
    "http(s)://",
  );

  const originalUrl = `HTTPS://EXAMPLE.COM/listing/${unique(
    "canonical",
  )}?utm_source=coverage&utm_campaign=test#detail`;
  await page.getByTestId("intake-url-input").fill(originalUrl);
  await expect(page.getByTestId("intake-canonical-preview")).toBeVisible();
  const requests: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      request.url().endsWith("/api/v1/intakes/url")
    ) {
      requests.push(request.url());
    }
  });
  const submitButton = page.getByTestId("intake-submit-button");
  await submitButton.dblclick();
  await expect(page.getByTestId("intake-inbox-submission-receipt")).toBeVisible({
    timeout: 30_000,
  });
  expect(requests).toHaveLength(1);
  const receiptLink = page.getByTestId("intake-receipt-primary-link");
  const target = await receiptLink.getAttribute("href");
  expect(target).toMatch(/^\/w\/expansion\/listings\/intake\//);
  const intakeId = target!.split("/").at(-1)!;
  const detail = await getDetail(api, intakeId);
  expect(detail.original_url).toBe(originalUrl);
  expect(detail.canonical_url).not.toBe(originalUrl);
  await receiptLink.click();
  await expect(page.getByTestId("intake-processing-page")).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("intake-detail-id")).toHaveText(intakeId);

  await page.goto("/w/expansion/listings");
  await expect(page.getByTestId("intake-inbox-view")).toBeVisible();
  await page.getByTestId("intake-add-button").click();
  const unsupportedUrl = `https://${unique(
    "unsupported",
  )}.invalid/listing/1`;
  await page.getByTestId("intake-url-input").fill(unsupportedUrl);
  await page.getByTestId("intake-submit-button").click();
  await expect(page.getByTestId("intake-inbox-submission-receipt")).toBeVisible({
    timeout: 30_000,
  });
  const unsupportedTarget = await page
    .getByTestId("intake-receipt-primary-link")
    .getAttribute("href");
  const unsupportedId = unsupportedTarget!.split("/").at(-1)!;
  const unsupported = await pollDetail(
    api,
    unsupportedId,
    (current) =>
      current.state === "QUARANTINED" &&
      current.policy_state === "POLICY_UNKNOWN",
  );

  const exactUrl = `https://example.com/${unique("exact-duplicate")}`;
  const exactSource = await createListingForUrl(
    api,
    reviewerApi,
    exactUrl,
  );
  const exactListingId = exactSource.listingId;
  const exactBeforeDuplicate = await getDetail(api, exactSource.intakeId);
  const retrievalCountBefore = exactBeforeDuplicate.processing_history.filter(
    (entry) => entry.to_state === "RETRIEVING",
  ).length;
  await page.goto("/w/expansion/listings");
  await expect(page.getByTestId("intake-inbox-view")).toBeVisible();
  await page.getByTestId("intake-add-button").click();
  await page.getByTestId("intake-url-input").fill(exactUrl);
  await page.getByTestId("intake-submit-button").click();
  await expect(page.getByTestId("intake-inbox-submission-receipt")).toBeVisible({
    timeout: 30_000,
  });
  const exactReceiptLink = page.getByTestId("intake-receipt-primary-link");
  const exactTarget = await exactReceiptLink.getAttribute("href");
  const exactDetail = await pollDetail(
    api,
    exactSource.intakeId,
    (current) =>
      current.lifecycle.submission_receipt?.receipt_type ===
      "EXACT_SOURCE_IDENTITY",
  );
  const retrievalCountAfter = exactDetail.processing_history.filter(
    (entry) => entry.to_state === "RETRIEVING",
  ).length;
  await capture(page, "add-url-matrix", {
    canonical: detail,
    exactBeforeDuplicate,
    exactDetail,
    exactListingId,
    exactTarget,
    retrievalCountAfter,
    retrievalCountBefore,
    requestCount: requests.length,
    unsupported,
  });
  expect(exactDetail.match_outcome).toBe("NEW");
  expect(exactDetail.lifecycle.submission_receipt).toMatchObject({
    receipt_type: "EXACT_SOURCE_IDENTITY",
    intake_id: exactSource.intakeId,
    existing_listing_id: exactListingId,
    navigation_target: `/w/expansion/listings/${exactListingId}`,
  });
  expect(retrievalCountAfter).toBe(retrievalCountBefore);
  expect.soft(
    exactTarget,
    "The exact-duplicate receipt must open the existing Listing.",
  ).toBe(`/w/expansion/listings/${exactListingId}`);
  await api.dispose();
  await reviewerApi.dispose();
});

test("Identity merge, split, unmerge and reversal persist two-actor graph plans, redirects and superseded lineage", async ({
  page,
}) => {
  test.info().annotations.push({
    type: "FTR",
    description: "FTR-084 FTR-085 FTR-117",
  });
  const proposerApi = await apiFor("data-steward", STEWARD);
  const reviewerApi = await apiFor("expansion-manager", REVIEWER);
  const sourceA = crypto.randomUUID();
  const sourceB = crypto.randomUUID();
  const target = crypto.randomUUID();

  const merged = await proposeAndReviewIdentity(
    proposerApi,
    reviewerApi,
    "/api/v1/identity/merge",
    {
      source_property_ids: [sourceA, sourceB],
      target_property_id: target,
      reason: "Merge two independently verified duplicate property identities.",
      risk_acknowledged: true,
    },
    "merge",
  );
  expect(merged.effect_receipt.identity_edge_ids.length).toBeGreaterThanOrEqual(
    2,
  );
  const mergeEdgesResponse = await reviewerApi.get(
    "/api/v1/identity/edges?include_superseded=true",
  );
  expect(mergeEdgesResponse.status(), await mergeEdgesResponse.text()).toBe(200);
  const mergeEdges = (await mergeEdgesResponse.json()) as {
    edges: Array<Record<string, any>>;
  };
  const effectiveMergeEdges = mergeEdges.edges.filter(
    (edge) =>
      merged.effect_receipt.identity_edge_ids.includes(
        edge.edge_id ?? edge.edgeId,
      ) && (edge.effective ?? edge.status === "EFFECTIVE"),
  );
  expect(effectiveMergeEdges.length).toBeGreaterThanOrEqual(2);
  expect(
    effectiveMergeEdges.every(
      (edge) =>
        (edge.target_property_id ?? edge.targetPropertyId) === target,
    ),
  ).toBe(true);

  const reversalResponse = await proposerApi.post(
    `/api/v1/identity-decisions/${merged.decision_id}/actions/reverse`,
    {
      data: {
        reason: "New authoritative evidence requires reversing the merge.",
        risk_acknowledged: true,
      },
      headers: {
        "Idempotency-Key": unique("merge-reversal"),
        "If-Match": `W/"${merged.version}"`,
      },
    },
  );
  expect(reversalResponse.status(), await reversalResponse.text()).toBe(202);
  const reversal = (await reversalResponse.json()) as Record<string, any>;
  expect(reversal.status).toBe("REVERSAL_PENDING");
  const reversedResponse = await reviewerApi.post(
    `/api/v1/identity-decisions/${reversal.decision_id}/actions/review`,
    {
      data: {
        decision: "APPROVE",
        reason: "Independent reviewer approves reversal and lineage retention.",
        risk_acknowledged: true,
      },
      headers: {
        "Idempotency-Key": unique("merge-reversal-review"),
        "If-Match": `W/"${reversal.version}"`,
      },
    },
  );
  expect(reversedResponse.status(), await reversedResponse.text()).toBe(200);
  const reversed = (await reversedResponse.json()) as Record<string, any>;
  expect(reversed.status).toBe("EXECUTED");
  const originalAfterResponse = await reviewerApi.get(
    `/api/v1/identity-decisions/${merged.decision_id}`,
  );
  const originalAfter = (await originalAfterResponse.json()) as Record<
    string,
    any
  >;
  expect(originalAfter.status).toBe("REVERSED");

  const afterReversalResponse = await reviewerApi.get(
    "/api/v1/identity/edges?include_superseded=true",
  );
  const afterReversal = (await afterReversalResponse.json()) as {
    edges: Array<Record<string, any>>;
  };
  expect(
    afterReversal.edges.some(
      (edge) =>
        (edge.relation ?? edge.relation_type) === "REVERSAL_OF" ||
        (edge.supersedes_edge_id ?? edge.supersedesEdgeId),
    ),
  ).toBe(true);

  const splitTargetA = crypto.randomUUID();
  const splitTargetB = crypto.randomUUID();
  const split = await proposeAndReviewIdentity(
    proposerApi,
    reviewerApi,
    "/api/v1/identity/split",
    {
      source_property_id: target,
      source_property_version: 1,
      partitions: [
        {
          target_property_id: splitTargetA,
          source_identity_edge_ids: [
            merged.effect_receipt.identity_edge_ids[0],
          ],
        },
        {
          target_property_id: splitTargetB,
          source_identity_edge_ids: [
            merged.effect_receipt.identity_edge_ids[1],
          ],
        },
      ],
      reason: "Split source edges into two separately verified properties.",
      risk_acknowledged: true,
    },
    "split",
  );
  expect(
    split.effect_receipt.identity_edge_ids.length,
  ).toBeGreaterThanOrEqual(2);

  const unmerge = await proposeAndReviewIdentity(
    proposerApi,
    reviewerApi,
    "/api/v1/identity/unmerge",
    {
      original_decision_id: merged.decision_id,
      replacement_edges: [
        {
          target_property_id: sourceA,
          source_identity_edge_ids: [
            merged.effect_receipt.identity_edge_ids[0],
          ],
        },
      ],
      reason: "Restore the first source identity with explicit replacement lineage.",
      risk_acknowledged: true,
    },
    "unmerge",
  );
  expect(unmerge.effect_receipt.identity_edge_ids.length).toBeGreaterThan(0);

  await page.goto(
    `/w/expansion/listings?selected=${encodeURIComponent(
      String(merged.decision_id),
    )}`,
  );
  await capture(page, "identity-graph-readback", {
    afterReversal,
    mergeEdges,
    merged,
    originalAfter,
    reversed,
    split,
    unmerge,
  });
  await proposerApi.dispose();
  await reviewerApi.dispose();
});

test("Promotion SCORE_FAILED retains Candidate and same-key replay recovers a lost response without duplication", async ({
  page,
}) => {
  test.info().annotations.push({
    type: "FTR",
    description: "FTR-044 FTR-101..FTR-106",
  });
  const proposerApi = await apiFor("expansion-manager", MANAGER);
  const reviewerApi = await apiFor("expansion-manager", REVIEWER);
  const [intakeId] = await createBatch(
    proposerApi,
    1,
    unique("promotion-failure"),
  );
  const detail = await getDetail(proposerApi, intakeId);
  expect(detail.state).toBe("READY");
  expect(detail.match_outcome).toBe("NEW");

  const decisionResponse = await proposerApi.post(
    `/api/v1/match-cases/${detail.match_case_id}/decisions`,
    {
      data: {
        decision_type: "CREATE",
        reason: "Create Listing before explicit Candidate promotion.",
        risk_acknowledged: true,
      },
      headers: {
        "Idempotency-Key": unique("promotion-listing"),
        "If-Match": `W/"${detail.match_case_version}"`,
      },
    },
  );
  expect(decisionResponse.status(), await decisionResponse.text()).toBe(201);
  const listingDecision = (await decisionResponse.json()) as Record<string, any>;
  const listingReview = await reviewerApi.post(
    `/api/v1/identity-decisions/${listingDecision.decision_id}/actions/review`,
    {
      data: {
        decision: "APPROVE",
        reason: "Independent review approves Listing creation.",
        risk_acknowledged: true,
      },
      headers: {
        "Idempotency-Key": unique("promotion-listing-review"),
        "If-Match": `W/"${listingDecision.version}"`,
      },
    },
  );
  expect(listingReview.status(), await listingReview.text()).toBe(200);

  const promotedDetail = await getDetail(proposerApi, intakeId);
  const promotionRequest = await proposerApi.post(
    `/api/v1/intakes/${intakeId}/promotion-requests`,
    {
      data: {
        target_format_code: "STANDARD",
        reason: "Request Candidate creation after verified Listing commit.",
        risk_acknowledged: true,
        gate_snapshot_sha256: "0".repeat(64),
      },
      headers: {
        "Idempotency-Key": unique("promotion-request"),
        "If-Match": `W/"${promotedDetail.version}"`,
      },
    },
  );
  expect(promotionRequest.status(), await promotionRequest.text()).toBe(202);
  const requested = (await promotionRequest.json()) as Record<string, any>;

  const failedReview = await reviewerApi.post(
    `/api/v1/promotion-decisions/${requested.promotion_decision_id}/actions/review`,
    {
      data: {
        decision: "APPROVE",
        reason: "Approve Candidate and exercise real score queue failure.",
        risk_acknowledged: true,
      },
      headers: {
        "Idempotency-Key": unique("promotion-failed-review"),
        "If-Match": `W/"${requested.version}"`,
        "X-ODP-Test-Fault": "score-failure",
      },
    },
  );
  expect(failedReview.status(), await failedReview.text()).toBe(422);
  expect(await failedReview.text()).toContain("ODP_TEST_SCORE_FAILURE");

  const failedDecisionResponse = await reviewerApi.get(
    `/api/v1/promotion-decisions/${requested.promotion_decision_id}`,
  );
  expect(
    failedDecisionResponse.status(),
    await failedDecisionResponse.text(),
  ).toBe(200);
  const failedDecision = (await failedDecisionResponse.json()) as Record<
    string,
    any
  >;
  expect(failedDecision.status).toBe("SCORE_FAILED");
  expect(failedDecision.candidate_site_id).toBeTruthy();
  expect(failedDecision.site_score_job_id).toBeTruthy();
  const candidateId = failedDecision.candidate_site_id;

  await page.goto(
    `/w/expansion/listings/intake/${intakeId}?section=promotion`,
  );
  await expect(page.getByTestId("promotion-receipt-status")).toContainText(
    "SCORE_FAILED",
  );
  await expect(page.getByTestId("promotion-candidate-id")).toHaveText(
    candidateId,
  );
  await expect(page.getByTestId("candidate-retained-note")).toContainText(
    candidateId,
  );

  const jobResponse = await reviewerApi.get(
    `/api/v1/jobs/${failedDecision.site_score_job_id}/receipt`,
  );
  expect(jobResponse.status(), await jobResponse.text()).toBe(200);
  const job = (await jobResponse.json()) as Record<string, any>;
  await writeFile(
    `${READBACK}/promotion-score-failed-before-replay.json`,
    `${JSON.stringify({ failedDecision, job }, null, 2)}\n`,
  );
  const replayKey = unique("promotion-score-replay");
  const replayBody = {
    checkpoint: "SCORE_QUEUED",
    reason: "Score dependency recovered; replay durable checkpoint.",
    risk_acknowledged: true,
  };
  const replayHeaders = {
    "Idempotency-Key": replayKey,
    "If-Match": `W/"${job.version}"`,
  };
  const firstReplay = await reviewerApi.post(
    `/api/v1/jobs/${job.job_id}/retry`,
    { data: replayBody, headers: replayHeaders },
  );
  expect(firstReplay.status(), await firstReplay.text()).toBe(202);
  expect(firstReplay.headers()["idempotency-replayed"]).toBe("false");
  const firstReceipt = await firstReplay.json();
  const lostResponseReplay = await reviewerApi.post(
    `/api/v1/jobs/${job.job_id}/retry`,
    { data: replayBody, headers: replayHeaders },
  );
  expect(
    lostResponseReplay.status(),
    await lostResponseReplay.text(),
  ).toBe(202);
  expect(lostResponseReplay.headers()["idempotency-replayed"]).toBe("true");
  expect(await lostResponseReplay.json()).toEqual(firstReceipt);

  const completed = await pollDetail(
    reviewerApi,
    intakeId,
    (current) =>
      current.lifecycle.promotion?.status === "COMPLETED" ||
      current.lifecycle.promotion_history.some(
        (entry) => entry.status === "COMPLETED",
      ),
  );
  expect(completed.lifecycle.promotion?.candidate_site_id).toBe(candidateId);
  await page.reload();
  await expect(page.getByTestId("promotion-candidate-id")).toHaveText(
    candidateId,
  );
  await capture(page, "promotion-score-failure-replay", {
    completed,
    failedDecision,
    firstReceipt,
    lostResponseReplay: await lostResponseReplay.json(),
  });
  await proposerApi.dispose();
  await reviewerApi.dispose();
});

test("Retrieval and parser failure matrix exposes supported page-removed, timeout, partial and permanent/stale/auth-wall/bot-challenge variants", async ({
  page,
}) => {
  test.info().annotations.push({
    type: "FTR",
    description: "FTR-127..FTR-130",
  });
  const api = await apiFor();
  const cases = [
    {
      name: "page-removed",
      url: `https://www.synthetic.example/detail-${unique("removed")}.html`,
      expectedLineageCode: "ODP-INTAKE-RETRIEVAL-404",
      expectedUiCode: "ODP-INTAKE-RETRIEVAL-404",
      expectedState: "FAILED",
    },
    {
      name: "retrieval-timeout",
      url: "https://www.synthetic.example/detail-50000001.html",
      expectedLineageCode: "ODP-INTAKE-RETRIEVAL-TIMEOUT",
      expectedUiCode: "ODP-INTAKE-RETRIEVAL-TIMEOUT",
      expectedState: "FAILED",
    },
    {
      name: "parser-partial",
      url: "https://www.synthetic.example/detail-40028801.html",
      expectedLineageCode: "PARSER_PARTIAL",
      expectedUiCode: "ASSISTED_ENTRY_REQUIRED",
      expectedState: "AWAITING_ASSISTED_ENTRY",
    },
    {
      name: "authentication-wall",
      url: "https://www.synthetic.example/detail-50000002.html",
      expectedLineageCode: "AUTH_WALL_ENCOUNTERED",
      expectedUiCode: "AUTH_WALL_ENCOUNTERED",
      expectedState: "FAILED",
    },
    {
      name: "bot-challenge",
      url: "https://www.synthetic.example/detail-50000003.html",
      expectedLineageCode: "BOT_CHALLENGE_ENCOUNTERED",
      expectedUiCode: "BOT_CHALLENGE_ENCOUNTERED",
      expectedState: "FAILED",
    },
    {
      name: "parser-permanent",
      url: "https://www.synthetic.example/detail-50000004.html",
      expectedLineageCode: "PARSER_PERMANENT_FAILURE",
      expectedUiCode: "PARSER_PERMANENT_FAILURE",
      expectedState: "FAILED",
    },
    {
      name: "stale-source-snapshot",
      url: "https://www.synthetic.example/detail-50000005.html",
      expectedLineageCode: "STALE_SOURCE_SNAPSHOT",
      expectedUiCode: "STALE_SOURCE_SNAPSHOT",
      expectedState: "NEEDS_REVIEW",
    },
  ] as const;
  const readback: Record<string, IntakeDetail> = {};
  for (const item of cases) {
    const submitted = await submitUrl(api, item.url);
    expect([200, 202]).toContain(submitted.status);
    const intakeId = String(submitted.body.intake_id);
    const detail = await pollDetail(
      api,
      intakeId,
      (current) =>
        current.state === item.expectedState &&
        (item.expectedState !== "FAILED" ||
          current.processing_history.some(
            (entry) => entry.reason_code === "MAX_RETRIES_EXHAUSTED",
          )),
    );
    readback[item.name] = detail;
    await writeFile(
      `${READBACK}/retrieval-${item.name}.json`,
      `${JSON.stringify(detail, null, 2)}\n`,
    );
    const codes = [
      detail.issue,
      detail.failure?.code,
      ...detail.processing_history.map((entry) => entry.reason_code),
      ...(detail.lifecycle.job_history ?? []).flatMap((entry) => [
        entry.reason_code,
        (entry.receipt as Record<string, unknown> | undefined)?.reason_code,
        (entry.receipt as Record<string, unknown> | undefined)?.error_code,
      ]),
    ];
    expect.soft(
      codes,
      `${item.name} must preserve its exact failure code after retry exhaustion`,
    ).toContain(item.expectedLineageCode);
    if (item.expectedState === "FAILED") {
      expect.soft(detail.issue).toBe(item.expectedUiCode);
      expect.soft(detail.next_action).toBe("REPLAY_FROM_CHECKPOINT");
      expect.soft(
        detail.processing_history.map((entry) => entry.reason_code),
      ).toContain("MAX_RETRIES_EXHAUSTED");
    }
    await page.goto(
      `/w/expansion/listings/intake/${intakeId}?section=error`,
    );
    await expect(page.getByTestId("intake-processing-page")).toBeVisible();
    await expect.soft(page.locator("body")).toContainText(item.expectedUiCode);
  }

  const authRequired = await submitUrl(
    api,
    `https://www.housefun.com.tw/detail/${Date.now()}`,
  );
  const authDetail = await pollDetail(
    api,
    String(authRequired.body.intake_id),
    (current) =>
      current.policy_state === "AUTH_REQUIRED" &&
      current.state === "AWAITING_ASSISTED_ENTRY",
  );
  readback["auth-required-policy"] = authDetail;

  const requiredVariants = [
    "AUTH_WALL_ENCOUNTERED",
    "BOT_CHALLENGE_ENCOUNTERED",
    "PARSER_PERMANENT_FAILURE",
    "STALE_SOURCE_SNAPSHOT",
  ];
  const allCodes = Object.values(readback).flatMap((detail) => [
    detail.failure?.code,
    ...detail.processing_history.map((entry) => entry.reason_code),
  ]);
  for (const requiredCode of requiredVariants) {
    expect.soft(
      allCodes,
      `Production API/UI must expose required variant ${requiredCode}`,
    ).toContain(requiredCode);
  }
  await capture(page, "retrieval-parser-failure-matrix", {
    readback,
    requiredVariants,
  });
  await api.dispose();
});
