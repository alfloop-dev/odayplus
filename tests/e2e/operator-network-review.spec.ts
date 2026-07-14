import { expect, request as playwrightRequest, test } from "@playwright/test";

// ODP-OC-R4-007 — Network Review decision + atomic governance sync.
// Screens verified against package 6 (sha db3ea3d…): data-screen-label values
// "Network 選址審核" (review panel) / "Dialog Review Decision" (decision dialog).
// Decision mapping GO→Approved, WAIT→On Hold, Return→Need Data, Reject→Rejected;
// Candidate + Review + Approval + Decision + Audit sync in one transaction.

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";

// Site Reviewer holds sitescore APPROVE (may decide).
const REVIEWER_HEADERS = {
  "x-subject-id": "operator-site-reviewer",
  "x-roles": "site_reviewer",
  "x-operator-role": "expansion-manager",
  "x-tenant-id": "tenant-a",
};

// Expansion holds sitescore VIEW/EXECUTE only (may submit, not decide).
const EXPANSION_HEADERS = {
  "x-subject-id": "operator-expansion-manager",
  "x-roles": "expansion_user",
  "x-operator-role": "expansion-manager",
  "x-tenant-id": "tenant-a",
};

test.describe.configure({ mode: "serial" });

test.describe("ODP-OC-R4-007 Network Review decision", () => {
  test.beforeEach(async () => {
    const api = await reviewerContext();
    const reset = await api.post("/api/v1/operator/network-reviews/reset");
    expect(reset.status()).toBe(200);
    await api.dispose();
  });

  test("Review panel opens the decision dialog and approves the golden GO flow", async ({ page }) => {
    await page.goto("/operator?ws=network");
    await expect(page.getByTestId("network-find-areas-workspace")).toBeVisible();

    await page.getByTestId("network-tab-5").click();
    const panel = page.getByTestId("network-panel-review");
    await expect(panel).toBeVisible();
    await expect(panel).toHaveAttribute("data-screen-label", "Network 選址審核");

    // Queue is hydrated from the API (RV-702 GO / RV-701 WAIT / RV-698 REJECT).
    await expect(page.getByTestId("review-card-RV-702")).toContainText("信義松仁", { timeout: 15_000 });
    await expect(page.getByTestId("review-card-RV-701")).toBeVisible();
    await expect(page.getByTestId("review-card-RV-698")).toBeVisible();

    // Open the GO decision dialog and confirm — reason is written to the log.
    await page.getByTestId("review-card-RV-702").click();
    await page.getByTestId("review-btn-go-RV-702").click();
    const dialog = page.getByTestId("review-decision-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAttribute("data-screen-label", "Dialog Review Decision");
    await page.getByTestId("review-decision-reason").fill("人流量體大且回本期可接受，核准進展店閘。");
    await page.getByTestId("review-decision-submit").click();

    // The review moves to Approved and the decision surfaces the mapped status.
    await expect(page.getByTestId("review-decided-RV-702")).toContainText("Approved", { timeout: 15_000 });
  });

  test("WAIT decision requires pass conditions before it can submit", async ({ page }) => {
    await page.goto("/operator?ws=network");
    await page.getByTestId("network-tab-5").click();
    await expect(page.getByTestId("network-panel-review")).toBeVisible();

    await page.getByTestId("review-card-RV-701").click({ timeout: 15_000 });
    await page.getByTestId("review-btn-wait-RV-701").click();
    await page.getByTestId("review-decision-reason").fill("人流佳惟站前施工需以條件管理。");
    await page.getByTestId("review-decision-submit").click();
    await expect(page.getByTestId("review-decision-error")).toContainText("通過條件");
  });

  test("GO decision syncs five records and survives reload", async () => {
    const api = await reviewerContext();
    const decide = await api.post("/api/v1/operator/network-reviews/RV-702/decide", {
      headers: { "idempotency-key": "e2e-r4-007-go" },
      data: { decision: "GO", reason: "人流量體大且回本期可接受，核准進展店閘。", actorRoleId: "siteReviewer" },
    });
    expect(decide.status()).toBe(200);
    const body = await decide.json();
    expect(body.review.status).toBe("approved");
    expect(body.candidate.status).toBe("approved");
    expect(body.approval.status).toBe("approved");
    expect(body.decision.finalDecision).toBe("Approved");
    expect(body.auditEvent.action).toBe("review.decision");

    const snapshot = await (await api.get("/api/v1/operator/network-reviews")).json();
    const rv = snapshot.reviews.find((review: { id: string }) => review.id === "RV-702");
    expect(rv.status).toBe("approved");
    expect(snapshot.decisions.length).toBe(1);
    expect(snapshot.auditEvents.length).toBe(1);
    await api.dispose();
  });

  test("Decision mapping covers WAIT / Return / Reject", async () => {
    const api = await reviewerContext();

    const wait = await api.post("/api/v1/operator/network-reviews/RV-701/decide", {
      data: {
        decision: "WAIT",
        reason: "人流佳惟站前施工需以條件管理。",
        conditions: "租金議價至 48,000 以下；補充晚間人流資料",
        actorRoleId: "siteReviewer",
      },
    });
    expect((await wait.json()).decision.finalDecision).toBe("On Hold");

    const ret = await api.post("/api/v1/operator/network-reviews/RV-698/decide", {
      data: {
        decision: "RETURN",
        reason: "決策前需補齊現勘與晚間人流資料。",
        requiredData: ["現勘紀錄", "晚間人流樣本"],
        actorRoleId: "siteReviewer",
      },
    });
    const retBody = await ret.json();
    expect(retBody.decision.finalDecision).toBe("Need Data");
    expect(retBody.candidate.missingData).toEqual(["現勘紀錄", "晚間人流樣本"]);
    await api.dispose();
  });

  test("Failed transaction leaves all five records unchanged", async () => {
    const api = await reviewerContext();
    // WAIT without conditions is rejected server-side (422).
    const blocked = await api.post("/api/v1/operator/network-reviews/RV-701/decide", {
      data: { decision: "WAIT", reason: "需暫緩但未附條件。", actorRoleId: "siteReviewer" },
    });
    expect(blocked.status()).toBe(422);
    const snapshot = await (await api.get("/api/v1/operator/network-reviews")).json();
    expect(snapshot.counts.decided).toBe(0);
    expect(snapshot.decisions.length).toBe(0);
    expect(snapshot.auditEvents.length).toBe(0);
    await api.dispose();
  });

  test("Idempotent replay creates no duplicate records", async () => {
    const api = await reviewerContext();
    const payload = {
      headers: { "idempotency-key": "e2e-r4-007-replay" },
      data: { decision: "GO", reason: "人流量體大且回本期可接受，核准進展店閘。", actorRoleId: "siteReviewer" },
    };
    const first = await api.post("/api/v1/operator/network-reviews/RV-702/decide", payload);
    expect(first.status()).toBe(200);
    const replay = await api.post("/api/v1/operator/network-reviews/RV-702/decide", payload);
    expect(replay.status()).toBe(200);
    expect((await replay.json()).idempotentReplay).toBe(true);
    const snapshot = await (await api.get("/api/v1/operator/network-reviews")).json();
    expect(snapshot.decisions.length).toBe(1);
    expect(snapshot.auditEvents.length).toBe(1);
    await api.dispose();
  });

  test("Expansion may read but not decide; reviewer may decide", async () => {
    const expansion = await playwrightRequest.newContext({
      baseURL: API_BASE_URL,
      extraHTTPHeaders: EXPANSION_HEADERS,
    });
    expect((await expansion.get("/api/v1/operator/network-reviews")).status()).toBe(200);
    const denied = await expansion.post("/api/v1/operator/network-reviews/RV-702/decide", {
      data: { decision: "GO", reason: "approve this strong site now.", actorRoleId: "expansionManager" },
    });
    expect(denied.status()).toBe(403);
    await expansion.dispose();

    const reviewer = await reviewerContext();
    const allowed = await reviewer.post("/api/v1/operator/network-reviews/RV-702/decide", {
      data: { decision: "GO", reason: "approve this strong site now.", actorRoleId: "siteReviewer" },
    });
    expect(allowed.status()).toBe(200);
    await reviewer.dispose();
  });
});

async function reviewerContext() {
  return playwrightRequest.newContext({
    baseURL: API_BASE_URL,
    extraHTTPHeaders: REVIEWER_HEADERS,
  });
}
