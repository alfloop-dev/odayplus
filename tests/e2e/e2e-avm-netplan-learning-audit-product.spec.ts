import { expect, request as playwrightRequest, test } from "@playwright/test";

const API_BASE_URL = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
const CORRELATION_ID = "corr-pv007-avm-netplan-learning-audit";
const headers = {
  "x-correlation-id": CORRELATION_ID,
  "x-subject-id": "product-e2e-test",
  "x-roles": "finance_legal,expansion_user,operations_manager,regional_supervisor,site_reviewer,data_owner,auditor,executive,model_owner,release_owner,pricing_manager,marketing_manager",
};

test.setTimeout(120_000);

test("E2E-PV-007 AVM, NetPlan, Learning Hub, and Audit product loop", async ({ page }, testInfo) => {
  const api = await playwrightRequest.newContext({ extraHTTPHeaders: headers });

  const avmCase = await api.post(`${API_BASE_URL}/avm/cases`, {
    data: {
      store_id: "pv007-store-avm",
      gm_ttm: 420_000,
      forecast_gm_next_12m: 455_000,
      asset_book_value: 240_000,
      equipment_fair_value: 130_000,
      lease_liability: 38_000,
      working_capital: 54_000,
      comparable_multiples: [2.8, 3.1, 3.4],
      liquidity_discount: 0.08,
      quality_score: 0.93,
      source_snapshot_ids: ["pv007-avm-finance-snapshot"],
      prediction_origin_time: "2026-06-28T01:00:00Z",
      created_by: "pv007-deal-ops",
      idempotency_key: "pv007-avm-case",
    },
  });
  expect(avmCase.status()).toBe(201);
  const avmCasePayload = await avmCase.json();
  const caseId = avmCasePayload.case_id as string;

  await expectStatus(api.post(`${API_BASE_URL}/avm/cases/${caseId}/normalize`, {
    data: { actor: "pv007-finance-analyst" },
  }), 200);
  const valuation = await expectStatus(api.post(`${API_BASE_URL}/avm/cases/${caseId}/value`, {
    data: { actor: "pv007-valuation-model" },
  }), 200);
  expect(valuation.fair_price.p50).toBeGreaterThan(0);
  const approval = await expectStatus(api.post(`${API_BASE_URL}/avm/cases/${caseId}/finance-approval`, {
    data: {
      actor: "pv007-finance-director",
      reason: "Approve reserve after normalized margin and three-lens valuation review.",
      reserve_price: valuation.reserve_price,
    },
  }), 200);
  expect(approval.finance_approval.decision_reason).toContain("Approve reserve");
  const dataroom = await expectStatus(api.post(`${API_BASE_URL}/avm/cases/${caseId}/dataroom`, {
    data: { actor: "pv007-deal-ops" },
  }), 200);
  expect(dataroom.checklist.length).toBeGreaterThan(0);
  const dataroomExport = await expectStatus(api.post(`${API_BASE_URL}/avm/cases/${caseId}/dataroom/export`, {
    data: {
      actor: "pv007-deal-ops",
      reason: "Export package for subsidy and diligence audit trail.",
    },
  }), 200);
  expect(dataroomExport.export_audit[0].exported_at).toBeTruthy();

  const scenario = await api.post(`${API_BASE_URL}/netplan/scenarios`, {
    data: netPlanScenario(),
  });
  expect(scenario.status()).toBe(201);
  const scenarioPayload = await scenario.json();
  expect(scenarioPayload.status).toBe("draft");
  const scenarioId = scenarioPayload.scenario_id as string;

  const solve = await expectStatus(api.post(`${API_BASE_URL}/netplan/scenarios/${scenarioId}/solve`, {
    data: {
      actor: "pv007-netplan-solver",
      reason: "Constrained expansion solve with budget, risk, and capacity gates.",
      solved_at: "2026-06-28T02:00:00Z",
      alternative_limit: 3,
    },
  }), 200);
  expect(solve.result.solver_status).toMatch(/optimal|feasible/);
  expect(solve.result.selected_actions.length).toBeGreaterThan(0);
  await expectStatus(api.post(`${API_BASE_URL}/netplan/scenarios/${scenarioId}/submit`, {
    data: { actor: "pv007-network-planner", reason: "Submit solver recommendation for governance approval." },
  }), 200);
  const netplanApproval = await expectStatus(api.post(`${API_BASE_URL}/netplan/scenarios/${scenarioId}/decide`, {
    data: {
      actor_id: "pv007-network-committee",
      reason: "Approve because the plan satisfies hard constraints and keeps rollback capacity.",
      decision: "approved",
      decided_at: "2026-06-28T02:10:00Z",
    },
  }), 200);
  expect(netplanApproval.decision).toBe("approved");
  const execution = await expectStatus(api.post(`${API_BASE_URL}/netplan/scenarios/${scenarioId}/execute`, {
    data: { executed_by: "pv007-network-ops", executed_at: "2026-06-28T02:20:00Z" },
  }), 200);
  expect(execution.actions.length).toBeGreaterThan(0);
  const outcome = await expectStatus(api.post(`${API_BASE_URL}/netplan/scenarios/${scenarioId}/outcomes`, {
    data: {
      actor: "pv007-label-writer",
      actual_gross_margin: solve.result.expected_gross_margin + 12_500,
      observed_at: "2026-07-28T02:20:00Z",
      source_snapshot_ids: ["pv007-netplan-outcome-snapshot"],
    },
  }), 200);
  expect(outcome.label_registry_payload.label_type).toBe("netplan_realized_gross_margin");
  const scenarioDetail = await expectStatus(api.get(`${API_BASE_URL}/netplan/scenarios/${scenarioId}`), 200);
  expect(scenarioDetail.execution.actions.length).toBeGreaterThan(0);
  expect(scenarioDetail.outcome.variance).toBeGreaterThan(0);

  // Solver returned an alternatives set for the side-by-side comparison view.
  expect(solve.result.alternative_plan_available).toBe(true);
  expect(solve.result.alternatives.length).toBeGreaterThan(0);
  // Comparison/list endpoint the NetPlan overview UI binds to (GET /netplan/scenarios).
  const scenarioList = await expectStatus(api.get(`${API_BASE_URL}/netplan/scenarios`), 200);
  const listedScenario = scenarioList.items.find(
    (item: { scenario_id: string }) => item.scenario_id === scenarioId,
  );
  expect(listedScenario).toBeTruthy();
  expect(listedScenario.status).toBe("outcome_observed");
  expect(listedScenario.solver_version).toBeTruthy();

  await registerLearningDataset(api);
  await registerLearningModelVersion(api, "2.3.0", {
    stage: "production",
    rollbackTarget: undefined,
    artifactContent: "pv007 champion model bytes",
    metrics: { precision_at_50: 0.81, auc: 0.87 },
  });
  await registerLearningModelVersion(api, "2.4.0", {
    stage: "dev",
    rollbackTarget: "2.3.0",
    artifactContent: "pv007 challenger model bytes",
    metrics: { precision_at_50: 0.88, auc: 0.91 },
  });
  const canary = await expectStatus(api.post(`${API_BASE_URL}/learninghub/releases`, {
    data: releasePayload("CANARY", "2.4.0", "2.3.0", "pv007-canary-approval"),
  }), 201);
  expect(canary.release_type).toBe("CANARY");
  const full = await expectStatus(api.post(`${API_BASE_URL}/learninghub/releases`, {
    data: releasePayload("FULL", "2.4.0", "2.3.0", "pv007-full-approval"),
  }), 201);
  expect(full.release_type).toBe("FULL");
  // Release monitor (ODP-FLOW-009): evaluate the FULL release's guardrails during
  // its monitoring window. A breach recommends — never auto-executes — a rollback,
  // and writes an audited learninghub.release_monitor.v1 event.
  const monitor = await expectStatus(api.post(`${API_BASE_URL}/learninghub/releases/${full.release_id}/monitor`, {
    data: {
      observed_metrics: { precision_at_50: 0.74 },
      guardrails: [{ metric_name: "precision_at_50", min_value: 0.8 }],
      evaluated_by: "pv007-release-monitor",
    },
  }), 201);
  expect(monitor.status).toBe("BREACHED");
  expect(monitor.recommended_action).toBe("ROLLBACK");
  expect(monitor.breaches[0].metric_name).toBe("precision_at_50");
  const rollback = await expectStatus(api.post(`${API_BASE_URL}/learninghub/releases`, {
    data: releasePayload("ROLLBACK", "2.4.0", "2.3.0", "pv007-rollback-approval"),
  }), 201);
  expect(rollback.to_version).toBe("2.3.0");
  // Release log endpoint the Learning Hub UI binds to (GET /learninghub/releases).
  const releaseList = await expectStatus(api.get(`${API_BASE_URL}/learninghub/releases`), 200);
  const listedFull = releaseList.items.find(
    (item: { release_id: string }) => item.release_id === full.release_id,
  );
  expect(listedFull).toBeTruthy();
  expect(listedFull.release_type).toBe("FULL");
  expect(releaseList.items.some((item: { release_type: string }) => item.release_type === "ROLLBACK")).toBe(true);
  const registryEvidence = await expectStatus(api.get(`${API_BASE_URL}/learninghub/models/sitescore-propensity/evidence`), 200);
  expect(registryEvidence.aliases.production).toBe("2.3.0");
  expect(registryEvidence.versions.find((version: { version: string }) => version.version === "2.4.0").artifacts[0].content_digest).toContain("sha256:");

  const auditEvents = await expectStatus(api.get(`${API_BASE_URL}/audit/events?correlation_id=${CORRELATION_ID}`), 200);
  const eventTypes = auditEvents.events.map((event: { event_type: string }) => event.event_type);
  expect(eventTypes).toEqual(expect.arrayContaining([
    "avm.valued.v1",
    "avm.dataroom_exported.v1",
    "netplan.solved.v1",
    "netplan.executed.v1",
    "netplan.outcome_observed.v1",
    "learninghub.model_release.v1",
    "learninghub.release_monitor.v1",
  ]));

  const evidenceExport = await api.post(`${API_BASE_URL}/audit/evidence/export`, {
    data: {
      program_id: "PV007-AUDIT",
      purpose: "product E2E evidence for AVM NetPlan Learning governance",
      requested_by: "pv007-audit-ops",
      from_time: "2026-06-28T00:00:00Z",
      to_time: "2026-07-29T00:00:00Z",
      correlation_ids: [CORRELATION_ID],
      export_scope: "avm-netplan-learning-audit",
      environment: "e2e",
      build_version: "pv007-local",
      data_classification: "restricted",
      sensitive: false,
      decision_cards: [
        decisionCard("decision-pv007-avm", "AVM", `avm/cases/${caseId}`, "APPROVED", dataroomExport.audit_event_id),
        decisionCard("decision-pv007-netplan", "NetPlan", `netplan/scenarios/${scenarioId}`, "EXECUTED", execution.audit_event_id),
        decisionCard("decision-pv007-learning", "LearningHub", "learninghub/models/sitescore-propensity:2.4.0", "ROLLED_BACK", rollback.audit_event_id),
      ],
    },
  });
  expect(evidenceExport.status()).toBe(201);
  const evidencePayload = await evidenceExport.json();
  expect(evidencePayload.decision_cards).toHaveLength(3);
  expect(evidencePayload.audit_events.length).toBeGreaterThanOrEqual(8);
  expect(evidencePayload.bundle_checksum).toMatch(/[a-f0-9]{64}/);
  expect(evidencePayload.missing_requirements).toEqual([]);

  const retained = await expectStatus(api.get(`${API_BASE_URL}/audit/evidence/exports?program_id=PV007-AUDIT`), 200);
  expect(retained.exports.some((item: { export_id: string }) => item.export_id === evidencePayload.export_id)).toBe(true);
  const retainedDetail = await expectStatus(api.get(`${API_BASE_URL}/audit/evidence/exports/${evidencePayload.export_id}`), 200);
  expect(retainedDetail.bundle.bundle_checksum).toBe(evidencePayload.bundle_checksum);

  await page.goto("/w/dealroom/cases/vc-5101");
  await expect(page.getByTestId("avm-case-detail-page")).toBeVisible();
  await expect(page.getByTestId("valuation-range-chart")).toContainText("Three-Lens Valuation");
  await expect(page.getByTestId("valuation-range-chart")).toContainText("MASKED_BY_PERMISSION");
  await expect(page.getByTestId("valuation-range-chart")).not.toContainText("17,654");
  await expect(page.getByTestId("valuation-range-chart")).not.toContainText("33,390");
  await expect(page.getByTestId("avm-reserve-marker")).toHaveCount(0);
  await expect(page.getByTestId("avm-asking-marker")).toHaveCount(0);
  await expect(page.getByTestId("avm-approval-panel")).toContainText("never optimistic");
  await expect(page.getByTestId("avm-dataroom")).toContainText("Valuation card");

  await page.goto("/netplan");
  // API-backed comparison region: bound to GET /netplan/scenarios with a
  // visible DataSourceBadge; renders live rows or a documented fixture fallback.
  await expect(page.getByTestId("netplan-live-scenarios")).toBeVisible();
  await expect(page.getByTestId("netplan-data-source")).toBeVisible();

  await page.goto("/w/network/scenarios/np-6201");
  await expect(page.getByTestId("netplan-scenario-card")).toContainText("Binding constraints");
  await expect(page.getByTestId("netplan-execution")).toContainText("Variance");
  await expect(page.getByTestId("netplan-approval-panel")).toContainText("never optimistic");

  await page.goto("/w/ai/models/sitescore-propensity/2.4.0");
  await expect(page.getByTestId("model-summary")).toContainText("Rollback target");
  await expect(page.getByTestId("model-card-section")).toContainText("privacy/security");
  await expect(page.getByTestId("validation-panel")).toContainText("precision_at_50");
  await expect(page.getByTestId("release-controller")).toContainText("Affected modules");
  await expect(page.getByTestId("rollback-console")).toContainText("rollback reason");
  await expect(page.getByTestId("learning-audit-metadata")).toContainText("correlation_id");

  await page.goto("/w/ai/releases");
  // API-backed release log: bound to GET /learninghub/releases with a visible
  // DataSourceBadge; renders live rows or a documented fixture fallback.
  await expect(page.getByTestId("learning-live-releases")).toBeVisible();
  await expect(page.getByTestId("learning-data-source")).toBeVisible();

  await page.goto("/w/audit/decisions/decision-netplan-404");
  await expect(page.getByTestId("audit-summary")).toContainText("override");
  await expect(page.getByTestId("decision-card")).toContainText("System Recommendation");
  await expect(page.getByTestId("override-comparison")).toContainText("override_reason");
  await expect(page.getByTestId("evidence-export-panel")).toContainText("no optimistic export state");
  await testInfo.attach("pv007-avm-netplan-learning-audit-evidence", {
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

function netPlanScenario() {
  return {
    tenant_id: "oday-tw",
    scenario_name: "PV007 Q3 expansion plan",
    planning_horizon: "2026-Q3",
    scenario_id: "pv007-netplan-scenario",
    constraints: {
      max_budget: 155_000,
      min_expected_gross_margin: 340_000,
      min_capacity_delta: 1,
      max_average_risk: 0.58,
      min_action_counts: { OPEN: 1 },
      max_action_counts: { EXIT: 0 },
    },
    existing_stores: [
      {
        store_id: "pv007-store-north",
        baseline_gross_margin: 180_000,
        improve_gross_margin_uplift: 42_000,
        improve_cost: 32_000,
        move_gross_margin_uplift: 24_000,
        move_cost: 45_000,
        exit_cost: 15_000,
        keep_risk: 0.15,
        improve_risk: 0.22,
        move_risk: 0.31,
        current_capacity: 1,
        source_snapshot_ids: ["pv007-netplan-store-snapshot"],
      },
    ],
    candidate_sites: [
      {
        candidate_site_id: "pv007-candidate-east",
        expected_gross_margin: 174_000,
        open_cost: 88_000,
        risk_score: 0.36,
        capacity_delta: 1,
        source_snapshot_ids: ["pv007-netplan-site-snapshot"],
      },
    ],
  };
}

async function registerLearningDataset(api: Awaited<ReturnType<typeof playwrightRequest.newContext>>) {
  const rows = Array.from({ length: 4 }, (_, index) => ({
    view_name: "sitescore-propensity-view",
    view_version: "features-v24",
    entity_id: `pv007-site-${index}`,
    feature_snapshot_time: "2026-06-01T00:00:00Z",
    prediction_origin_time: "2026-06-02T00:00:00Z",
    source_snapshot_ids: ["pv007-learning-feature-snapshot"],
    data_quality_score: 0.97,
    confidence: 0.94,
    features: { visits_28d: 120 + index, competitor_count: 3 },
    labels: { converted: index % 2 },
    label_maturity_time: "2026-05-30T00:00:00Z",
  }));
  const response = await api.post(`${API_BASE_URL}/learninghub/dataset-snapshots`, {
    data: { dataset_snapshot_id: "pv007-sitescore-dataset", rows },
  });
  expect(response.status()).toBe(201);
  const payload = await response.json();
  expect(payload.training_record_count).toBe(4);
}

async function registerLearningModelVersion(
  api: Awaited<ReturnType<typeof playwrightRequest.newContext>>,
  version: string,
  options: {
    stage: string;
    rollbackTarget?: string;
    artifactContent: string;
    metrics: Record<string, number>;
  },
) {
  const response = await api.post(`${API_BASE_URL}/learninghub/models/sitescore-propensity/versions`, {
    data: {
      version,
      dataset_snapshot_id: "pv007-sitescore-dataset",
      metrics: options.metrics,
      baseline_metrics: { precision_at_50: 0.76, auc: 0.84 },
      thresholds: [
        { metric_name: "precision_at_50", min_value: 0.8 },
        { metric_name: "auc", min_value: 0.85 },
      ],
      segment_metrics: [
        {
          segment_name: "region",
          segment_value: "north",
          metrics: { precision_at_50: options.metrics.precision_at_50 },
          record_count: 4,
        },
      ],
      calibration_summary: { ece: 0.03 },
      feature_schema_version: "features-v24",
      label_version: "labels-v7",
      artifact_content: options.artifactContent,
      artifact_content_type: "application/octet-stream",
      artifact_metadata: { training_run: `pv007-run-${version}` },
      stage: options.stage,
      run_id: `pv007-run-${version}`,
      git_sha: "154e4c6cddd2de96b052f741857e059050828e05",
      rollback_target: options.rollbackTarget,
      monitoring_config: { window: "7d", guardrail: "precision_at_50" },
      model_card: {
        owner: "ml-governance",
        risk_level: "R2",
        intended_use: "Rank candidate stores for governed site-score prioritization.",
        not_intended_use: "Do not use for credit, employment, or fully automated approvals.",
        feature_set_id: "features-v24",
        label_set_id: "labels-v7",
        training_period: "2026-01-01/2026-05-31",
        validation_period: "2026-06-01/2026-06-07",
        algorithm: "gradient_boosted_trees",
        baseline: "sitescore-propensity-2.3.0",
        metrics_summary: options.metrics,
        segment_metrics: [{ segment: "north", precision_at_50: options.metrics.precision_at_50 }],
        calibration_summary: { ece: 0.03 },
        explainability_method: "shap",
        limitations: ["Requires fresh footfall and competitor signals."],
        known_biases: ["Lower confidence for newly opened districts."],
        rollback_conditions: ["precision_at_50 drops below 0.8", "audit override rate exceeds 12%"],
        approvals: [{ approver: "pv007-model-review-board", role: "governance", decision: "approved" }],
      },
    },
  });
  expect(response.status()).toBe(201);
  const payload = await response.json();
  expect(payload.artifact_verified).toBe(true);
}

function releasePayload(releaseType: string, version: string, rollbackTarget: string, approvalId: string) {
  return {
    model_name: "sitescore-propensity",
    version,
    release_type: releaseType,
    reason: `${releaseType} governed release for PV007 product E2E.`,
    approval_id: approvalId,
    rollback_target: rollbackTarget,
    monitoring_window: "7d",
    success_criteria: ["precision_at_50 >= 0.8", "override_rate <= 0.12"],
    fail_criteria: ["precision_at_50 < 0.8", "rollback requested by governance"],
    affected_modules: ["sitescore", "audit", "netplan"],
    requested_by: "pv007-ml-release-manager",
    approved_by: "pv007-model-review-board",
  };
}

function decisionCard(
  decisionId: string,
  module: string,
  subjectRef: string,
  outcome: string,
  auditEventId: string,
) {
  return {
    decision_id: decisionId,
    decision_type: "governed_product_decision",
    module,
    title: `${module} governed product decision`,
    subject_ref: subjectRef,
    outcome,
    owner: "pv007-audit-ops",
    decided_at: "2026-06-28T03:00:00Z",
    rationale: "Product E2E captured lineage, approval, execution, outcome, and rollback evidence.",
    input_snapshot_id: "pv007-input-snapshot",
    evidence_refs: [`evidence://${decisionId}`],
    model_refs: ["sitescore-propensity:2.4.0"],
    policy_refs: ["audit-evidence-export-policy-v1"],
    audit_event_ids: [auditEventId],
    subsidy_requirements: ["ELIGIBILITY", "DECISION", "EFFECT", "CONTROL", "TRACE"],
    controls: ["approval", "rollback", "retention"],
    approval_ref: `${subjectRef}/approval`,
    execution_ref: `${subjectRef}/execution`,
    outcome_ref: `${subjectRef}/outcome`,
    feature_version: "features-v24",
    data_snapshot_id: "pv007-sitescore-dataset",
    artifact_hash: "sha256:pv007",
    metrics: { evidence_count: 1 },
  };
}
