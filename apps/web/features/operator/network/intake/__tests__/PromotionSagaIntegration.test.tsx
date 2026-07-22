import React, { useSyncExternalStore } from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AssistedIntake, IntakeInboxPage } from "@oday-plus/openapi-client";
import { AssistedIntakeSection } from "../AssistedIntakeSection";

// Integration coverage for the Candidate promotion saga
// (ODP-INTAKE-UX-PROMOTION-001, review round 2).
//
// These tests mount the REAL operator container (AssistedIntakeSection → the
// live detail dialog → PromotionReviewPanel → SiteScoreJobStatus) and drive it
// against a stubbed `fetch`, so every assertion below is about what actually
// leaves the generated @oday-plus/openapi-client on the wire:
//
//   1. POST /api/v1/intakes/{id}/promotion-requests   (explicit request)
//   2. POST /api/v1/promotion-decisions/{id}/actions/review (second actor)
//   3. GET  /api/v1/promotion-decisions/{id}          (lost-response lookup)
//   4. POST /api/v1/jobs/{id}/retry                   (authorized replay)
//
// Nothing here mocks the intake feature's own modules — the only test double
// is the network boundary.

// ---------------------------------------------------------------------------
// Stateful next/navigation mock: AssistedIntakeSection routes its dialogs
// through URL state, so router.replace must actually re-render the tree.
// ---------------------------------------------------------------------------
const nav = vi.hoisted(() => {
  const state = { search: "", listeners: new Set<() => void>() };
  return {
    state,
    reset() {
      state.search = "";
      state.listeners.clear();
    },
    replace(url: string) {
      state.search = url.includes("?") ? url.slice(url.indexOf("?") + 1) : "";
      for (const listener of state.listeners) listener();
    },
  };
});

vi.mock("next/navigation", async () => {
  return {
    useRouter: () => ({ replace: nav.replace, push: nav.replace }),
    usePathname: () => "/operator/network",
    useSearchParams: () => {
      const search = useSyncExternalStore(
        (cb) => {
          nav.state.listeners.add(cb);
          return () => nav.state.listeners.delete(cb);
        },
        () => nav.state.search,
        () => nav.state.search,
      );
      return new URLSearchParams(search);
    },
  };
});

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------
const INTAKE_ID = "11111111-1111-4111-8111-111111111111";
const DECISION_ID = "22222222-2222-4222-8222-222222222222";
const SCORE_JOB_ID = "33333333-3333-4333-8333-333333333333";
const CANDIDATE_ID = "44444444-4444-4444-8444-444444444444";

function readyIntake(overrides: Partial<AssistedIntake> = {}): AssistedIntake {
  return {
    id: INTAKE_ID,
    sourceId: "src-591",
    canonicalUrl: "https://www.591.com.tw/rent-1.html",
    originalUrl: "https://www.591.com.tw/rent-1.html",
    stage: "READY",
    policy: "APPROVED_RETRIEVAL",
    policyLabel: "核准來源",
    policyReason: "來源在核准清單",
    // Proposer differs from the console operator so second-actor review is open.
    submitter: "expansion-staff-lin",
    owner: "expansion-staff-lin",
    heatZoneId: null,
    rawSnapshot: null,
    snapshotId: null,
    capturedAt: "2026-07-22T10:00:00Z",
    parserVersion: "parser-v1",
    correlationId: "corr-fixture",
    parsedFields: {},
    matchResult: null,
    auditEvents: [],
    version: 3,
    ...overrides,
  };
}

function inboxPage(record: AssistedIntake): IntakeInboxPage {
  return {
    items: [record],
    total: 1,
    page: 1,
    pageSize: 10,
    counts: { needsReview: 0, awaitingEntry: 0, processing: 0, blocked: 0, ready: 1 },
    evidenceState: "complete",
  };
}

function pendingReviewReceipt() {
  return {
    promotion_decision_id: DECISION_ID,
    intake_id: INTAKE_ID,
    listing_id: "LST-440",
    status: "PENDING_REVIEW" as const,
    decision_type: "STANDARD" as const,
    reviewer_subject_id: null,
    candidate_site_id: null,
    site_score_job_id: null,
    version: 1,
    audit_event_id: "AE-PROMO-1",
    correlation_id: "corr-promo-1",
  };
}

function scoreFailedReceipt() {
  return {
    ...pendingReviewReceipt(),
    status: "SCORE_FAILED" as const,
    reviewer_subject_id: "expansion-manager",
    candidate_site_id: CANDIDATE_ID,
    site_score_job_id: SCORE_JOB_ID,
    version: 6,
  };
}

type CapturedRequest = {
  method: string;
  url: string;
  path: string;
  headers: Record<string, string>;
  body: any;
};

/**
 * Network stub for the routes this flow touches. Each handler can be replaced
 * per test (e.g. to fail once with a network error). Every request is captured
 * with its headers and parsed body so the tests assert the actual wire shape.
 */
function buildFetchStub(record: AssistedIntake) {
  const captured: CapturedRequest[] = [];
  const routes: Record<string, (req: CapturedRequest) => Response | Promise<Response>> = {};

  function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
    return new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json", ...headers },
    });
  }

  routes[`GET /api/v1/operator/network-listings/intake`] = () => json(inboxPage(record));
  routes[`GET /api/v1/operator/network-listings/intake/${INTAKE_ID}`] = () =>
    json({ ...record, version: record.version + 1 });
  routes[`POST /api/v1/intakes/${INTAKE_ID}/promotion-requests`] = () =>
    json(pendingReviewReceipt(), 202, { "Idempotency-Replayed": "false", ETag: 'W/"1"' });
  routes[`POST /api/v1/promotion-decisions/${DECISION_ID}/actions/review`] = () =>
    json(scoreFailedReceipt(), 200, { "Idempotency-Replayed": "false", ETag: 'W/"6"' });
  routes[`GET /api/v1/promotion-decisions/${DECISION_ID}`] = () =>
    json(scoreFailedReceipt(), 200, { ETag: 'W/"6"' });
  routes[`POST /api/v1/jobs/${SCORE_JOB_ID}/retry`] = () =>
    json(
      {
        job_id: SCORE_JOB_ID,
        status: "QUEUED",
        checkpoint: "SCORE_QUEUED",
        attempt: 2,
        version: 2,
        correlation_id: "corr-promo-1",
      },
      202,
      { "Idempotency-Replayed": "false" },
    );

  const fetchStub = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const path = new URL(url, "http://localhost").pathname;
    const method = (init?.method ?? "GET").toUpperCase();
    const headers: Record<string, string> = {};
    for (const [key, value] of Object.entries((init?.headers ?? {}) as Record<string, string>)) {
      headers[key.toLowerCase()] = value;
    }
    const request: CapturedRequest = {
      method,
      url,
      path,
      headers,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    };
    captured.push(request);
    const handler = routes[`${method} ${path}`];
    if (!handler) {
      return new Response(JSON.stringify({ detail: `no stub for ${method} ${path}` }), { status: 404 });
    }
    return handler(request);
  });

  return { captured, routes, fetchStub };
}

function requestsTo(captured: CapturedRequest[], method: string, path: string): CapturedRequest[] {
  return captured.filter((req) => req.method === method && req.path === path);
}

async function openPromotionPanel() {
  // Queue loads from the stubbed list endpoint, then the row opens the REAL
  // detail dialog through URL state.
  const row = await screen.findByTestId(`intake-inbox-row-${INTAKE_ID}`);
  fireEvent.click(row);
  await screen.findByTestId("intake-detail-dialog");
  // The promotion section renders once the gate snapshot hash is computed.
  await screen.findByTestId("promotion-review-panel");
  await screen.findByTestId("promotion-request-form");
}

function fillAndSubmitRequestForm() {
  fireEvent.change(screen.getByTestId("promotion-request-reason"), {
    target: { value: "熱區缺口與租金符合門檻，申請晉升。" },
  });
  fireEvent.click(screen.getByTestId("promotion-request-ack"));
  fireEvent.click(screen.getByTestId("promotion-request-submit"));
}

beforeEach(() => {
  nav.reset();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("promotion saga — live operator route integration", () => {
  it("drives request → second-actor review → score replay through the generated client", async () => {
    const record = readyIntake();
    const { captured, fetchStub } = buildFetchStub(record);
    vi.stubGlobal("fetch", fetchStub);

    render(<AssistedIntakeSection activeRoleId="expansion-manager" />);
    await openPromotionPanel();

    // ---- 1. explicit promotion request -----------------------------------
    fillAndSubmitRequestForm();

    await waitFor(() => {
      expect(requestsTo(captured, "POST", `/api/v1/intakes/${INTAKE_ID}/promotion-requests`)).toHaveLength(1);
    });
    const requestCall = requestsTo(captured, "POST", `/api/v1/intakes/${INTAKE_ID}/promotion-requests`)[0];
    // Concurrency + idempotency are mandatory on the wire, not decorative.
    expect(requestCall.headers["if-match"]).toBe('W/"3"');
    expect(requestCall.headers["idempotency-key"]).toMatch(/^promotion-request-/);
    expect(requestCall.headers["x-correlation-id"]).toBeTruthy();
    expect(requestCall.body.target_format_code).toBe("FMT-STANDARD-STORE");
    expect(requestCall.body.reason).toContain("申請晉升");
    expect(requestCall.body.risk_acknowledged).toBe(true);
    expect(requestCall.body.gate_snapshot_sha256).toMatch(/^[a-f0-9]{64}$/);

    // Server receipt (not optimistic state) drives the UI to PENDING_REVIEW.
    await waitFor(() => {
      expect(screen.getByTestId("promotion-status-badge").textContent).toContain("PENDING_REVIEW");
    });

    // ---- 2. independent second-actor review ------------------------------
    // Proposer (submitter fixture) differs from this console operator.
    expect(screen.getByTestId("promotion-second-actor-ok")).toBeInTheDocument();
    fireEvent.change(screen.getByTestId("promotion-review-reason"), {
      target: { value: "已核對 gate snapshot，核准晉升。" },
    });
    fireEvent.click(screen.getByTestId("promotion-review-ack"));
    fireEvent.click(screen.getByTestId("promotion-approve-btn"));

    await waitFor(() => {
      expect(
        requestsTo(captured, "POST", `/api/v1/promotion-decisions/${DECISION_ID}/actions/review`),
      ).toHaveLength(1);
    });
    const reviewCall = requestsTo(
      captured,
      "POST",
      `/api/v1/promotion-decisions/${DECISION_ID}/actions/review`,
    )[0];
    expect(reviewCall.headers["if-match"]).toBe('W/"1"');
    expect(reviewCall.headers["idempotency-key"]).toMatch(/^promotion-review-/);
    expect(reviewCall.body).toMatchObject({ decision: "APPROVE", risk_acknowledged: true });

    // ---- 3. SCORE_FAILED: committed IDs shown, candidate retained --------
    await waitFor(() => {
      expect(screen.getByTestId("promotion-status-badge").textContent).toContain("SCORE_FAILED");
    });
    expect(screen.getByTestId("promotion-candidate-id").textContent).toBe(CANDIDATE_ID);
    expect(screen.getByTestId("promotion-score-job-id").textContent).toBe(SCORE_JOB_ID);
    expect(screen.getByTestId("candidate-retained-note")).toBeInTheDocument();

    // ---- 4. authorized replay from the durable checkpoint ----------------
    fireEvent.change(screen.getByTestId("sitescore-replay-reason"), {
      target: { value: "外部評分逾時已排除，授權重放。" },
    });
    fireEvent.click(screen.getByTestId("sitescore-replay-ack"));
    fireEvent.click(screen.getByTestId("sitescore-replay-btn"));

    await waitFor(() => {
      expect(requestsTo(captured, "POST", `/api/v1/jobs/${SCORE_JOB_ID}/retry`)).toHaveLength(1);
    });
    const retryCall = requestsTo(captured, "POST", `/api/v1/jobs/${SCORE_JOB_ID}/retry`)[0];
    expect(retryCall.headers["if-match"]).toBe('W/"1"');
    expect(retryCall.headers["idempotency-key"]).toMatch(/^sitescore-replay-/);
    expect(retryCall.body).toMatchObject({
      checkpoint: "SCORE_QUEUED",
      risk_acknowledged: true,
    });

    // The server's JobReceipt (attempt 2) replaces the bootstrap view.
    await waitFor(() => {
      expect(screen.getByTestId("sitescore-job-state").textContent).toContain("QUEUED");
      expect(screen.getByTestId("sitescore-job-attempt").textContent).toContain("2");
    });
  });

  it("recovers a lost request response by retrying with the SAME idempotency key", async () => {
    const record = readyIntake();
    const { captured, routes, fetchStub } = buildFetchStub(record);
    let failures = 1;
    const succeed = routes[`POST /api/v1/intakes/${INTAKE_ID}/promotion-requests`];
    routes[`POST /api/v1/intakes/${INTAKE_ID}/promotion-requests`] = (req) => {
      if (failures > 0) {
        failures -= 1;
        throw new TypeError("network dropped before response");
      }
      // Same durable receipt, flagged as a replay by the server.
      return new Response(JSON.stringify(pendingReviewReceipt()), {
        status: 200,
        headers: { "content-type": "application/json", "Idempotency-Replayed": "true" },
      });
    };
    void succeed;
    vi.stubGlobal("fetch", fetchStub);

    render(<AssistedIntakeSection activeRoleId="expansion-manager" />);
    await openPromotionPanel();

    fillAndSubmitRequestForm();

    // Transport loss is surfaced as "result unconfirmed", never as success.
    await screen.findByTestId("promotion-lost-response");
    const firstAttempt = requestsTo(captured, "POST", `/api/v1/intakes/${INTAKE_ID}/promotion-requests`)[0];

    fireEvent.click(screen.getByTestId("promotion-lost-retry-btn"));
    await waitFor(() => {
      expect(requestsTo(captured, "POST", `/api/v1/intakes/${INTAKE_ID}/promotion-requests`)).toHaveLength(2);
    });
    const secondAttempt = requestsTo(captured, "POST", `/api/v1/intakes/${INTAKE_ID}/promotion-requests`)[1];

    // The retry must reuse the SAME key — that is the no-duplicate guarantee.
    expect(secondAttempt.headers["idempotency-key"]).toBe(firstAttempt.headers["idempotency-key"]);

    // And the server-flagged replay is labeled to the operator.
    await screen.findByTestId("idempotency-replayed-indicator");
    await waitFor(() => {
      expect(screen.getByTestId("promotion-status-badge").textContent).toContain("PENDING_REVIEW");
    });
  });

  it("recovers a lost review response via decision lookup without resending", async () => {
    const record = readyIntake();
    const { captured, routes, fetchStub } = buildFetchStub(record);
    let reviewFailures = 1;
    routes[`POST /api/v1/promotion-decisions/${DECISION_ID}/actions/review`] = () => {
      if (reviewFailures > 0) {
        reviewFailures -= 1;
        throw new TypeError("network dropped before response");
      }
      throw new Error("review must NOT be resent by the lookup path");
    };
    vi.stubGlobal("fetch", fetchStub);

    render(<AssistedIntakeSection activeRoleId="expansion-manager" />);
    await openPromotionPanel();

    fillAndSubmitRequestForm();
    await waitFor(() => {
      expect(screen.getByTestId("promotion-status-badge").textContent).toContain("PENDING_REVIEW");
    });

    fireEvent.change(screen.getByTestId("promotion-review-reason"), {
      target: { value: "已核對 gate snapshot，核准晉升。" },
    });
    fireEvent.click(screen.getByTestId("promotion-review-ack"));
    fireEvent.click(screen.getByTestId("promotion-approve-btn"));

    await screen.findByTestId("promotion-lost-response");
    fireEvent.click(screen.getByTestId("promotion-lookup-btn"));

    await waitFor(() => {
      expect(requestsTo(captured, "GET", `/api/v1/promotion-decisions/${DECISION_ID}`)).toHaveLength(1);
    });
    // Lookup resolved the saga state — one review POST attempt, no resend.
    expect(
      requestsTo(captured, "POST", `/api/v1/promotion-decisions/${DECISION_ID}/actions/review`),
    ).toHaveLength(1);
    await waitFor(() => {
      expect(screen.getByTestId("promotion-status-badge").textContent).toContain("SCORE_FAILED");
    });
  });
});
