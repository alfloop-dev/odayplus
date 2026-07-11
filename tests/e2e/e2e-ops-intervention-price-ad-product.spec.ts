import { expect, request as playwrightRequest, test } from "@playwright/test";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
const CORRELATION_ID = "corr-pv006-ops-intervention-price-ad";
const headers = {
  "x-correlation-id": CORRELATION_ID,
  "x-subject-id": "product-e2e-test",
  "x-roles": "finance_legal,expansion_user,operations_manager,regional_supervisor,site_reviewer,data_owner,auditor,executive,model_owner,release_owner,pricing_manager,marketing_manager",
};

test.setTimeout(90_000);

test("E2E-PV-006 operations, intervention, pricing, and AdLift product loop", async ({ page }, testInfo) => {
  const api = await playwrightRequest.newContext({ extraHTTPHeaders: headers });

  const forecast = await api.post(`${API_BASE_URL}/forecastops/forecast-jobs`, {
    data: {
      idempotency_key: "pv006-forecast-red-alert",
      prediction_origin_time: "2026-06-28T02:00:00Z",
      inputs: [
        forecastInput("pv006-store-red", 69_000, 120_000, "pv006-pos-red"),
        forecastInput("pv006-store-green", 118_000, 120_000, "pv006-pos-green"),
      ],
    },
  });
  expect(forecast.status()).toBe(202);
  const forecastPayload = await forecast.json();
  expect(forecastPayload.status).toBe("succeeded");
  expect(forecastPayload.forecasts).toHaveLength(2);
  expect(forecastPayload.alerts.map((alert: { alert_level: string }) => alert.alert_level)).toContain("red");
  expect(forecastPayload.handoffs[0].eligibility_status).toBeTruthy();

  const intervention = await openIntervention(api);
  const interventionId = intervention.intervention_id as string;
  expect(intervention.status).toBe("CANDIDATE");

  await expectStatus(api.post(`${API_BASE_URL}/interventions/${interventionId}/eligibility`, {
    data: { eligible: true, actor: "pv006-policy", reasons: ["red ForecastOps alert has fresh telemetry"] },
  }), 200);
  await expectStatus(api.post(`${API_BASE_URL}/interventions/${interventionId}/action`, {
    data: {
      actor: "pv006-ops-builder",
      action_spec: { action: "maintenance_recovery", linked_forecast_run: forecastPayload.job_id },
    },
  }), 200);
  const conflict = await api.post(`${API_BASE_URL}/interventions/${interventionId}/conflict-check`, {
    data: { actor: "pv006-conflict-engine", allow_overlap: false },
  });
  expect(conflict.status()).toBe(200);
  expect((await conflict.json()).conflict.blocks_approval).toBe(false);
  await expectStatus(api.post(`${API_BASE_URL}/interventions/${interventionId}/submit`, {
    data: { actor: "pv006-ops-manager" },
  }), 200);
  await expectStatus(api.post(`${API_BASE_URL}/interventions/${interventionId}/approve`, {
    data: {
      action: "APPROVE",
      actor: "pv006-ops-director",
      reason: "Approve maintenance recovery because the red alert has no overlapping treatment.",
    },
  }), 200);
  await expectStatus(api.post(`${API_BASE_URL}/interventions/${interventionId}/execute`, {
    data: { executor: "pv006-job-runner", executed_at: "2026-05-01T00:00:00Z" },
  }), 200);
  await expectStatus(api.post(`${API_BASE_URL}/interventions/${interventionId}/outcomes`, {
    data: {
      actor: "pv006-causal-eval",
      incremental_revenue: 82_000,
      incremental_gross_margin: 31_000,
      has_control_group: true,
      pretrend_status: "PASS",
      treatment_store_count: 1,
      control_store_count: 3,
      evaluation_method: "DID",
      measurement_method: "panel_did",
    },
  }), 200);
  const interventionEffect = await api.post(`${API_BASE_URL}/interventions/${interventionId}/evaluate`, {
    data: { actor: "pv006-causal-eval", replicated: false, now: "2026-06-15T00:00:00Z" },
  });
  expect(interventionEffect.status()).toBe(200);
  const interventionEffectPayload = await interventionEffect.json();
  expect(interventionEffectPayload.status).toBe("COMPLETED");
  expect(interventionEffectPayload.effect.can_claim_causal).toBe(true);
  expect(interventionEffectPayload.label.is_mature).toBe(true);

  const priceJob = await api.post(`${API_BASE_URL}/priceops/optimizer-jobs`, {
    data: {
      idempotency_key: "pv006-priceops-optimizer",
      optimized_at: "2026-06-28T03:00:00Z",
      plans: [
        {
          tenant_id: "oday-tw",
          plan_id: "pv006-price-plan-rollback",
          items: [priceItem("pv006-price-item-1", "pv006-store-red", 168, 72, -1.35)],
        },
      ],
    },
  });
  expect(priceJob.status()).toBe(202);
  const priceJobPayload = await priceJob.json();
  expect(priceJobPayload.status).toBe("succeeded");
  expect(priceJobPayload.hard_constraint_violation_count).toBe(0);
  const planId = priceJobPayload.plans[0].plan_id as string;

  await expectStatus(api.post(`${API_BASE_URL}/priceops/plans/${planId}/submit`, {
    data: { actor: "pv006-pricing-manager", reason: "Safe constrained plan ready for pilot" },
  }), 200);
  await expectStatus(api.post(`${API_BASE_URL}/priceops/plans/${planId}/approve`, {
    data: {
      actor_id: "pv006-pricing-director",
      reason: "Approve pilot with rollback plan and bounded max delta.",
      decision: "approved",
      approved_at: "2026-06-28T03:05:00Z",
    },
  }), 200);
  const activation = await api.post(`${API_BASE_URL}/priceops/plans/${planId}/activate`, {
    data: {
      executor: "pv006-price-publisher",
      executed_at: "2026-06-28T03:10:00Z",
      label_maturity_time: "2026-07-26T03:10:00Z",
    },
  });
  expect(activation.status()).toBe(200);
  const activationPayload = await activation.json();
  expect(activationPayload.rollback_plan.reverts[0].revert_to_price).toBe(168);
  expect(activationPayload.handoff.treatments.length).toBeGreaterThan(0);

  await expectStatus(api.post(`${API_BASE_URL}/priceops/plans/${planId}/observation`, {
    data: {
      actor: "pv006-pricing-manager",
      start_time: "2026-06-28T03:10:00Z",
      stop_conditions: { max_observation_days: 7, negative_impact_threshold: 0.05 },
    },
  }), 200);
  const priceEval = await api.post(`${API_BASE_URL}/priceops/plans/${planId}/evaluate`, {
    data: {
      actor: "pv006-pricing-analyst",
      actual_gross_margin: 1_000,
      measurement_method: "before_after",
      evidence_level: "medium",
      outcome_window_start: "2026-06-28T03:10:00Z",
      outcome_window_end: "2026-07-05T03:10:00Z",
      generated_at: "2026-07-05T04:00:00Z",
    },
  });
  expect(priceEval.status()).toBe(200);
  const priceEvalPayload = await priceEval.json();
  expect(priceEvalPayload.evaluation.rollback.recommended).toBe(true);
  expect(priceEvalPayload.plan.status).toBe("rollback");

  const pricePlan = await api.get(`${API_BASE_URL}/priceops/plans/${planId}`);
  expect(pricePlan.status()).toBe(200);
  const pricePlanPayload = await pricePlan.json();
  expect(pricePlanPayload.rollback_plan).toBeTruthy();
  expect(pricePlanPayload.execution.correlation_id).toBe(CORRELATION_ID);
  expect(pricePlanPayload.label_entries[0].status).toBe("registered");

  const adlift = await api.post(`${API_BASE_URL}/adlift/incrementality-jobs`, {
    data: {
      idempotency_key: "pv006-adlift-incrementality",
      generated_at: "2026-06-28T04:00:00Z",
      campaigns: [adCampaign()],
    },
  });
  expect(adlift.status()).toBe(202);
  const adliftPayload = await adlift.json();
  expect(adliftPayload.status).toBe("succeeded");
  expect(adliftPayload.reports[0].pre_trend_status).toBe("PASS");
  expect(adliftPayload.reports[0].causal_claim_allowed).toBe(true);
  expect(adliftPayload.reports[0].recommendation).toEqual(expect.stringMatching(/CONTINUE|SCALE/));

  const audit = await api.get(`${API_BASE_URL}/audit/events?correlation_id=${CORRELATION_ID}`);
  expect(audit.status()).toBe(200);
  const auditEvents = (await audit.json()).events as Array<{ event_type: string; action: string }>;
  expect(auditEvents.map((event) => event.event_type)).toEqual(expect.arrayContaining([
    "forecastops.forecasted.v1",
    "priceops.optimized.v1",
    "priceops.activated.v1",
    "priceops.evaluated.v1",
    "adlift.incrementality_evaluated.v1",
  ]));
  expect(auditEvents.map((event) => event.action)).toEqual(expect.arrayContaining([
    "run_model",
    "execute",
    "evaluate",
  ]));

  await page.goto("/w/operations/forecast/store-001");
  await expect(page.getByTestId("ops-store-detail-page")).toBeVisible();
  await expect(page.getByTestId("audit-metadata")).toContainText("four-light-policy-v1");
  await expect(page.getByTestId("handoff-panel")).toContainText("handoff-9001");

  await page.goto("/interventions?selected=int-3002&drawer=case");
  await expect(page.getByTestId("intervention-conflict-block")).toContainText("Conflict blocks approval execution");
  await expect(page.getByTestId("intervention-approval-panel")).toContainText("decision_id dec-int-3002-pending");

  await page.goto("/pricing?selected=price-5102&drawer=plan");
  await expect(page.getByTestId("priceops-constraint")).toContainText("HARD_CONSTRAINT_FAILED");
  await expect(page.getByRole("button", { name: "核准此調價方案" })).toBeDisabled();
  await expect(page.getByTestId("priceops-rollback")).toContainText("Rollback");

  await page.goto("/adlift?selected=adlift-8801&drawer=report");
  await expect(page.getByTestId("adlift-report-card")).toContainText("Control stores");
  await expect(page.getByTestId("adlift-claim-guard")).toContainText("causal incrementality claim allowed");
  await testInfo.attach("pv006-ops-price-ad-evidence", {
    body: await page.screenshot({ fullPage: true }),
    contentType: "image/png",
  });

  await api.dispose();
});

async function expectStatus(responsePromise: Promise<import("@playwright/test").APIResponse>, status: number) {
  const response = await responsePromise;
  expect(response.status()).toBe(status);
  return response.json();
}

async function openIntervention(api: Awaited<ReturnType<typeof playwrightRequest.newContext>>) {
  const response = await api.post(`${API_BASE_URL}/interventions`, {
    data: {
      store_id: "pv006-store-red",
      kind: "MAINTENANCE",
      trigger_ref: "pv006-forecast-red-alert",
      expected_outcome: "Recover gross margin and machine uptime after red alert.",
      planned_start: "2026-05-01T00:00:00Z",
      planned_end: "2026-05-02T00:00:00Z",
      created_by: "pv006-ops-manager",
      action_spec: { root_cause: "machine_uptime", source: "forecastops" },
      idempotency_key: "pv006-intervention-open",
    },
  });
  expect(response.status()).toBe(201);
  return response.json();
}

function forecastInput(storeId: string, actualRevenue: number, baselineP50: number, snapshotId: string) {
  return {
    store_id: storeId,
    horizon_days: 28,
    target_metric: "revenue",
    observations: Array.from({ length: 7 }, (_, index) => ({
      business_date: `2026-06-${String(21 + index).padStart(2, "0")}`,
      actual_revenue: actualRevenue - (6 - index) * 500,
      machine_cycles: Math.round((actualRevenue / 1000) + index * 2),
      site_score_baseline_p50: baselineP50,
      data_quality_score: 0.96,
      source_snapshot_ids: [snapshotId],
    })),
  };
}

function priceItem(
  itemId: string,
  storeId: string,
  currentPrice: number,
  unitCost: number,
  elasticityValue: number,
) {
  return {
    item_id: itemId,
    store_id: storeId,
    machine_type: "dinner_combo",
    unit_cost: unitCost,
    current_price: currentPrice,
    baseline_demand: 1_200,
    elasticity_value: elasticityValue,
    confidence: 0.92,
    max_increase_pct: 0.12,
    max_decrease_pct: 0.08,
    price_ladder_step: 1,
    min_price: 150,
    max_price: 188,
    prediction_origin_time: "2026-06-28T03:00:00Z",
  };
}

function adCampaign() {
  const treatment = ["pv006-store-red"];
  const controls = ["pv006-control-a", "pv006-control-b", "pv006-control-c"];
  return {
    campaign_id: "pv006-adlift-campaign",
    name: "PV006 dinner recovery paid search",
    treatment_store_ids: treatment,
    candidate_control_store_ids: controls,
    pre_period_start: "2026-06-01",
    pre_period_end: "2026-06-07",
    campaign_period_start: "2026-06-08",
    campaign_period_end: "2026-06-14",
    ad_spend: 18_000,
    channel: "paid_search",
    campaign_intervention_id: "pv006-adlift",
    observations: [
      ...storeMetrics(treatment[0], 100_000, 128_000, 0, "pv006-adlift"),
      ...controls.flatMap((storeId, index) => storeMetrics(storeId, 99_000 + index * 500, 100_000 + index * 600)),
    ],
  };
}

function storeMetrics(storeId: string, preRevenue: number, postRevenue: number, spend = 0, campaignId?: string) {
  const pre = Array.from({ length: 7 }, (_, index) => ({
    store_id: storeId,
    business_date: `2026-06-${String(1 + index).padStart(2, "0")}`,
    revenue: preRevenue + index * 100,
    gross_margin: Math.round((preRevenue + index * 100) * 0.42),
    ad_spend: 0,
    active_intervention_ids: [],
    source_snapshot_ids: ["pv006-adlift-pre"],
  }));
  const post = Array.from({ length: 7 }, (_, index) => ({
    store_id: storeId,
    business_date: `2026-06-${String(8 + index).padStart(2, "0")}`,
    revenue: postRevenue + index * 150,
    gross_margin: Math.round((postRevenue + index * 150) * 0.42),
    ad_spend: spend / 7,
    active_intervention_ids: campaignId ? [campaignId] : [],
    source_snapshot_ids: ["pv006-adlift-post"],
  }));
  return [...pre, ...post];
}
