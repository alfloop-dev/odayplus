import { mkdir } from "node:fs/promises";
import AxeBuilder from "@axe-core/playwright";
import {
  expect,
  request as playwrightRequest,
  test,
  type APIRequestContext,
  type Browser,
  type BrowserContext,
  type Page,
} from "@playwright/test";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:18199";
const SCREENSHOT_DIR =
  "docs/evidence/completion/ODP-INTAKE-FCL-INTEGRATION-001/screenshots";
const TENANT_ID = "00000000-0000-0000-0000-000000000001";
const SUBJECTS = {
  staff: "00000000-0000-0000-0000-000000000101",
  manager: "00000000-0000-0000-0000-000000000102",
  reviewer: "00000000-0000-0000-0000-000000000103",
  steward: "00000000-0000-0000-0000-000000000104",
  governance: "00000000-0000-0000-0000-000000000105",
  privacy: "00000000-0000-0000-0000-000000000106",
  limited: "00000000-0000-0000-0000-000000000107",
} as const;

const ROLES = {
  "expansion-staff": {
    roles: "expansion-staff",
    subject: SUBJECTS.staff,
  },
  "expansion-manager": {
    roles: "expansion-manager",
    subject: SUBJECTS.manager,
  },
  "data-steward": {
    roles: "data-steward",
    subject: SUBJECTS.steward,
  },
  "governance-reviewer": {
    roles: "governance-reviewer",
    subject: SUBJECTS.governance,
  },
  "privacy-officer": {
    roles: "privacy-officer",
    subject: SUBJECTS.privacy,
  },
  "permission-limited": {
    roles: "permission-limited",
    subject: SUBJECTS.limited,
  },
} as const;

type RoleId = keyof typeof ROLES;
type LifecycleReceipt = {
  receipt_id: string | null;
  category: string;
  action: string | null;
  resource_id: string | null;
  resource_version: number | null;
  status: string | null;
  correlation_id: string | null;
  occurred_at: string | null;
  receipt: {
    from_state?: string | null;
    to_state?: string | null;
    checkpoint?: string | null;
    attempt?: number | null;
    reason?: string | null;
  };
};

type CanonicalDetail = {
  intake_id: string;
  state: string;
  version: number;
  policy_state?: string | null;
  source_snapshot_id?: string | null;
  match_outcome?: string | null;
  match_case_id?: string | null;
  match_case_version?: number | null;
  fields: Array<{
    field_path: string;
    corrected?: unknown;
    effective?: unknown;
    masked?: boolean;
  }>;
  processing_history: Array<{
    transition_id: string;
    from_state: string | null;
    to_state: string;
    reason_code?: string | null;
    version_after: number;
  }>;
  lifecycle: {
    actor_facts: {
      role_mode: string;
      allowed_actions: string[];
      purpose: { required: boolean; bound: boolean };
    };
    latest_decision_receipt?: {
      decision_id?: string | null;
      listing_id?: string | null;
      version?: number;
    } | null;
    assignment?: {
      assignment_id: string;
      status: string;
      owner_subject_id: string | null;
      queue_id: string | null;
      due_at: string | null;
      version: number;
    } | null;
    sla?: {
      sla_instance_id: string;
      state: string;
      due_at: string | null;
      paused_duration_seconds: number | null;
      version: number;
    } | null;
    job?: {
      job_id: string;
      status: string;
      attempt: number | null;
      checkpoint: string | null;
      next_retry_at: string | null;
      version: number | null;
    } | null;
    decisions: Array<{
      decision_id: string;
      status: string;
      action: string | null;
      version: number;
    }>;
    assignment_history: LifecycleReceipt[];
    sla_history: LifecycleReceipt[];
    job_history: LifecycleReceipt[];
    mutation_receipts: LifecycleReceipt[];
    promotion?: {
      candidate_site_id?: string | null;
      site_score_job_id?: string | null;
      status?: string;
    } | null;
  };
  original_url?: string | null;
  masked_fields?: string[];
};

type BatchRow = {
  address_raw: string;
  area_ping: number;
  floor: string;
  listing_status?: string;
  listing_type?: string;
  original_url?: string;
  rent_amount: number;
  source_id: string;
  source_listing_id: string;
};

const managerHeaders = headersFor("expansion-manager");

test.describe.configure({ mode: "serial" });
test.use({ extraHTTPHeaders: managerHeaders });
test.beforeAll(async () => {
  await mkdir(SCREENSHOT_DIR, { recursive: true });
});

function unique(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function headersFor(role: RoleId, subject = ROLES[role].subject) {
  return {
    "x-operator-role": role,
    "x-roles": ROLES[role].roles,
    "x-subject-id": subject,
    "x-tenant-id": TENANT_ID,
  };
}

async function apiFor(role: RoleId, subject = ROLES[role].subject) {
  return playwrightRequest.newContext({
    baseURL: API_BASE_URL,
    extraHTTPHeaders: headersFor(role, subject),
  });
}

async function browserContextFor(
  browser: Browser,
  role: RoleId,
  subject = ROLES[role].subject,
) {
  return browser.newContext({
    baseURL: process.env.ODP_WEB_BASE_URL ?? "http://127.0.0.1:13199",
    extraHTTPHeaders: headersFor(role, subject),
    viewport: { width: 1440, height: 900 },
  });
}

async function getDetail(
  api: APIRequestContext,
  intakeId: string,
): Promise<CanonicalDetail> {
  const response = await api.get(`/api/v1/intakes/${intakeId}`);
  expect(response.status(), await response.text()).toBe(200);
  return response.json() as Promise<CanonicalDetail>;
}

async function pollDetail(
  api: APIRequestContext,
  intakeId: string,
  predicate: (detail: CanonicalDetail) => boolean,
  timeout = 30_000,
): Promise<CanonicalDetail> {
  const deadline = Date.now() + timeout;
  let detail = await getDetail(api, intakeId);
  while (!predicate(detail) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    detail = await getDetail(api, intakeId);
  }
  expect(predicate(detail), JSON.stringify(detail, null, 2)).toBe(true);
  return detail;
}

async function createBatch(
  api: APIRequestContext,
  rows: BatchRow[],
): Promise<string[]> {
  const response = await api.post("/api/v1/intake-batches", {
    data: {
      batch_id: crypto.randomUUID(),
      method: "MANUAL",
      scope: { tenant_id: TENANT_ID },
      rows,
    },
    headers: {
      "Idempotency-Key": unique("batch"),
      "X-Correlation-Id": crypto.randomUUID(),
    },
  });
  expect(response.status(), await response.text()).toBe(202);
  const receipt = (await response.json()) as {
    rows: Array<{ status: string; intake_id: string | null }>;
  };
  expect(
    receipt.rows.every((row) => row.status === "ACCEPTED" && row.intake_id),
  ).toBe(true);
  return receipt.rows.map((row) => row.intake_id!);
}

async function submitCanonicalUrl(api: APIRequestContext, url: string) {
  const response = await api.post("/api/v1/intakes/url", {
    data: {
      original_url: url,
      scope: { tenant_id: TENANT_ID },
      purpose: "Assisted Listing Intake functional closure E2E",
    },
    headers: {
      "Idempotency-Key": unique("url-submit"),
      "X-Correlation-Id": crypto.randomUUID(),
    },
  });
  expect([200, 202]).toContain(response.status());
  return {
    status: response.status(),
    body: (await response.json()) as Record<string, unknown>,
  };
}

function observeLegacyRequests(page: Page) {
  const legacy: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/operator/network-listings")) {
      legacy.push(`${request.method()} ${request.url()}`);
    }
  });
  return legacy;
}

async function openDurable(page: Page, intakeId: string, section = "timeline") {
  const href = `/w/expansion/listings/intake/${intakeId}?section=${section}`;
  let mounted = false;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    if (attempt === 0) {
      await page.goto(href);
    } else {
      await page.reload();
    }
    try {
      await expect(page.getByTestId("intake-processing-page")).toBeVisible({
        timeout: 15_000,
      });
      mounted = true;
      break;
    } catch (error) {
      if (attempt === 1) throw error;
    }
  }
  expect(mounted).toBe(true);
  await expect(page.getByTestId("intake-detail-id")).toHaveText(intakeId);
  await expect(page).toHaveURL(
    new RegExp(`/w/expansion/listings/intake/${intakeId}`),
  );
}

async function reloadDurable(page: Page, intakeId: string) {
  let mounted = false;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    await page.reload();
    try {
      await expect(page.getByTestId("intake-processing-page")).toBeVisible({
        timeout: 15_000,
      });
      mounted = true;
      break;
    } catch (error) {
      if (attempt === 1) throw error;
    }
  }
  expect(mounted).toBe(true);
  await expect(page.getByTestId("intake-detail-id")).toHaveText(intakeId);
}

async function expectFullRecoveryEnvelope(page: Page, code: string) {
  const recovery = page.getByTestId("intake-error-recovery");
  await expect(recovery).toBeVisible();
  await expect(page.getByTestId("error-code")).toHaveText(code);
  await expect(page.getByTestId("error-correlation-id")).not.toContainText(
    "API 未回傳",
  );
  await expect(page.getByTestId("error-occurred-at")).not.toContainText(
    "API 未回傳",
  );
  await expect(page.getByTestId("error-current-state")).not.toContainText(
    "API 未回傳",
  );
  await expect(page.getByTestId("error-current-version")).not.toContainText(
    "API 未回傳",
  );
  await expect(page.getByTestId("error-operation")).not.toContainText(
    "API 未回傳",
  );
  await expect(page.getByTestId("error-server-value")).not.toContainText(
    "API 未回傳",
  );
  await expect(page.getByTestId("error-next-action")).not.toContainText(
    "API 未回傳",
  );
  await expect(page.getByTestId("error-toggle-preserved-input")).toBeVisible();
}

function expectNoHorizontalOverflow(page: Page) {
  return expect
    .poll(() =>
      page.evaluate(() => ({
        client: document.documentElement.clientWidth,
        scroll: document.documentElement.scrollWidth,
      })),
    )
    .toMatchObject({ client: expect.any(Number), scroll: expect.any(Number) })
    .then(async () => {
      const widths = await page.evaluate(() => ({
        client: document.documentElement.clientWidth,
        scroll: document.documentElement.scrollWidth,
      }));
      expect(widths.scroll).toBeLessThanOrEqual(widths.client + 1);
    });
}

test("URL submit returns a canonical queued receipt, polls persisted worker state, and survives direct reload", async ({
  page,
}) => {
  test.info().annotations.push({
    type: "approved-retrieval-proof",
    description:
      "tests/integration/test_assisted_listing_functional_runtime.py::test_canonical_api_submit_runs_through_production_worker_and_persisted_get proves approved retrieval with a real HTTP origin; this browser case proves canonical enqueue, polling and durable readback without fixture_replay.",
  });
  const legacy = observeLegacyRequests(page);
  const api = await apiFor("expansion-manager");
  const url = `https://unregistered.example/listing/${unique("queued")}`;

  const submitted = await submitCanonicalUrl(api, url);
  expect(submitted.status).toBe(202);
  expect(submitted.body.job_id).toBeTruthy();
  expect(submitted.body.correlation_id).toBeTruthy();
  const intakeId = String(submitted.body.intake_id);
  await openDurable(page, intakeId);

  const completed = await pollDetail(
    api,
    intakeId,
    (detail) =>
      !["SUBMITTED", "CHECKING_IDENTITY", "CHECKING_SOURCE_POLICY"].includes(
        detail.state,
      ),
  );
  expect(completed.processing_history.map((entry) => entry.to_state)).toContain(
    completed.state,
  );
  await expect(page.getByTestId("timeline-current-stage")).toContainText(
    completed.state,
    { timeout: 20_000 },
  );

  await reloadDurable(page, intakeId);
  await expect(page.getByTestId("intake-detail-id")).toHaveText(intakeId);
  expect((await getDetail(api, intakeId)).state).toBe(completed.state);
  expect(legacy).toEqual([]);
  await api.dispose();
});

test("source-policy matrix persists all five outcomes and never retrieves non-approved sources", async ({
  page,
}) => {
  test.info().annotations.push({
    type: "approved-retrieval-runtime-proof",
    description:
      "This browser case proves policy presentation and canonical readback. tests/integration/test_assisted_listing_functional_runtime.py::test_canonical_api_submit_runs_through_production_worker_and_persisted_get proves APPROVED_RETRIEVAL against a real HTTP origin.",
  });
  const api = await apiFor("expansion-manager");
  const cases = [
    {
      expectedPolicy: "APPROVED_RETRIEVAL",
      expectedState: "FAILED",
      url: "https://www.synthetic.example/detail-77120345.html",
    },
    {
      expectedPolicy: "ASSISTED_ENTRY_ONLY",
      expectedState: "AWAITING_ASSISTED_ENTRY",
      url: `https://www.591.com.tw/rent-detail-${Date.now()}.html`,
    },
    {
      expectedPolicy: "AUTH_REQUIRED",
      expectedState: "AWAITING_ASSISTED_ENTRY",
      url: `https://www.housefun.com.tw/detail/${Date.now() + 1}`,
    },
    {
      expectedPolicy: "SOURCE_BLOCKED",
      expectedState: "QUARANTINED",
      url: `https://listing-aggregator.example/item/${unique("blocked")}`,
    },
    {
      expectedPolicy: "POLICY_UNKNOWN",
      expectedState: "QUARANTINED",
      url: `https://${unique("unknown")}.example/listing/1`,
    },
  ] as const;

  for (const policyCase of cases) {
    const submitted = await submitCanonicalUrl(api, policyCase.url);
    expect(submitted.status).toBe(202);
    const intakeId = String(submitted.body.intake_id);
    const persisted = await pollDetail(
      api,
      intakeId,
      (detail) =>
        detail.policy_state === policyCase.expectedPolicy &&
        detail.state === policyCase.expectedState,
      75_000,
    );
    const stages = persisted.processing_history.map((entry) => entry.to_state);

    if (policyCase.expectedPolicy === "APPROVED_RETRIEVAL") {
      expect(stages).toContain("RETRIEVING");
    } else {
      expect(stages).not.toContain("RETRIEVING");
      expect(persisted.source_snapshot_id).toBeFalsy();
    }

    await openDurable(page, intakeId, "evidence");
    await expect(page.getByTestId("intake-detail-stage")).toBeVisible();
    await expect(page.getByTestId("evidence-policy-state")).toContainText(
      policyCase.expectedPolicy,
    );
    await reloadDurable(page, intakeId);
    await expect(page.getByTestId("evidence-policy-state")).toContainText(
      policyCase.expectedPolicy,
    );
  }

  await api.dispose();
});

test("CANCELLED intake is terminal after canonical browser cancellation and reload", async ({
  page,
}) => {
  const api = await apiFor("expansion-manager");
  const [intakeId] = await createBatch(api, [
    {
      address_raw: "台北市信義區松仁路 96 號 1F",
      area_ping: 18,
      floor: "1F 臨路",
      listing_status: "active",
      listing_type: "店面",
      rent_amount: 61800,
      source_id: "SRC-SYNTHETIC",
      source_listing_id: unique("cancelled-possible"),
    },
  ]);
  expect((await getDetail(api, intakeId)).state).toBe("NEEDS_REVIEW");

  await openDurable(page, intakeId, "timeline");
  await expect(page.getByTestId("timeline-cancel-button")).toBeVisible();
  await page.getByTestId("timeline-cancel-button").click();
  const cancelled = await pollDetail(
    api,
    intakeId,
    (detail) =>
      detail.state === "CANCELLED" &&
      detail.processing_history.some(
        (entry) => entry.to_state === "CANCELLED",
      ) &&
      detail.lifecycle.mutation_receipts.some(
        (entry) => entry.category === "intake" && entry.action === "CANCEL",
      ),
  );
  expect(cancelled.processing_history.at(-1)?.to_state).toBe("CANCELLED");

  await reloadDurable(page, intakeId);
  await expect(page.getByTestId("timeline-current-stage")).toContainText(
    "CANCELLED",
  );
  await expect(page.getByTestId("timeline-cancelled-terminal")).toContainText(
    "terminal",
  );
  await expect(page.getByTestId("timeline-cancel-button")).toHaveCount(0);
  await expect(page.getByTestId("timeline-retry-button")).toHaveCount(0);
  await expect(page.getByTestId("timeline-reopen-button")).toHaveCount(0);
  await api.dispose();
});

test("EXACT_DUPLICATE is intercepted by canonical URL identity and returns the persisted existing target", async ({
  page,
}) => {
  const legacy = observeLegacyRequests(page);
  const api = await apiFor("expansion-manager", SUBJECTS.manager);
  const reviewerApi = await apiFor("expansion-manager", SUBJECTS.reviewer);
  const url = `https://unregistered.example/listing/${unique("exact")}`;
  const [sourceIntakeId] = await createBatch(api, [
    {
      address_raw: `嘉義市東區耐斯路 ${Date.now() % 1000} 號 1F`,
      area_ping: 21,
      floor: "1F",
      original_url: url,
      rent_amount: 48000,
      source_id: "manual.operator",
      source_listing_id: unique("exact-source"),
    },
  ]);
  const source = await getDetail(api, sourceIntakeId);
  expect(source.match_outcome).toBe("NEW");
  const proposal = await api.post(
    `/api/v1/match-cases/${source.match_case_id}/decisions`,
    {
      data: {
        decision_type: "CREATE",
        reason:
          "Create the authoritative Listing used by exact URL identity proof.",
        risk_acknowledged: true,
      },
      headers: {
        "Idempotency-Key": unique("exact-create"),
        "If-Match": `W/"${source.match_case_version}"`,
      },
    },
  );
  expect(proposal.status(), await proposal.text()).toBe(201);
  const decisionId = String((await proposal.json()).decision_id);
  const review = await reviewerApi.post(
    `/api/v1/identity-decisions/${decisionId}/actions/review`,
    {
      data: {
        decision: "APPROVE",
        reason: "Independent reviewer confirms the canonical source identity.",
        risk_acknowledged: true,
      },
      headers: {
        "Idempotency-Key": unique("exact-review"),
        "If-Match": 'W/"1"',
      },
    },
  );
  expect(review.status(), await review.text()).toBe(200);
  const reviewedDecision = (await review.json()) as {
    effect_receipt?: {
      runtime_receipt?: { listing_id?: string | null } | null;
    } | null;
  };
  const existingListingId =
    reviewedDecision.effect_receipt?.runtime_receipt?.listing_id;
  expect(existingListingId).toBeTruthy();

  const duplicate = await submitCanonicalUrl(api, url);
  expect(duplicate.status).toBe(200);
  const duplicateIntakeId = String(duplicate.body.intake_id);
  expect(duplicateIntakeId).toBe(sourceIntakeId);
  expect(duplicate.body.identity_outcome).toBe("EXACT_DUPLICATE");
  expect(duplicate.body.existing_listing_id).toBe(existingListingId);
  expect(duplicate.body.navigation_target).toBe(
    `/w/expansion/listings/${existingListingId}`,
  );
  expect(duplicate.body.submission_receipt_id).toBeTruthy();
  const listingResponse = await api.get(
    `/api/v1/listings/${existingListingId}`,
  );
  expect(listingResponse.status(), await listingResponse.text()).toBe(200);
  const authoritativeListing = (await listingResponse.json()) as {
    current_revision_id: string | null;
    current_values: Record<string, unknown>;
    identity_edges: Array<Record<string, unknown>>;
    listing_id: string;
    revisions: Array<Record<string, unknown>>;
  };
  expect(authoritativeListing).toMatchObject({
    listing_id: existingListingId,
    current_values: {
      rent_per_month: 48000,
    },
  });
  expect(authoritativeListing.current_revision_id).toBeNull();
  expect(authoritativeListing.revisions).toEqual([]);
  expect(authoritativeListing.identity_edges.length).toBeGreaterThan(0);
  const persisted = await getDetail(api, duplicateIntakeId);
  expect(
    persisted.processing_history.map((entry) => entry.to_state),
  ).not.toContain("RETRIEVING");
  await openDurable(page, duplicateIntakeId);
  expect(await getDetail(api, duplicateIntakeId)).toMatchObject({
    intake_id: duplicateIntakeId,
  });
  await page.goto(String(duplicate.body.navigation_target));
  await expect(page.getByTestId("listing-detail-page")).toBeVisible();
  await expect(page.getByTestId("listing-detail-id")).toHaveText(
    String(existingListingId),
  );
  await expect(
    page.locator('section[aria-labelledby="listing-summary-title"]'),
  ).toContainText("48000");
  await expect(page.getByTestId("listing-detail-revisions")).toContainText(
    "尚無追加版本",
  );
  const identityEdgeId = String(
    authoritativeListing.identity_edges[0].edge_id ??
      authoritativeListing.identity_edges[0].edgeId,
  );
  await expect(page.getByTestId("listing-detail-identity-edges")).toContainText(
    identityEdgeId,
  );
  expect(legacy).toEqual([]);
  await reviewerApi.dispose();
  await api.dispose();
});

test("ASSISTED_ENTRY_ONLY preserves a durable non-authoritative draft across reload and a real version conflict", async ({
  page,
}) => {
  const legacy = observeLegacyRequests(page);
  const api = await apiFor("expansion-manager");
  const submitted = await submitCanonicalUrl(
    api,
    `https://www.591.com.tw/rent-detail-${Date.now()}.html`,
  );
  const intakeId = String(submitted.body.intake_id);
  const initial = await pollDetail(
    api,
    intakeId,
    (detail) => detail.state === "AWAITING_ASSISTED_ENTRY",
  );

  await openDurable(page, intakeId, "review");
  await expect(page.getByTestId("assisted-entry-form")).toBeVisible();
  await page
    .getByTestId("assisted-entry-address")
    .fill("高雄市左營區博愛三路 100 號 1F");
  await page.getByTestId("assisted-entry-rent").fill("72000");
  await page.getByTestId("assisted-entry-areaPing").fill("24");
  await page
    .getByTestId("assisted-entry-reason")
    .fill("依核准證據人工補錄必要欄位");
  await page.getByTestId("assisted-entry-risk-ack").check();

  await reloadDurable(page, intakeId);
  await expect(page.getByTestId("assisted-entry-address")).toHaveValue(
    "高雄市左營區博愛三路 100 號 1F",
  );
  await expect(page.getByTestId("assisted-entry-reason")).toHaveValue(
    "依核准證據人工補錄必要欄位",
  );

  let conflictInjected = false;
  await page.route(
    new RegExp(`/api/v1/intakes/${intakeId}/corrections$`),
    async (route) => {
      if (!conflictInjected) {
        conflictInjected = true;
        const assignment = await api.put(
          `/api/v1/intakes/${intakeId}/assignment`,
          {
            data: {
              owner_subject_id: SUBJECTS.manager,
              owner_role: "expansion-manager",
              due_at: "2026-07-30T16:00:00Z",
              reason:
                "Inject a real concurrent version change for browser conflict proof.",
            },
            headers: {
              "Idempotency-Key": unique("conflict-assignment"),
              "If-Match": `W/"${initial.version}"`,
            },
          },
        );
        expect(assignment.status(), await assignment.text()).toBe(200);
      }
      await route.continue();
    },
  );

  await page.getByTestId("assisted-entry-submit").click();
  await expect(page.getByTestId("assisted-entry-submit-error")).toContainText(
    "VERSION_CONFLICT",
  );
  await expect(page.getByTestId("assisted-entry-address")).toHaveValue(
    "高雄市左營區博愛三路 100 號 1F",
  );
  // The authoritative 409 is visible and the draft is durable. It has not
  // silently become authoritative.
  await reloadDurable(page, intakeId);
  await expect(page.getByTestId("assisted-entry-address")).toHaveValue(
    "高雄市左營區博愛三路 100 號 1F",
  );
  const afterConflict = await getDetail(api, intakeId);
  expect(afterConflict.version).toBeGreaterThan(initial.version);
  expect(afterConflict.fields).toHaveLength(3);
  expect(
    afterConflict.fields.every(
      (field) => field.corrected == null && field.effective == null,
    ),
  ).toBe(true);
  expect(legacy).toEqual([]);
  await api.dispose();
});

test("ASSISTED_ENTRY_ONLY commits canonical corrections and returns persisted field lineage", async ({
  page,
}) => {
  const legacy = observeLegacyRequests(page);
  const api = await apiFor("expansion-manager");
  const submitted = await submitCanonicalUrl(
    api,
    `https://www.591.com.tw/rent-detail-${Date.now()}-commit.html`,
  );
  const intakeId = String(submitted.body.intake_id);
  await pollDetail(
    api,
    intakeId,
    (detail) => detail.state === "AWAITING_ASSISTED_ENTRY",
  );
  await openDurable(page, intakeId, "review");
  await page
    .getByTestId("assisted-entry-address")
    .fill("高雄市左營區博愛三路 102 號 1F");
  await page.getByTestId("assisted-entry-rent").fill("73000");
  await page.getByTestId("assisted-entry-areaPing").fill("25");
  await page
    .getByTestId("assisted-entry-reason")
    .fill("依核准證據人工補錄並送出");
  await page.getByTestId("assisted-entry-risk-ack").check();
  await page.getByTestId("assisted-entry-submit").click();
  await expect(page.getByTestId("assisted-entry-submit-error")).toHaveCount(0);

  const proposed = await pollDetail(
    api,
    intakeId,
    (detail) =>
      detail.lifecycle.decisions.filter(
        (decision) =>
          decision.action === "identity_correction" &&
          decision.status === "PENDING_REVIEW",
      ).length === 3,
  );
  const reviewerApi = await apiFor("expansion-manager", SUBJECTS.reviewer);
  for (const decision of proposed.lifecycle.decisions.filter(
    (candidate) =>
      candidate.action === "identity_correction" &&
      candidate.status === "PENDING_REVIEW",
  )) {
    const review = await reviewerApi.post(
      `/api/v1/identity-decisions/${decision.decision_id}/actions/review`,
      {
        data: {
          decision: "APPROVE",
          reason:
            "Independent reviewer verifies assisted-entry source evidence.",
          risk_acknowledged: true,
        },
        headers: {
          "Idempotency-Key": unique("assisted-correction-review"),
          "If-Match": `W/"${decision.version}"`,
        },
      },
    );
    expect(review.status(), await review.text()).toBe(200);
  }

  const corrected = await pollDetail(reviewerApi, intakeId, (detail) =>
    detail.fields.some(
      (field) =>
        field.field_path === "address" &&
        String(field.corrected ?? field.effective).includes("博愛三路 102"),
    ),
  );
  expect(corrected.processing_history.length).toBeGreaterThan(0);
  expect(legacy).toEqual([]);
  await reviewerApi.dispose();
  await api.dispose();
});

test("NEW, REVISION and POSSIBLE_MATCH render distinct canonical compare outcomes on durable routes", async ({
  page,
}) => {
  const legacy = observeLegacyRequests(page);
  const api = await apiFor("expansion-manager");
  const [newId, revisionId, possibleId] = await createBatch(api, [
    {
      address_raw: `台中市西屯區功能路 ${Date.now() % 1000} 號 9F`,
      area_ping: 60,
      floor: "9F",
      listing_status: "active",
      listing_type: "辦公室",
      rent_amount: 125000,
      source_id: "manual.operator",
      source_listing_id: unique("manual-new"),
    },
    {
      address_raw: "台北市信義區松仁路 96 號 1F",
      area_ping: 18,
      floor: "1F 臨路",
      listing_status: "active",
      listing_type: "店面",
      rent_amount: 55000,
      source_id: "SRC-SYNTHETIC",
      source_listing_id: "synthetic-2024",
    },
    {
      address_raw: "台北市信義區松仁路 96 號 1F",
      area_ping: 18,
      floor: "1F 臨路",
      listing_status: "active",
      listing_type: "店面",
      rent_amount: 61000,
      source_id: "SRC-SYNTHETIC",
      source_listing_id: unique("synthetic-other"),
    },
  ]);

  for (const [intakeId, outcome] of [
    [newId, "NEW"],
    [revisionId, "REVISION"],
    [possibleId, "POSSIBLE_MATCH"],
  ] as const) {
    const detail = await getDetail(api, intakeId);
    expect(detail.match_outcome).toBe(outcome);
    await openDurable(page, intakeId, "identity");
    await expect(page.getByTestId("identity-match-badge")).toHaveText(outcome);
    await page.getByTestId("identity-tab-compare").click();
    await expect(page.getByTestId("listing-compare-table")).toBeVisible();
    await expect(page.getByTestId("compare-outcome-badge")).toHaveText(outcome);
  }
  await expect(page.getByTestId("identity-no-auto-merge-note")).toContainText(
    "不會自動合併",
  );
  expect(legacy).toEqual([]);
  await api.dispose();
});

test("POSSIBLE_MATCH identity proposal enforces a second actor and persists its decision readback", async ({
  browser,
}) => {
  const proposerApi = await apiFor("data-steward", SUBJECTS.steward);
  const [intakeId] = await createBatch(proposerApi, [
    {
      address_raw: "台北市信義區松仁路 96 號 1F",
      area_ping: 18,
      floor: "1F 臨路",
      listing_status: "active",
      listing_type: "店面",
      rent_amount: 61500,
      source_id: "SRC-SYNTHETIC",
      source_listing_id: unique("possible-review"),
    },
  ]);
  const proposerContext = await browserContextFor(
    browser,
    "data-steward",
    SUBJECTS.steward,
  );
  const proposerPage = await proposerContext.newPage();
  const proposerLegacy = observeLegacyRequests(proposerPage);
  await openDurable(proposerPage, intakeId, "identity");
  await proposerPage.getByTestId("identity-action-MARK_DUPLICATE").click();
  await proposerPage
    .getByTestId("identity-decision-reason")
    .fill("地址、樓層與坪數一致，提交第二人覆核重複關係。");
  await proposerPage.getByTestId("identity-risk-ack").check();
  await proposerPage.getByTestId("identity-submit-proposal").click();
  await expect(
    proposerPage.getByTestId("identity-durable-receipt"),
  ).toBeVisible();
  await expect(proposerPage.getByTestId("self-review-denied")).toContainText(
    "SELF_REVIEW_DENIED",
  );
  const proposed = await pollDetail(proposerApi, intakeId, (detail) =>
    detail.lifecycle.decisions.some(
      (decision) =>
        decision.action === "match_decision" &&
        decision.status === "PENDING_REVIEW",
    ),
  );
  const decisionId = proposed.lifecycle.decisions.find(
    (decision) =>
      decision.action === "match_decision" &&
      decision.status === "PENDING_REVIEW",
  )?.decision_id;
  expect(decisionId).toBeTruthy();

  const reviewerApi = await apiFor("expansion-manager", SUBJECTS.reviewer);
  const review = await reviewerApi.post(
    `/api/v1/identity-decisions/${decisionId}/actions/review`,
    {
      data: {
        decision: "APPROVE",
        reason: "第二人依來源與 canonical comparison 核准重複關係。",
        risk_acknowledged: true,
      },
      headers: {
        "Idempotency-Key": unique("possible-review"),
        "If-Match": 'W/"1"',
      },
    },
  );
  expect(review.status(), await review.text()).toBe(200);
  expect((await review.json()).status).toBe("EXECUTED");
  await reloadDurable(proposerPage, intakeId);
  await expect(
    proposerPage.getByTestId("identity-durable-receipt"),
  ).toContainText("EXECUTED");
  const readback = await getDetail(reviewerApi, intakeId);
  expect(
    readback.lifecycle.decisions.find(
      (decision) => decision.decision_id === decisionId,
    )?.status,
  ).toBe("EXECUTED");
  expect(proposerLegacy).toEqual([]);
  await proposerApi.dispose();
  await reviewerApi.dispose();
  await proposerContext.close();
});

test("NEW listing promotion requires an independent reviewer and persists Candidate plus SiteScore receipts", async ({
  browser,
}) => {
  const proposerContext = await browserContextFor(
    browser,
    "expansion-manager",
    SUBJECTS.manager,
  );
  const proposerPage = await proposerContext.newPage();
  const proposerApi = await apiFor("expansion-manager", SUBJECTS.manager);
  const [intakeId] = await createBatch(proposerApi, [
    {
      address_raw: `台南市中西區候選路 ${Date.now() % 1000} 號 1F`,
      area_ping: 28,
      floor: "1F",
      listing_status: "active",
      listing_type: "店面",
      rent_amount: 76000,
      source_id: "manual.operator",
      source_listing_id: unique("promotion-new"),
    },
  ]);

  await openDurable(proposerPage, intakeId, "identity");
  await proposerPage.getByTestId("identity-action-CREATE").click();
  await proposerPage
    .getByTestId("identity-decision-reason")
    .fill("無可靠既有 identity，建立獨立 Listing 並保留來源 lineage。");
  await proposerPage.getByTestId("identity-risk-ack").check();
  await proposerPage.getByTestId("identity-submit-proposal").click();
  const afterProposal = await pollDetail(proposerApi, intakeId, (detail) =>
    detail.lifecycle.decisions.some(
      (decision) =>
        decision.action === "match_decision" &&
        decision.status === "PENDING_REVIEW",
    ),
  );
  const identityDecisionId = afterProposal.lifecycle.decisions.find(
    (decision) =>
      decision.action === "match_decision" &&
      decision.status === "PENDING_REVIEW",
  )?.decision_id;
  expect(identityDecisionId).toBeTruthy();

  const reviewerApi = await apiFor("expansion-manager", SUBJECTS.reviewer);
  const identityReview = await reviewerApi.post(
    `/api/v1/identity-decisions/${identityDecisionId}/actions/review`,
    {
      data: {
        decision: "APPROVE",
        reason: "Independent reviewer confirms the new Listing decision.",
        risk_acknowledged: true,
      },
      headers: {
        "Idempotency-Key": unique("identity-review"),
        "If-Match": 'W/"1"',
      },
    },
  );
  expect(identityReview.status(), await identityReview.text()).toBe(200);

  await reloadDurable(proposerPage, intakeId);
  await proposerPage.getByTestId("tab-promotion").click();
  await expect(
    proposerPage.getByTestId("promotion-request-form"),
  ).toBeVisible();
  await proposerPage
    .getByTestId("promotion-request-reason")
    .fill("商圈、租金與來源證據均已核對，提出 Candidate Site 晉升。");
  await proposerPage.getByTestId("promotion-request-ack").check();
  await proposerPage.getByTestId("promotion-request-submit").click();
  await expect(
    proposerPage.getByTestId("promotion-self-review-denied"),
  ).toContainText("SELF_REVIEW_DENIED");

  const reviewerContext = await browserContextFor(
    browser,
    "expansion-manager",
    SUBJECTS.reviewer,
  );
  const reviewerPage = await reviewerContext.newPage();
  await openDurable(reviewerPage, intakeId, "promotion");
  await expect(
    reviewerPage.getByTestId("promotion-second-actor-ok"),
  ).toBeVisible();
  await reviewerPage
    .getByTestId("promotion-review-reason")
    .fill("第二人確認 gate snapshot 與 Listing evidence，核准晉升。");
  await reviewerPage.getByTestId("promotion-review-ack").check();
  await reviewerPage.getByTestId("promotion-approve-btn").click();
  await expect(
    reviewerPage.getByTestId("promotion-receipt-status"),
  ).toContainText("COMPLETED");
  const candidateId = (
    await reviewerPage.getByTestId("promotion-candidate-id").textContent()
  )?.trim();
  const scoreJobId = (
    await reviewerPage.getByTestId("promotion-score-job-id").textContent()
  )?.trim();
  expect(candidateId).toBeTruthy();
  expect(scoreJobId).toBeTruthy();

  const readback = await getDetail(reviewerApi, intakeId);
  expect(readback.lifecycle.promotion?.candidate_site_id).toBe(candidateId);
  expect(readback.lifecycle.promotion?.site_score_job_id).toBe(scoreJobId);
  await proposerApi.dispose();
  await reviewerApi.dispose();
  await proposerContext.close();
  await reviewerContext.close();
});

test("all six role modes receive authoritative detail facts, masking, purpose binding and distinct write visibility", async ({
  browser,
}) => {
  const ownerApi = await apiFor("expansion-staff");
  const submitted = await submitCanonicalUrl(
    ownerApi,
    `https://unregistered.example/listing/${unique("roles")}`,
  );
  const intakeId = String(submitted.body.intake_id);

  for (const role of Object.keys(ROLES) as RoleId[]) {
    const roleApi = await apiFor(role);
    const detail = await getDetail(roleApi, intakeId);
    expect(detail.lifecycle.actor_facts.role_mode).toBe(role);
    expect(detail.lifecycle.actor_facts.allowed_actions).toContain("VIEW");
    if (role === "governance-reviewer" || role === "privacy-officer") {
      expect(detail.lifecycle.actor_facts.purpose).toMatchObject({
        required: true,
        bound: true,
      });
    }
    if (role === "permission-limited") {
      expect(detail.original_url).toBeNull();
      expect(detail.masked_fields).toContain("original_url");
      expect(detail.lifecycle.actor_facts.allowed_actions).toEqual(["VIEW"]);
    }

    const context = await browserContextFor(browser, role);
    const page = await context.newPage();
    const legacy = observeLegacyRequests(page);
    await openDurable(page, intakeId, "evidence");
    if (
      role === "governance-reviewer" ||
      role === "privacy-officer" ||
      role === "permission-limited"
    ) {
      const decisionButtons = page
        .getByTestId("intake-detail-actions")
        .getByRole("button");
      expect(await decisionButtons.count()).toBeGreaterThan(0);
      for (let index = 0; index < (await decisionButtons.count()); index += 1) {
        await expect(decisionButtons.nth(index)).toBeDisabled();
      }
    }
    if (role === "permission-limited") {
      await expect(page.getByTestId("intake-open-source-link")).toHaveCount(0);
    }
    expect(legacy).toEqual([]);
    await context.close();
    await roleApi.dispose();
  }
  await ownerApi.dispose();
});

test("390 mobile preserves the desktop-required identity route while 1024 tablet and 1440 desktop remain usable without overflow", async ({
  page,
}) => {
  const api = await apiFor("expansion-manager");
  const [intakeId] = await createBatch(api, [
    {
      address_raw: "台北市信義區松仁路 96 號 1F",
      area_ping: 18,
      floor: "1F 臨路",
      listing_status: "active",
      listing_type: "店面",
      rent_amount: 62000,
      source_id: "SRC-SYNTHETIC",
      source_listing_id: unique("responsive-possible"),
    },
  ]);

  await page.setViewportSize({ width: 390, height: 844 });
  await openDurable(page, intakeId, "identity");
  await expect(page.getByTestId("identity-desktop-required")).toBeVisible();
  await expect(page.getByTestId("identity-desktop-link")).toHaveAttribute(
    "href",
    new RegExp(`/w/expansion/listings/intake/${intakeId}`),
  );
  await expectNoHorizontalOverflow(page);
  await page.screenshot({
    path: `${SCREENSHOT_DIR}/viewport-390.png`,
    fullPage: true,
  });

  await page.setViewportSize({ width: 1024, height: 768 });
  await reloadDurable(page, intakeId);
  await expectNoHorizontalOverflow(page);
  await page.screenshot({
    path: `${SCREENSHOT_DIR}/viewport-1024.png`,
    fullPage: true,
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await reloadDurable(page, intakeId);
  await expect(page.getByTestId("identity-desktop-workflow")).toBeVisible();
  await page.getByTestId("identity-tab-compare").click();
  await expect(page.getByTestId("listing-compare-table")).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.screenshot({
    path: `${SCREENSHOT_DIR}/viewport-1440.png`,
    fullPage: true,
  });
  await api.dispose();
});

test("assignment claim and SLA pause/resume survive canonical persisted readback", async ({
  page,
}) => {
  const legacy = observeLegacyRequests(page);
  const api = await apiFor("expansion-manager");
  const [intakeId] = await createBatch(api, [
    {
      address_raw: `台北市中山區生命週期路 ${Date.now() % 1000} 號 2F`,
      area_ping: 31,
      floor: "2F",
      listing_status: "active",
      listing_type: "辦公室",
      rent_amount: 88000,
      source_id: "manual.operator",
      source_listing_id: unique("assignment-sla"),
    },
  ]);

  await openDurable(page, intakeId, "assignment");
  await expect(page.getByTestId("asg-status")).toHaveText("UNASSIGNED");

  await page.getByTestId("asg-btn-claim").click();
  await expect(page.getByTestId("asg-status")).toHaveText("ASSIGNED");
  let persisted = await pollDetail(
    api,
    intakeId,
    (detail) => detail.lifecycle.assignment?.status === "ASSIGNED",
  );
  expect(
    persisted.lifecycle.assignment_history.map((item) => item.action),
  ).toContain("ASSIGN");

  await page.getByTestId("asg-btn-claim").click();
  await expect(page.getByTestId("asg-status")).toHaveText("CLAIMED");
  persisted = await pollDetail(
    api,
    intakeId,
    (detail) => detail.lifecycle.assignment?.status === "CLAIMED",
  );
  expect(
    persisted.lifecycle.assignment_history.map((item) => item.action),
  ).toContain("CLAIM");

  await page.getByTestId("asg-btn-pause").click();
  await expect(page.getByTestId("pause-sla-dialog")).toBeVisible();
  await page
    .getByTestId("pause-reason-input")
    .fill("等待來源權利文件，依營運規則暫停 SLA。");
  await page
    .getByTestId("pause-resume-time-input")
    .fill(new Date(Date.now() + 86_400_000).toISOString().slice(0, 16));
  await page.getByTestId("pause-risk-ack").check();
  await page.getByTestId("pause-submit-btn").click();
  await expect(page.getByTestId("pause-sla-dialog")).toHaveCount(0);
  await expect(page.getByTestId("asg-sla-status")).toContainText("PAUSED");

  await page.getByTestId("asg-btn-resume").click();
  await expect(page.getByTestId("asg-sla-status")).toContainText("ON_TRACK");

  persisted = await pollDetail(
    api,
    intakeId,
    (detail) =>
      detail.lifecycle.assignment?.status === "CLAIMED" &&
      detail.lifecycle.sla?.state === "ON_TRACK",
  );
  expect(
    persisted.lifecycle.assignment_history.map((item) => item.action),
  ).toEqual(expect.arrayContaining(["ASSIGN", "CLAIM"]));
  expect(persisted.lifecycle.sla_history.map((item) => item.action)).toEqual(
    expect.arrayContaining(["START", "PAUSE", "RESUME"]),
  );

  await reloadDurable(page, intakeId);
  await expect(page.getByTestId("asg-status")).toHaveText("CLAIMED");
  await expect(page.getByTestId("asg-sla-status")).toContainText("ON_TRACK");
  await expect(page.getByTestId("asg-history")).toContainText("PAUSED");
  await expect(page.getByTestId("asg-history")).toContainText("ON_TRACK");
  expect(legacy).toEqual([]);
  await api.dispose();
});

test("assignment transfer persists the selected canonical owner and handoff history", async ({
  page,
}) => {
  const api = await apiFor("expansion-manager");
  const [intakeId] = await createBatch(api, [
    {
      address_raw: `台北市大安區轉交路 ${Date.now() % 1000} 號`,
      area_ping: 19,
      floor: "1F",
      rent_amount: 49000,
      source_id: "manual.operator",
      source_listing_id: unique("assignment-transfer"),
    },
  ]);
  await openDurable(page, intakeId, "assignment");
  await page.getByTestId("asg-btn-claim").click();
  await expect(page.getByTestId("asg-status")).toHaveText("ASSIGNED");
  await page.getByTestId("asg-btn-claim").click();
  await expect(page.getByTestId("asg-status")).toHaveText("CLAIMED");

  await page.getByTestId("asg-btn-transfer").click();
  await page.getByTestId("transfer-target-subject").fill(SUBJECTS.steward);
  await page.getByTestId("transfer-target-select").selectOption("data-steward");
  await page
    .getByTestId("transfer-handoff-note")
    .fill("轉交資料管理員核對來源與欄位 lineage。");
  await page.getByTestId("transfer-risk-ack").check();
  await page.getByTestId("transfer-submit-btn").click();
  await expect(page.getByTestId("transfer-intake-dialog")).toHaveCount(0);
  await expect(page.getByTestId("asg-status")).toHaveText("TRANSFERRED");

  const persisted = await pollDetail(
    api,
    intakeId,
    (detail) =>
      detail.lifecycle.assignment?.status === "TRANSFERRED" &&
      detail.lifecycle.assignment?.owner_subject_id === SUBJECTS.steward,
  );
  expect(
    persisted.lifecycle.assignment_history.map((item) => item.action),
  ).toContain("TRANSFER");
  await api.dispose();
});

test("assignment escalation and completion persist authoritative assignment/SLA history", async ({
  page,
}) => {
  const api = await apiFor("expansion-manager");
  const [intakeId] = await createBatch(api, [
    {
      address_raw: `新竹市東區升級路 ${Date.now() % 1000} 號`,
      area_ping: 36,
      floor: "4F",
      rent_amount: 93000,
      source_id: "manual.operator",
      source_listing_id: unique("assignment-escalate"),
    },
  ]);
  await openDurable(page, intakeId, "assignment");
  await page.getByTestId("asg-btn-claim").click();
  await expect(page.getByTestId("asg-status")).toHaveText("ASSIGNED");
  await page.getByTestId("asg-btn-claim").click();
  await expect(page.getByTestId("asg-status")).toHaveText("CLAIMED");

  await page.getByTestId("asg-btn-escalate").click();
  await expect(page.getByTestId("asg-status")).toHaveText("ESCALATED");
  await page.getByTestId("asg-btn-complete").click();
  await expect(page.getByTestId("asg-status")).toHaveText("COMPLETED");
  await expect(page.getByTestId("asg-sla-status")).toContainText("COMPLETED");

  const persisted = await pollDetail(
    api,
    intakeId,
    (detail) =>
      detail.lifecycle.assignment?.status === "COMPLETED" &&
      detail.lifecycle.sla?.state === "COMPLETED",
  );
  expect(
    persisted.lifecycle.assignment_history.map((item) => item.action),
  ).toEqual(
    expect.arrayContaining(["ASSIGN", "CLAIM", "ESCALATE", "COMPLETE"]),
  );
  await reloadDurable(page, intakeId);
  await expect(page.getByTestId("asg-history")).toContainText("ESCALATED");
  await expect(page.getByTestId("asg-history")).toContainText("COMPLETED");
  await api.dispose();
});

test("FAILED intake exposes retry/cancel/DLQ/replay controls with authoritative job history", async ({
  page,
}) => {
  const legacy = observeLegacyRequests(page);
  const api = await apiFor("expansion-manager");
  const submitted = await submitCanonicalUrl(
    api,
    `https://www.synthetic.example/detail-${Date.now()}.html`,
  );
  const intakeId = String(submitted.body.intake_id);
  const failed = await pollDetail(
    api,
    intakeId,
    (detail) =>
      detail.state === "FAILED" &&
      ["FAILED", "DEAD_LETTER"].includes(detail.lifecycle.job?.status ?? ""),
    75_000,
  );
  const jobId = failed.lifecycle.job?.job_id;
  expect(jobId).toBeTruthy();
  expect(failed.lifecycle.job_history.length).toBeGreaterThan(0);

  await openDurable(page, intakeId, "timeline");
  await expect(page.getByTestId("timeline-current-stage")).toContainText(
    "FAILED",
  );
  const jobCard = page.getByTestId(`timeline-job-${jobId}`);
  await expect(jobCard).toBeVisible();
  await expect(jobCard).toContainText(failed.lifecycle.job!.status);
  await expect(page.getByTestId("timeline-retry-button")).toBeVisible();
  await expect(page.getByTestId(`timeline-replay-job-${jobId}`)).toBeVisible();

  await page.getByTestId("timeline-retry-button").click();
  const retriedIntake = await pollDetail(
    api,
    intakeId,
    (detail) =>
      detail.processing_history.some(
        (entry) =>
          entry.from_state === "FAILED" &&
          entry.to_state === "SUBMITTED" &&
          entry.reason_code === "RETRY_QUEUED",
      ) &&
      detail.lifecycle.mutation_receipts.some(
        (receipt) =>
          receipt.category === "intake" && receipt.action === "RETRY",
      ),
  );
  const retryTransition = retriedIntake.processing_history.find(
    (entry) =>
      entry.from_state === "FAILED" &&
      entry.to_state === "SUBMITTED" &&
      entry.reason_code === "RETRY_QUEUED",
  );
  expect(retryTransition).toBeTruthy();
  await expect(page.getByTestId("timeline-current-stage")).not.toContainText(
    "FAILED",
  );

  const failedAfterIntakeRetry = await pollDetail(
    api,
    intakeId,
    (detail) =>
      detail.state === "FAILED" &&
      ["FAILED", "DEAD_LETTER"].includes(detail.lifecycle.job?.status ?? "") &&
      detail.processing_history.some(
        (entry) => entry.transition_id === retryTransition?.transition_id,
      ),
    75_000,
  );
  expect(failedAfterIntakeRetry.lifecycle.job?.checkpoint).toBe(
    failed.lifecycle.job?.checkpoint,
  );
  await reloadDurable(page, intakeId);
  await expect(page.getByTestId("timeline-current-stage")).toContainText(
    "FAILED",
  );
  await expect(
    page.getByTestId(`timeline-transition-${retryTransition?.transition_id}`),
  ).toContainText("RETRY_QUEUED");
  await expect(page.getByTestId(`timeline-job-history-${jobId}`)).toBeVisible();

  await page.getByTestId(`timeline-replay-job-${jobId}`).click();
  await expect(page.getByTestId(`timeline-cancel-job-${jobId}`)).toBeVisible();
  await page.getByTestId(`timeline-cancel-job-${jobId}`).click();
  const cancelled = await pollDetail(
    api,
    intakeId,
    (detail) =>
      detail.lifecycle.job?.status === "CANCELLED" &&
      detail.lifecycle.job_history.some((item) => item.action === "CANCEL"),
  );
  expect(cancelled.lifecycle.job_history.map((item) => item.action)).toEqual(
    expect.arrayContaining(["RETRY", "CANCEL"]),
  );
  expect(
    cancelled.lifecycle.job_history.some(
      (item) =>
        item.status === "DEAD_LETTER" ||
        item.receipt.to_state === "DEAD_LETTER",
    ),
  ).toBe(true);
  expect(legacy).toEqual([]);
  await api.dispose();
});

test("QUARANTINED release requires a durable first-actor proposal and a distinct reviewer", async ({
  browser,
}) => {
  const proposerApi = await apiFor("expansion-manager", SUBJECTS.manager);
  const submitted = await submitCanonicalUrl(
    proposerApi,
    `https://unregistered.example/listing/${unique("quarantine-release")}`,
  );
  expect(submitted.status).toBe(202);
  const intakeId = String(submitted.body.intake_id);
  const quarantined = await pollDetail(
    proposerApi,
    intakeId,
    (detail) => detail.state === "QUARANTINED",
  );
  expect(quarantined.processing_history.at(-1)?.to_state).toBe("QUARANTINED");

  const proposerContext = await browserContextFor(
    browser,
    "expansion-manager",
    SUBJECTS.manager,
  );
  const proposerPage = await proposerContext.newPage();
  await openDurable(proposerPage, intakeId, "timeline");
  await expect(
    proposerPage.getByTestId("timeline-current-stage"),
  ).toContainText("QUARANTINED");
  await proposerPage.getByTestId("timeline-reopen-button").click();
  await expect(proposerPage.getByTestId("reopen-intake-dialog")).toBeVisible();
  await expect(
    proposerPage.getByTestId("reopen-workflow-summary"),
  ).toContainText("另一位具權限的人員覆核前");
  await proposerPage
    .getByTestId("reopen-intake-reason")
    .fill("來源政策已補齊，提出解除隔離並等待獨立覆核。");
  await proposerPage.getByTestId("reopen-intake-risk").check();
  await proposerPage.getByTestId("reopen-intake-submit").click();

  const proposed = await pollDetail(
    proposerApi,
    intakeId,
    (detail) =>
      detail.state === "QUARANTINED" &&
      detail.processing_history.some(
        (entry) =>
          entry.from_state === "QUARANTINED" &&
          entry.to_state === "QUARANTINED" &&
          entry.reason_code === "SECOND_ACTOR_REQUIRED",
      ),
  );
  const proposalTransition = proposed.processing_history.find(
    (entry) =>
      entry.from_state === "QUARANTINED" &&
      entry.to_state === "QUARANTINED" &&
      entry.reason_code === "SECOND_ACTOR_REQUIRED",
  );
  expect(proposalTransition).toBeTruthy();
  await reloadDurable(proposerPage, intakeId);
  await expect(
    proposerPage.getByTestId("timeline-current-stage"),
  ).toContainText("QUARANTINED");
  await expect(
    proposerPage.getByTestId("timeline-reopen-denied"),
  ).toContainText("SELF_REVIEW_DENIED");
  await expect(
    proposerPage.getByTestId(
      `timeline-transition-${proposalTransition?.transition_id}`,
    ),
  ).toContainText("SECOND_ACTOR_REQUIRED");

  const selfReview = await proposerApi.post(
    `/api/v1/intakes/${intakeId}/actions/reopen`,
    {
      data: {
        reason: "同一提案者不得自行解除隔離。",
        risk_acknowledged: true,
      },
      headers: {
        "Idempotency-Key": unique("quarantine-self-review"),
        "If-Match": `W/"${proposed.version}"`,
      },
    },
  );
  expect(selfReview.status(), await selfReview.text()).toBe(403);
  expect(await selfReview.text()).toContain("SELF_REVIEW_DENIED");

  const reviewerApi = await apiFor("expansion-manager", SUBJECTS.reviewer);
  const reviewerContext = await browserContextFor(
    browser,
    "expansion-manager",
    SUBJECTS.reviewer,
  );
  const reviewerPage = await reviewerContext.newPage();
  await openDurable(reviewerPage, intakeId, "timeline");
  await reviewerPage.getByTestId("timeline-reopen-button").click();
  await expect(reviewerPage.getByTestId("reopen-intake-dialog")).toBeVisible();
  await expect(
    reviewerPage.getByTestId("reopen-workflow-summary"),
  ).toContainText("已有解除提案");
  await reviewerPage
    .getByTestId("reopen-intake-reason")
    .fill("獨立覆核來源政策與隔離證據後，同意解除隔離。");
  await reviewerPage.getByTestId("reopen-intake-risk").check();
  await reviewerPage.getByTestId("reopen-intake-submit").click();

  const released = await pollDetail(
    reviewerApi,
    intakeId,
    (detail) =>
      detail.state === "NEEDS_REVIEW" &&
      detail.processing_history.some(
        (entry) =>
          entry.from_state === "QUARANTINED" &&
          entry.to_state === "NEEDS_REVIEW",
      ),
  );
  const releaseTransition = released.processing_history.find(
    (entry) =>
      entry.from_state === "QUARANTINED" && entry.to_state === "NEEDS_REVIEW",
  );
  expect(releaseTransition).toBeTruthy();
  await reloadDurable(reviewerPage, intakeId);
  await expect(
    reviewerPage.getByTestId("timeline-current-stage"),
  ).toContainText("NEEDS_REVIEW");
  await expect(
    reviewerPage.getByTestId(
      `timeline-transition-${releaseTransition?.transition_id}`,
    ),
  ).toBeVisible();
  expect(
    (await getDetail(reviewerApi, intakeId)).processing_history.map(
      (entry) => entry.transition_id,
    ),
  ).toContain(releaseTransition?.transition_id);

  await proposerApi.dispose();
  await reviewerApi.dispose();
  await proposerContext.close();
  await reviewerContext.close();
});

test("428 PRECONDITION_REQUIRED renders a complete recoverable UI envelope", async ({
  page,
}) => {
  const api = await apiFor("expansion-manager");
  const [intakeId] = await createBatch(api, [
    {
      address_raw: `新北市板橋區前置條件路 ${Date.now() % 1000} 號`,
      area_ping: 20,
      floor: "1F",
      rent_amount: 51000,
      source_id: "manual.operator",
      source_listing_id: unique("error-428"),
    },
  ]);
  await openDurable(page, intakeId, "assignment");
  await page.getByTestId("asg-btn-claim").click();
  await expect(page.getByTestId("asg-status")).toHaveText("ASSIGNED");

  await page.route(
    /\/api\/v1\/assignments\/[^/]+\/actions\/claim$/,
    async (route) => {
      const headers = { ...route.request().headers() };
      delete headers["if-match"];
      await route.continue({ headers });
    },
  );
  await page.getByTestId("asg-btn-claim").click();
  await page.getByTestId("tab-error").click();
  await expectFullRecoveryEnvelope(page, "PRECONDITION_REQUIRED");
  await api.dispose();
});

test("409 VERSION_CONFLICT preserves the attempted command and renders server-current recovery facts", async ({
  page,
}) => {
  const api = await apiFor("expansion-manager");
  const [intakeId] = await createBatch(api, [
    {
      address_raw: `桃園市中壢區版本衝突路 ${Date.now() % 1000} 號`,
      area_ping: 22,
      floor: "3F",
      rent_amount: 56000,
      source_id: "manual.operator",
      source_listing_id: unique("error-409"),
    },
  ]);
  await openDurable(page, intakeId, "assignment");
  await page.getByTestId("asg-btn-claim").click();
  const assigned = await pollDetail(
    api,
    intakeId,
    (detail) => detail.lifecycle.assignment?.status === "ASSIGNED",
  );
  const assignment = assigned.lifecycle.assignment!;
  let injected = false;
  await page.route(
    new RegExp(
      `/api/v1/assignments/${assignment.assignment_id}/actions/claim$`,
    ),
    async (route) => {
      if (!injected) {
        injected = true;
        const concurrent = await api.post(
          `/api/v1/assignments/${assignment.assignment_id}/actions/transfer`,
          {
            data: {
              target_owner_subject_id: SUBJECTS.reviewer,
              target_owner_role: "expansion-manager",
              handoff_note:
                "Concurrent transfer for authoritative VERSION_CONFLICT proof.",
              reason:
                "Concurrent transfer for authoritative VERSION_CONFLICT proof.",
            },
            headers: {
              "Idempotency-Key": unique("conflict-transfer"),
              "If-Match": `W/"${assignment.version}"`,
            },
          },
        );
        expect(concurrent.status(), await concurrent.text()).toBe(200);
      }
      await route.continue();
    },
  );
  await page.getByTestId("asg-btn-claim").click();
  await page.getByTestId("tab-error").click();
  await expectFullRecoveryEnvelope(page, "VERSION_CONFLICT");
  await api.dispose();
});

test("403 SELF_REVIEW_DENIED keeps the pending decision receipt and exposes a second-actor recovery path", async ({
  page,
}) => {
  const api = await apiFor("expansion-manager", SUBJECTS.manager);
  const reviewerApi = await apiFor("expansion-manager", SUBJECTS.reviewer);
  const address = `新北市板橋區獨立審核路 ${unique("address")} 號 1F`;
  const areaPing = 18;
  const floor = "1F 臨路";
  const [baseIntakeId] = await createBatch(api, [
    {
      address_raw: address,
      area_ping: areaPing,
      floor,
      listing_status: "active",
      listing_type: "店面",
      rent_amount: 61000,
      source_id: "manual.operator",
      source_listing_id: unique("self-review-base"),
    },
  ]);
  const base = await getDetail(api, baseIntakeId);
  expect(base.match_outcome).toBe("NEW");
  const baseProposal = await api.post(
    `/api/v1/match-cases/${base.match_case_id}/decisions`,
    {
      data: {
        decision_type: "CREATE",
        reason:
          "Create a unique persisted Listing for deterministic self-review matching.",
        risk_acknowledged: true,
      },
      headers: {
        "Idempotency-Key": unique("self-review-base-create"),
        "If-Match": `W/"${base.match_case_version}"`,
      },
    },
  );
  expect(baseProposal.status(), await baseProposal.text()).toBe(201);
  const baseDecisionId = String((await baseProposal.json()).decision_id);
  const baseReview = await reviewerApi.post(
    `/api/v1/identity-decisions/${baseDecisionId}/actions/review`,
    {
      data: {
        decision: "APPROVE",
        reason: "Independent second actor approves the unique base Listing.",
        risk_acknowledged: true,
      },
      headers: {
        "Idempotency-Key": unique("self-review-base-approve"),
        "If-Match": 'W/"1"',
      },
    },
  );
  expect(baseReview.status(), await baseReview.text()).toBe(200);
  const baseReviewReceipt = (await baseReview.json()) as {
    effect_receipt?: {
      runtime_receipt?: { listing_id?: string | null } | null;
    } | null;
  };
  const baseListingId =
    baseReviewReceipt.effect_receipt?.runtime_receipt?.listing_id;
  expect(baseListingId).toBeTruthy();
  const persistedBase = await api.get(`/api/v1/listings/${baseListingId}`);
  expect(persistedBase.status(), await persistedBase.text()).toBe(200);

  const [intakeId] = await createBatch(api, [
    {
      address_raw: address,
      area_ping: areaPing,
      floor,
      listing_status: "active",
      listing_type: "店面",
      rent_amount: 61000,
      source_id: "SRC-SYNTHETIC",
      source_listing_id: unique("self-review-independent-source"),
    },
  ]);
  const possibleMatch = await pollDetail(
    api,
    intakeId,
    (detail) => detail.match_outcome === "POSSIBLE_MATCH",
  );
  expect(possibleMatch.match_outcome).toBe("POSSIBLE_MATCH");
  await openDurable(page, intakeId, "identity");
  await expect(page.getByTestId("identity-match-badge")).toHaveText(
    "POSSIBLE_MATCH",
  );
  await page.getByTestId("identity-action-MARK_DUPLICATE").click();
  await page
    .getByTestId("identity-decision-reason")
    .fill("提交後由不同操作者覆核，不由提案者自行核准。");
  await page.getByTestId("identity-risk-ack").check();
  await page.getByTestId("identity-submit-proposal").click();
  await expect(page.getByTestId("identity-durable-receipt")).toContainText(
    "PENDING_REVIEW",
  );
  await expect(page.getByTestId("self-review-denied")).toContainText(
    "SELF_REVIEW_DENIED",
  );
  const persisted = await pollDetail(api, intakeId, (detail) =>
    detail.lifecycle.decisions.some(
      (decision) => decision.status === "PENDING_REVIEW",
    ),
  );
  const decisionId = persisted.lifecycle.decisions.find(
    (decision) => decision.status === "PENDING_REVIEW",
  )?.decision_id;
  expect(decisionId).toBeTruthy();
  const denied = await api.post(
    `/api/v1/identity-decisions/${decisionId}/actions/review`,
    {
      data: {
        decision: "APPROVE",
        reason: "Self-review must fail authoritatively.",
        risk_acknowledged: true,
      },
      headers: {
        "Idempotency-Key": unique("self-review"),
        "If-Match": 'W/"1"',
      },
    },
  );
  expect(denied.status(), await denied.text()).toBe(403);
  expect(await denied.text()).toContain("SELF_REVIEW_DENIED");
  await reloadDurable(page, intakeId);
  await expect(page.getByTestId("identity-durable-receipt")).toContainText(
    "PENDING_REVIEW",
  );
  await expect(page.getByTestId("self-review-denied")).toContainText(
    "SELF_REVIEW_DENIED",
  );
  await expect(page.getByTestId("identity-review-approve")).toBeDisabled();
  await reviewerApi.dispose();
  await api.dispose();
});

test("422 VALIDATION_FAILED renders field-level recovery and preserves submitted values", async ({
  page,
}) => {
  const api = await apiFor("expansion-manager");
  const [intakeId] = await createBatch(api, [
    {
      address_raw: `台中市北屯區驗證路 ${Date.now() % 1000} 號`,
      area_ping: 27,
      floor: "5F",
      rent_amount: 64000,
      source_id: "manual.operator",
      source_listing_id: unique("error-422"),
    },
  ]);
  await openDurable(page, intakeId, "assignment");
  await page.getByTestId("asg-btn-claim").click();
  await expect(page.getByTestId("asg-status")).toHaveText("ASSIGNED");

  await page.route(
    /\/api\/v1\/assignments\/[^/]+\/actions\/transfer$/,
    async (route) => {
      await route.continue({
        postData: JSON.stringify({
          target_owner_subject_id: "",
          target_owner_role: "",
          handoff_note: "",
          reason: "",
        }),
        headers: {
          ...route.request().headers(),
          "content-type": "application/json",
        },
      });
    },
  );
  await page.getByTestId("asg-btn-transfer").click();
  await page.getByTestId("transfer-target-subject").fill(SUBJECTS.steward);
  await page.getByTestId("transfer-target-select").selectOption("data-steward");
  await page
    .getByTestId("transfer-handoff-note")
    .fill("此值必須在 422 後保留。");
  await page.getByTestId("transfer-risk-ack").check();
  await page.getByTestId("transfer-submit-btn").click();
  await expect(page.getByTestId("transfer-handoff-note")).toHaveValue(
    "此值必須在 422 後保留。",
  );
  await expect(page.getByTestId("transfer-error-panel")).toContainText(
    "VALIDATION_FAILED",
  );
  await page.getByRole("button", { name: "關閉" }).click();
  await page.getByTestId("tab-error").click();
  await expectFullRecoveryEnvelope(page, "VALIDATION_FAILED");
  await api.dispose();
});

test("canonical durable intake route has zero serious or critical axe violations", async ({
  page,
}) => {
  const api = await apiFor("expansion-manager");
  const [intakeId] = await createBatch(api, [
    {
      address_raw: `台北市中正區無障礙路 ${Date.now() % 1000} 號`,
      area_ping: 24,
      floor: "2F",
      rent_amount: 72000,
      source_id: "manual.operator",
      source_listing_id: unique("axe-durable"),
    },
  ]);

  await openDurable(page, intakeId, "timeline");
  const results = await new AxeBuilder({ page })
    .include('[data-testid="intake-processing-page"]')
    .analyze();
  const blocking = results.violations
    .filter(
      (violation) =>
        violation.impact === "serious" || violation.impact === "critical",
    )
    .map((violation) => ({
      id: violation.id,
      impact: violation.impact,
      nodes: violation.nodes.map((node) => ({
        failureSummary: node.failureSummary,
        target: node.target,
      })),
    }));

  expect(blocking).toEqual([]);
  await api.dispose();
});

test("canonical durable intake dialog is keyboard operable and restores focus to its trigger", async ({
  page,
}) => {
  const api = await apiFor("expansion-manager");
  const [intakeId] = await createBatch(api, [
    {
      address_raw: `台北市松山區鍵盤路 ${Date.now() % 1000} 號`,
      area_ping: 21,
      floor: "1F",
      rent_amount: 68000,
      source_id: "manual.operator",
      source_listing_id: unique("keyboard-focus"),
    },
  ]);

  await openDurable(page, intakeId, "assignment");
  await page.getByTestId("asg-btn-claim").click();
  await expect(page.getByTestId("asg-status")).toHaveText("ASSIGNED");

  const trigger = page.getByTestId("asg-btn-transfer");
  await trigger.focus();
  await expect(trigger).toBeFocused();
  await page.keyboard.press("Enter");

  const dialog = page.getByTestId("transfer-intake-dialog");
  await expect(dialog).toBeVisible();
  await expect
    .poll(() =>
      dialog.evaluate((element) =>
        element.contains(element.ownerDocument.activeElement),
      ),
    )
    .toBe(true);

  await page.keyboard.press("Shift+Tab");
  await expect
    .poll(() =>
      dialog.evaluate((element) =>
        element.contains(element.ownerDocument.activeElement),
      ),
    )
    .toBe(true);

  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  const focusDiagnostic = await page.evaluate(() => {
    const active = document.activeElement as HTMLElement | null;
    const transferButtons = Array.from(
      document.querySelectorAll<HTMLElement>(
        '[data-testid="asg-btn-transfer"]',
      ),
    );
    return {
      activeElement: active
        ? {
            ariaLabel: active.getAttribute("aria-label"),
            id: active.id || null,
            tagName: active.tagName,
            testId: active.dataset.testid ?? null,
            text: active.textContent?.trim() || null,
          }
        : null,
      currentUrl: window.location.href,
      transferButtonCount: transferButtons.length,
      visibleTransferButtonCount: transferButtons.filter((button) => {
        const style = getComputedStyle(button);
        const rect = button.getBoundingClientRect();
        return (
          style.display !== "none" &&
          style.visibility !== "hidden" &&
          rect.width > 0 &&
          rect.height > 0
        );
      }).length,
    };
  });
  await expect(
    trigger,
    `Focus restoration diagnostic: ${JSON.stringify(focusDiagnostic)}`,
  ).toBeFocused();
  await api.dispose();
});

test("canonical durable intake route honors prefers-reduced-motion without infinite animation", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const api = await apiFor("expansion-manager");
  const [intakeId] = await createBatch(api, [
    {
      address_raw: `新北市新店區減少動態路 ${Date.now() % 1000} 號`,
      area_ping: 26,
      floor: "3F",
      rent_amount: 76000,
      source_id: "manual.operator",
      source_listing_id: unique("reduced-motion"),
    },
  ]);

  await openDurable(page, intakeId, "timeline");
  expect(
    await page.evaluate(
      () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    ),
  ).toBe(true);

  const infiniteAnimations = await page
    .getByTestId("intake-processing-page")
    .evaluate((root) =>
      Array.from(root.querySelectorAll<HTMLElement>("*"))
        .filter((element) => {
          const style = getComputedStyle(element);
          return (
            style.animationName !== "none" &&
            style.animationIterationCount
              .split(",")
              .some((count) => count.trim() === "infinite")
          );
        })
        .map((element) => ({
          animationName: getComputedStyle(element).animationName,
          testId: element.dataset.testid ?? null,
          tag: element.tagName,
        })),
    );
  expect(infiniteAnimations).toEqual([]);
  await api.dispose();
});
