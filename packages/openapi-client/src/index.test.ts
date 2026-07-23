import { describe, expect, it } from "vitest";

import { OdpApiClient, OdpApiError } from "./index";

describe("OdpApiClient canonical error metadata", () => {
  it("preserves the assisted-intake v1 error without status-derived substitutes", async () => {
    const serverError = {
      code: "VERSION_CONFLICT",
      message: "The intake changed after review started.",
      retryable: false,
      correlation_id: "8df98dc6-54af-4ffc-a58f-05225f8d9a12",
      reason_code: "STALE_REVIEW",
      current_version: 9,
      current_state: "NEEDS_REVIEW",
      retry_after_seconds: null,
      occurred_at: "2026-07-23T08:09:10.111Z",
      next_action: "REFRESH",
    } as const;
    const client = new OdpApiClient({
      baseUrl: "https://api.example.test",
      fetchImpl: async () =>
        new Response(JSON.stringify(serverError), {
          status: 409,
          headers: {
            "content-type": "application/json",
            "x-correlation-id": "header-must-not-override-body",
          },
        }),
    });

    const error = await client
      .getIntake("a71a6121-bca0-4303-b8a0-f7c1e264f8c9")
      .then(
        () => undefined,
        (caught: unknown) => caught,
      );

    expect(error).toBeInstanceOf(OdpApiError);
    expect(error).toMatchObject({
      status: 409,
      code: serverError.code,
      detail: serverError.message,
      retryable: serverError.retryable,
      correlationId: serverError.correlation_id,
      reasonCode: serverError.reason_code,
      currentVersion: serverError.current_version,
      currentState: serverError.current_state,
      retryAfterSeconds: serverError.retry_after_seconds,
      occurredAt: serverError.occurred_at,
      nextAction: serverError.next_action,
    });
  });

  it("preserves the compatibility envelope when the v1 body is not present", async () => {
    const client = new OdpApiClient({
      baseUrl: "https://api.example.test",
      fetchImpl: async () =>
        new Response(
          JSON.stringify({
            detail: "Access denied",
            error: {
              code: "forbidden",
              message: "Access denied",
              next_action: "Request access.",
              occurred_at: "2026-07-23T08:10:11.222Z",
              details: [],
              correlation_id: "corr-server",
            },
          }),
          { status: 403, headers: { "content-type": "application/json" } },
        ),
    });

    const error = await client.health().then(
      () => undefined,
      (caught: unknown) => caught,
    );

    expect(error).toMatchObject({
      code: "forbidden",
      detail: "Access denied",
      correlationId: "corr-server",
      occurredAt: "2026-07-23T08:10:11.222Z",
      nextAction: "Request access.",
    });
  });

  it("uses canonical keyset list and detail routes instead of the legacy facade", async () => {
    const urls: string[] = [];
    const client = new OdpApiClient({
      baseUrl: "https://api.example.test",
      fetchImpl: async (input) => {
        urls.push(String(input));
        return new Response(
          JSON.stringify(
            urls.length === 1
              ? {
                  items: [
                    {
                      intake_id: "intake-1",
                      state: "READY",
                      intake_method: "URL",
                      source_id: "source-1",
                      original_url: "https://example.test/listing/1?utm_source=test",
                      canonical_url: "https://example.test/listing/1",
                      policy_state: "APPROVED_RETRIEVAL",
                    },
                  ],
                  next_cursor: null,
                  page_size: 25,
                  total_count: 0,
                  total_count_accuracy: "EXACT",
                  snapshot_time: "2026-07-23T08:10:11.222Z",
                  query_fingerprint: "sha256:test",
                }
              : { intake_id: "intake-1" },
          ),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      },
    });

    const page = await client.listIntakes({
      cursor: "signed.cursor",
      page_size: 25,
      status: ["READY", "NEEDS_REVIEW"],
      intake_method: ["URL", "MANUAL"],
      owner_subject_id: ["owner-1"],
      assignment_status: ["CLAIMED"],
      assigned: true,
      sla_state: ["OVERDUE"],
      observed_from: "2026-07-22T00:00:00Z",
      updated_to: "2026-07-23T23:59:59Z",
      restricted_data: true,
      quarantined: false,
      failed: false,
      retryable: true,
      saved_view_id: "saved-view-1",
    });
    await client.getIntake("intake-1");

    expect(urls[0]).toContain("/api/v1/intakes?");
    expect(urls[0]).toContain("cursor=signed.cursor");
    expect(urls[0]).toContain("status=READY");
    expect(urls[0]).toContain("status=NEEDS_REVIEW");
    expect(urls[0]).toContain("intake_method=URL");
    expect(urls[0]).toContain("intake_method=MANUAL");
    expect(urls[0]).toContain("owner_subject_id=owner-1");
    expect(urls[0]).toContain("assignment_status=CLAIMED");
    expect(urls[0]).toContain("assigned=true");
    expect(urls[0]).toContain("sla_state=OVERDUE");
    expect(urls[0]).toContain("restricted_data=true");
    expect(urls[0]).toContain("retryable=true");
    expect(urls[0]).toContain("saved_view_id=saved-view-1");
    expect(urls[0]).not.toContain("/operator/network-listings/intake");
    expect(urls[1]).toBe("https://api.example.test/api/v1/intakes/intake-1");
    expect(page.items[0]).toMatchObject({
      original_url: "https://example.test/listing/1?utm_source=test",
      canonical_url: "https://example.test/listing/1",
      policy_state: "APPROVED_RETRIEVAL",
    });
  });

  it("exposes typed bootstrap, saved-view, and server assignment commands", async () => {
    const requests: Array<{ url: string; method: string }> = [];
    const client = new OdpApiClient({
      baseUrl: "https://api.example.test",
      fetchImpl: async (input, init) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        requests.push({ url, method });
        const body = url.endsWith("/intakes/bootstrap")
          ? {
              tenant_id: "tenant-1",
              subject_id: "subject-1",
              role_mode: "expansion-manager",
              scope: {
                tenant_id: "tenant-1",
                brand_ids: [],
                region_ids: [],
                assigned_area_ids: [],
                heat_zone_ids: [],
              },
              heat_zones: [],
              selected_heat_zone_id: null,
              intake_methods: ["URL"],
              intake_states: ["READY"],
              match_outcomes: ["NEW"],
              assignment_states: ["CLAIMED"],
              sla_states: ["ON_TRACK"],
              saved_views: [],
              commands: {},
            }
          : url.endsWith("/saved-views") && method === "POST"
            ? {
                saved_view_id: "saved-view-created",
                name: "北區待覆核",
                query: { status: ["NEEDS_REVIEW"] },
                resource: "intake",
                shared_role: null,
                visibility: "PRIVATE",
                owner_subject_id: "subject-1",
                created_at: "2026-07-23T00:00:00Z",
                version: 1,
              }
          : url.endsWith("/saved-views")
            ? []
            : {
                assignment_id: "assignment-1",
                status: "CLAIMED",
                owner_subject_id: "subject-1",
                due_at: "2026-07-24T00:00:00Z",
                version: 2,
                audit_event_id: "audit-1",
              };
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      },
    });

    await client.getIntakeInboxBootstrap();
    await client.listSavedViews();
    const created = await client.createSavedView(
      {
        name: "北區待覆核",
        query: { status: ["NEEDS_REVIEW"] },
        resource: "intake",
        visibility: "PRIVATE",
      },
      { idempotencyKey: "typed-saved-view-command-0001" },
    );
    await client.claimAssignment(
      "assignment-1",
      { reason: "Claim canonical Inbox work." },
      {
        idempotencyKey: "typed-claim-command-0001",
        ifMatch: 'W/"1"',
      },
    );

    expect(requests).toEqual([
      {
        url: "https://api.example.test/api/v1/intakes/bootstrap",
        method: "GET",
      },
      {
        url: "https://api.example.test/api/v1/saved-views",
        method: "GET",
      },
      {
        url: "https://api.example.test/api/v1/saved-views",
        method: "POST",
      },
      {
        url: "https://api.example.test/api/v1/assignments/assignment-1/actions/claim",
        method: "POST",
      },
    ]);
    expect(created.saved_view_id).toBe("saved-view-created");
  });

  it("submits structured intake through the canonical typed batch contract", async () => {
    const requests: Array<{ url: string; method: string }> = [];
    const client = new OdpApiClient({
      baseUrl: "https://api.example.test",
      fetchImpl: async (input, init) => {
        requests.push({ url: String(input), method: init?.method ?? "GET" });
        return new Response(
          JSON.stringify({
            batch_id: "00000000-0000-0000-0000-000000000001",
            submitted_at: "2026-07-23T12:00:00Z",
            accepted_count: 1,
            rejected_count: 0,
            rows: [
              {
                row_index: 1,
                status: "ACCEPTED",
                intake_id: "00000000-0000-0000-0000-000000000002",
              },
            ],
            correlation_id: "00000000-0000-0000-0000-000000000003",
          }),
          { status: 202, headers: { "content-type": "application/json" } },
        );
      },
    });

    await client.submitIntakeBatch(
      {
        batch_id: "00000000-0000-0000-0000-000000000001",
        method: "MANUAL",
        scope: { tenant_id: "00000000-0000-0000-0000-000000000004" },
        rows: [{ address_raw: "台北市信義區測試路 1 號" }],
      },
      { idempotencyKey: "typed-batch-command-0001" },
    );

    expect(requests).toEqual([
      {
        url: "https://api.example.test/api/v1/intake-batches",
        method: "POST",
      },
    ]);
  });
});
