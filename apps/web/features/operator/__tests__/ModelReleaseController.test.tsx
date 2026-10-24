import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ModelReleaseController } from "../governance/ModelReleaseController";
import { fallbackLearningHubModels, monitorRelease } from "../governance/learningHubLoader";
import { GovernanceWorkspace } from "../GovernanceWorkspace";

describe("ModelReleaseController Component (ODP-CAP-MODEL-RELEASE-UI-001)", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("renders model release controller with model selector and version table", async () => {
    render(<ModelReleaseController actor="TestActor" roleId="ops-lead" />);

    expect(screen.getByTestId("model-release-controller")).toBeInTheDocument();
    expect(screen.getByText("Model Release Controller UI")).toBeInTheDocument();
    expect(screen.getByLabelText("選擇模型 (Model):")).toBeInTheDocument();
    expect(screen.getAllByText("v2.1.0").length).toBeGreaterThan(0);
    expect(screen.getByText("v2.2.0-candidate")).toBeInTheDocument();
    expect(screen.getByText("發布前置 Gate Checklist (FR-GOV-009)")).toBeInTheDocument();
  });

  it("enforces segregation of duties by rejecting self-review", async () => {
    render(<ModelReleaseController actor="ModelReviewBoard" roleId="ops-lead" />);

    // Open release drawer
    fireEvent.click(screen.getAllByRole("button", { name: "發布" })[0]);
    expect(screen.getByText(/申請模型發布/)).toBeInTheDocument();

    // Fill reason
    fireEvent.change(screen.getByLabelText(/Release Reason/), {
      target: { value: "Valid reason for promoting v2.1.0 to production champion" },
    });

    // Set approved_by to match actor (ModelReviewBoard)
    fireEvent.change(screen.getByLabelText(/Approver Principal/), {
      target: { value: "ModelReviewBoard" },
    });

    fireEvent.click(screen.getByRole("button", { name: /送出發布/ }));

    expect(await screen.findByText(/MODEL_RELEASE_SELF_REVIEW/)).toBeInTheDocument();
  });

  it("enforces reason length rule (>= 10 chars) per FR-GOV-009", async () => {
    render(<ModelReleaseController actor="RequesterOne" roleId="ops-lead" />);

    fireEvent.click(screen.getAllByRole("button", { name: "發布" })[0]);

    fireEvent.change(screen.getByLabelText(/Approver Principal/), {
      target: { value: "ApproverTwo" },
    });
    fireEvent.change(screen.getByLabelText(/Release Reason/), {
      target: { value: "short" },
    });

    fireEvent.click(screen.getByRole("button", { name: /送出發布/ }));

    expect(await screen.findByText(/發布與回滾理由必須至少包含 10 個字/)).toBeInTheDocument();
  });

  it("submits release request to backend API (POST /learninghub/releases)", async () => {
    const fetchMock = vi.fn().mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/learninghub/releases") && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            release_id: "rel-mock-101",
            model_name: "forecast_revenue_interval",
            from_version: "v2.0.4",
            to_version: "v2.1.0",
            version: "v2.1.0",
            release_type: "FULL",
            reason: "Valid reason for promoting v2.1.0 to production champion",
            approval_id: "ap-mock-01",
            monitoring_window: "7d",
            success_criteria: ["MAPE <= 0.09"],
            fail_criteria: ["MAPE > 0.12"],
            affected_modules: ["Store Ops"],
            requested_by: "RequesterOne",
            approved_by: "ApproverTwo",
            created_at: "2026-08-05T00:00:00Z",
            audit_event_id: "aud-mock-101",
          }),
          { status: 201, headers: { "Content-Type": "application/json" } }
        );
      }
      return new Response(
        JSON.stringify({ items: fallbackLearningHubModels, count: 3 }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ModelReleaseController actor="RequesterOne" roleId="ops-lead" />);

    fireEvent.click(screen.getAllByRole("button", { name: "發布" })[0]);

    fireEvent.change(screen.getByLabelText(/Approver Principal/), {
      target: { value: "ApproverTwo" },
    });
    fireEvent.change(screen.getByLabelText(/Release Reason/), {
      target: { value: "Valid reason for promoting v2.1.0 to production champion" },
    });

    fireEvent.click(screen.getByRole("button", { name: /送出發布/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const postCall = fetchMock.mock.calls.find(([u, init]) => String(u).includes("/releases") && init?.method === "POST");
    expect(postCall).toBeDefined();
    const [url, init] = postCall!;
    expect(String(url)).toContain("/learninghub/releases");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toMatchObject({
      model_name: "forecast_revenue_interval",
      version: "v2.1.0",
      release_type: "FULL",
      approved_by: "ApproverTwo",
    });

    expect(await screen.findByText(/Release rel-mock-101/)).toBeInTheDocument();
  });

  it("approves model card via API (POST /learninghub/models/:name/versions/:ver/approval)", async () => {
    const fetchMock = vi.fn().mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/approval")) {
        return new Response(
          JSON.stringify({
            model_name: "forecast_revenue_interval",
            model_version: "v2.1.0",
            owner: "ForecastOps Team",
            risk_level: "R3",
            intended_use: "Predict revenue",
            not_intended_use: "Loan underwriting",
            dataset_snapshot_id: "ds-1",
            validation_run_id: "val-1",
            feature_set_id: "feat-1",
            label_set_id: "lbl-1",
            training_period: "2025-2026",
            validation_period: "2026",
            algorithm: "LightGBM",
            baseline: "v2.0",
            metrics_summary: { mape: 0.08 },
            segment_metrics: [],
            calibration_summary: {},
            explainability_method: "SHAP",
            limitations: [],
            known_biases: [],
            privacy_review: "PASSED",
            security_review: "PASSED",
            release_status: "production",
            rollback_conditions: ["MAPE > 0.12"],
            approvals: [{ approver: "ModelReviewBoard", role: "model-review-board", decision: "approved" }],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      return new Response(
        JSON.stringify({ items: fallbackLearningHubModels, count: 3 }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ModelReleaseController actor="ReviewerOne" roleId="ops-lead" />);

    // Open Model Card drawer
    fireEvent.click(screen.getAllByRole("button", { name: "Model Card" })[0]);
    expect(screen.getByText(/Model Card: forecast_revenue_interval:v2.1.0/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Approve Model Card" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url] = fetchMock.mock.calls.find(([u]) => String(u).includes("/approval"))!;
    expect(String(url)).toContain("/learninghub/models/forecast_revenue_interval/versions/v2.1.0/approval");
  });

  it("renders ModelReleaseController inside GovernanceWorkspace under learningHub tab", async () => {
    render(<GovernanceWorkspace roleId="ops-lead" viewState={{ tab:"learningHub", approvalId:null, evidenceId:null, auditCategory:"all" }} />);

    fireEvent.click(screen.getByTestId("governance-tab-learningHub"));

    expect(await screen.findByTestId("model-release-controller")).toBeInTheDocument();
    expect(screen.getByText("Model Release Controller UI")).toBeInTheDocument();
  });
});


// Local HTTP doubles verify presentation only; these are not model approvals.
describe("ModelReleaseController production data admission", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  function registeredModel() {
    return {
      ...structuredClone(fallbackLearningHubModels[0]),
      model_name: "owned-registered-model",
      version: "owned-version-1",
      release_revision: 7,
      model_card: { ...fallbackLearningHubModels[0].model_card, model_name: "owned-registered-model", model_version: "owned-version-1" },
      stage: "dev",
      aliases: [],
    };
  }

  function stubReads(modelsBody: unknown, releasesBody: unknown = { items: [] }) {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method && init.method !== "GET") throw new Error("Unexpected write in read-only presentation check");
      const body = String(input).includes("/releases") ? releasesBody : modelsBody;
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("keeps fixture stages, validation and releases absent while production reads are pending", () => {
    const fetchMock = vi.fn(() => new Promise<Response>(() => {}));
    vi.stubGlobal("fetch", fetchMock);
    render(<ModelReleaseController roleId="ops-lead" />);
    expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "loading");
    expect(screen.queryByTestId("model-release-controller")).not.toBeInTheDocument();
    expect(screen.queryByText("v2.1.0")).not.toBeInTheDocument();
    expect(screen.queryByText(/所有門檻通過/)).not.toBeInTheDocument();
  });

  it.each(["models", "releases", "network"])("does not replace a failed %s read with fixture state", async (failure) => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (failure === "network") throw new Error("Owned read unavailable");
      const releases = String(input).includes("/releases");
      const rejected = failure === (releases ? "releases" : "models");
      return new Response(JSON.stringify({ items: releases ? [] : [registeredModel()] }), { status: rejected ? 503 : 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ModelReleaseController roleId="ops-lead" />);
    await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error"));
    expect(screen.queryByTestId("model-release-controller")).not.toBeInTheDocument();
    expect(screen.queryByText("v2.1.0")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "發布" })).not.toBeInTheDocument();
  });

  it("preserves a successful empty model list as empty without restoring demo models", async () => {
    stubReads({ items: [] });
    render(<ModelReleaseController roleId="ops-lead" />);
    await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "empty"));
    expect(screen.queryByTestId("model-release-controller")).not.toBeInTheDocument();
    expect(screen.queryByText("v2.1.0")).not.toBeInTheDocument();
  });

  it.each([
    { label: "null envelope", body: null },
    { label: "missing items", body: {} },
    { label: "non-array items", body: { items: "invalid" } },
    { label: "null item", body: { items: [null] } },
    { label: "missing identity", body: { items: [{}] } },
    { label: "non-array aliases", body: { items: [{ ...registeredModel(), aliases: "champion" }] } },
    { label: "non-array approvals", body: { items: [{ ...registeredModel(), model_card: { ...registeredModel().model_card, approvals: {} } }] } },
    { label: "non-array rollback conditions", body: { items: [{ ...registeredModel(), model_card: { ...registeredModel().model_card, rollback_conditions: {} } }] } },
    { label: "non-array thresholds", body: { items: [{ ...registeredModel(), validation_run: { thresholds: {} } }] } },
    { label: "non-object metrics", body: { items: [{ ...registeredModel(), validation_run: { metrics: "invalid" } }] } },
  ])("rejects malformed production models: $label", async ({ body }) => {
    stubReads(body);
    render(<ModelReleaseController roleId="ops-lead" />);
    await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error"));
    expect(screen.queryByText("v2.1.0")).not.toBeInTheDocument();
  });

  it.each(["models", "releases"])("rejects explicit seed metadata on the %s root envelope", async (seeded) => {
    stubReads({ items: [registeredModel()], ...(seeded === "models" ? { source: "seed-r4" } : {}) },
      { items: [], ...(seeded === "releases" ? { metadata: { fixture: true } } : {}) });
    render(<ModelReleaseController roleId="ops-lead" />);
    await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error"));
    expect(screen.queryByTestId("model-release-controller")).not.toBeInTheDocument();
  });

  it("treats malformed releases as unavailable rather than a successful empty history", async () => {
    stubReads({ items: [registeredModel()] }, { items: "invalid" });
    render(<ModelReleaseController roleId="ops-lead" />);
    await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error"));
    expect(screen.queryByText("v2.1.0")).not.toBeInTheDocument();
  });

  it.each(["dev", "shadow", "canary", "production", "retired"])("keeps a real %s stage readable with genuinely empty release history", async (stage) => {
    const fetchMock = stubReads({ items: [{ ...registeredModel(), stage }] });
    render(<ModelReleaseController roleId="ops-lead" />);
    await screen.findByTestId("model-release-controller");
    expect(screen.getAllByText("owned-version-1").length).toBeGreaterThan(0);
    expect(screen.getByText(stage)).toBeInTheDocument();
    expect(screen.queryByText("v2.1.0")).not.toBeInTheDocument();
    expect(screen.queryByTestId("operator-data-unavailable")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });

  it("shows a readable registered model while missing validation and reviews keep its checklist incomplete", async () => {
    const model = registeredModel();
    const { validation_run: _validation, ...withoutValidation } = model;
    stubReads({ items: [{ ...withoutValidation, model_card: { ...model.model_card, privacy_review: undefined, security_review: undefined } }] });
    render(<ModelReleaseController roleId="ops-lead" />);
    await screen.findByTestId("model-release-controller");
    expect(screen.getAllByText("owned-version-1").length).toBeGreaterThan(0);
    expect(screen.getByText("Validation checks failed or incomplete")).toBeInTheDocument();
    expect(screen.getByText("Model Card incomplete or security/privacy review FAILED")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "開啟發布控制器 (Release Controller)" })).toBeDisabled();
    expect(screen.queryByText(/所有門檻通過/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("operator-data-unavailable")).not.toBeInTheDocument();
  });

  it.each(["is_complete", "is_approved"])("honors canonical %s=false even when local card fields are populated", async (field) => {
    const model = registeredModel();
    stubReads({ items: [{ ...model, model_card: { ...model.model_card, [field]: false } }] });
    render(<ModelReleaseController roleId="ops-lead" />);
    await screen.findByTestId("model-release-controller");
    expect(screen.getByRole("button", { name: "開啟發布控制器 (Release Controller)" })).toBeDisabled();
    expect(screen.queryByText(/所有門檻通過/)).not.toBeInTheDocument();
    if (field === "is_approved") expect(screen.queryByText(/✓ 已簽核/)).not.toBeInTheDocument();
  });

  it("retains the existing non-blocking WARNING policy without inventing a passed review", async () => {
    const model = registeredModel();
    stubReads({ items: [{ ...model, model_card: { ...model.model_card, privacy_review: "WARNING", security_review: "PASSED" } }] });
    render(<ModelReleaseController roleId="ops-lead" />);
    await screen.findByTestId("model-release-controller");
    expect(screen.getByText(/Privacy\/Security WARNING/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "開啟發布控制器 (Release Controller)" })).toBeEnabled();
  });

  it("retries an unavailable read using the original endpoint and no write command", async () => {
    let unavailable = true;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const releases = String(input).includes("/releases");
      return new Response(JSON.stringify({ items: releases ? [] : [registeredModel()] }), { status: unavailable ? 503 : 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ModelReleaseController roleId="ops-lead" />);
    await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error"));
    unavailable = false;
    fireEvent.click(screen.getByRole("button", { name: "重新載入" }));
    await screen.findByTestId("model-release-controller");
    expect(screen.getAllByText("owned-version-1").length).toBeGreaterThan(0);
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });

  it("keeps a late prior-role success from replacing the current-role error", async () => {
    const held: Array<{ url: string; resolve: (value: Response) => void }> = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (new Headers(init?.headers).get("x-operator-role") === "ops-lead") {
        return new Promise<Response>((resolve) => held.push({ url: String(input), resolve }));
      }
      return Promise.resolve(new Response("{}", { status: 503 }));
    }));
    const view = render(<ModelReleaseController roleId="ops-lead" />);
    expect(held).toHaveLength(2);
    view.rerender(<ModelReleaseController roleId="pm-audit" />);
    await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error"));
    await act(async () => {
      for (const request of held) request.resolve(new Response(JSON.stringify({ items: request.url.includes("/releases") ? [] : [registeredModel()] }), { status: 200 }));
    });
    expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error");
    expect(screen.queryByTestId("model-release-controller")).not.toBeInTheDocument();
    expect(screen.queryByText("owned-version-1")).not.toBeInTheDocument();
  });

  it("leaves production release and observation inputs unset and does not invent a rollback target or risk", async () => {
    const model = registeredModel();
    stubReads({ items: [{ ...model, rollback_target: null, model_card: { ...model.model_card, risk_level: undefined } }] },
      { items: [{ release_id: "owned-release", model_name: model.model_name }] });
    render(<ModelReleaseController roleId="ops-lead" />);
    await screen.findByTestId("model-release-controller");
    expect(screen.getByText("未提供")).toBeInTheDocument();
    expect(screen.queryByText("R2")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "開啟發布控制器 (Release Controller)" })).toBeDisabled();
    expect(screen.getByText("Rollback target version is required for FULL/CANARY/ROLLBACK")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^發布$/ }));
    for (const label of [/Approval ID:/, /Approver Principal/, /Monitoring Window:/, /Success Criteria:/, /Fail Criteria:/, /Affected Modules/, /Rollback Target Version:/]) {
      expect(screen.getByLabelText(label)).toHaveValue("");
    }
    fireEvent.click(screen.getByRole("button", { name: "評估 Guardrails" }));
    expect(screen.getByLabelText("Observed Metrics (JSON format):")).toHaveValue("");
  });

  it("keeps missing optional evidence collections readable and opens their drawers without inventing rows", async () => {
    const model = registeredModel();
    stubReads({ items: [{ ...model, aliases: undefined,
      model_card: { ...model.model_card, approvals: undefined, rollback_conditions: undefined },
      validation_run: { ...model.validation_run, thresholds: undefined, metrics: undefined, baseline_metrics: undefined } }] });
    render(<ModelReleaseController roleId="ops-lead" />);
    await screen.findByTestId("model-release-controller");
    fireEvent.click(screen.getByRole("button", { name: "Model Card" }));
    const card = within(screen.getByRole("heading", { name: /^Model Card:/ }).parentElement!.parentElement!);
    expect(card.getByText("未提供")).toBeInTheDocument();
    fireEvent.click(card.getByRole("button", { name: "關閉" }));
    fireEvent.click(screen.getByRole("button", { name: "Validation" }));
    expect(screen.getByText("驗證門檻未提供")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "開啟發布控制器 (Release Controller)" })).toBeDisabled();
  });

  it("shows unknown validation values and comparison instead of synthetic zero and PASSED", async () => {
    const model = registeredModel();
    stubReads({ items: [{ ...model, validation_run: { ...model.validation_run,
      metrics: {}, baseline_metrics: {}, thresholds: [{ metric_name: "mape", max_value: 0.1 }] } }] });
    render(<ModelReleaseController roleId="ops-lead" />);
    await screen.findByTestId("model-release-controller");
    fireEvent.click(screen.getByRole("button", { name: "Validation" }));
    const drawer = within(screen.getByRole("heading", { name: /^Validation Run & Threshold Comparison:/ }).parentElement!.parentElement!);
    expect(drawer.getByText("未判讀")).toBeInTheDocument();
    expect(drawer.getAllByText("未提供")).toHaveLength(3);
    expect(drawer.queryByText("PASSED")).not.toBeInTheDocument();
  });

  it.each(["card", "release", "monitor"])("rejects a completed old-role %s result after the current role is ready", async (command) => {
    // Production has no defined release-monitor policy yet. The existing
    // monitor async/context contract remains exercised in explicit POC mode.
    if (command === "monitor") vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const model = registeredModel();
    const otherModel = { ...model, model_name: "owned-role-b-model", version: "owned-role-b-version",
      model_card: { ...model.model_card, model_name: "owned-role-b-model", model_version: "owned-role-b-version" } };
    let finishCommand: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return new Promise<Response>((resolve) => { finishCommand = resolve; });
      const currentModel = new Headers(init?.headers).get("x-operator-role") === "pm-audit" ? otherModel : model;
      const rows = String(input).includes("/releases") ? [{ release_id: "owned-release", model_name: currentModel.model_name }] : [currentModel];
      return Promise.resolve(new Response(JSON.stringify({ items: rows }), { status: 200 }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<ModelReleaseController roleId="ops-lead" />);
    await screen.findByTestId("model-release-controller");
    if (command === "monitor") await waitFor(() => expect(screen.getAllByText("owned-version-1").length).toBeGreaterThan(0));
    if (command === "card") {
      fireEvent.click(screen.getByRole("button", { name: "Model Card" }));
      fireEvent.click(screen.getByRole("button", { name: "Approve Model Card" }));
    } else if (command === "release") {
      fillProductionReleaseForm();
      fireEvent.click(screen.getByRole("button", { name: /送出發布/ }));
    } else {
      fireEvent.click(screen.getByRole("button", { name: "評估 Guardrails" }));
      fireEvent.change(screen.getByLabelText("Observed Metrics (JSON format):"), { target: { value: '{"mape":0.12}' } });
      fireEvent.click(screen.getByRole("button", { name: "Evaluated Guardrails (POST /monitor)" }));
    }
    expect(finishCommand).toBeDefined();
    view.rerender(<ModelReleaseController roleId="pm-audit" />);
    await waitFor(() => expect(screen.getAllByText("owned-role-b-version").length).toBeGreaterThan(0));
    const oldRoleReads = () => fetchMock.mock.calls.filter(([, init]) => init?.method === "GET" && new Headers(init.headers).get("x-operator-role") === "ops-lead").length;
    const callsBeforeOldCommandFinishes = oldRoleReads();
    await act(async () => { finishCommand!(new Response(JSON.stringify(command === "card" ? model.model_card : command === "release"
      ? { release_id: "old-role-release", audit_event_id: "old-role-release-audit" }
      : { release_id: "owned-release", status: "HEALTHY", evaluated_by: "old-role-monitor", audit_event_id: "old-role-monitor-audit" }), { status: 200 })); });
    expect(oldRoleReads()).toBe(callsBeforeOldCommandFinishes);
    expect(screen.getAllByText("owned-role-b-version").length).toBeGreaterThan(0);
    expect(screen.queryByText(/Model Card for owned-registered-model/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Release old-role-release/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "評估 Guardrails" }));
    expect(screen.queryByText("old-role-monitor")).not.toBeInTheDocument();
    expect(screen.queryByText("old-role-monitor-audit")).not.toBeInTheDocument();
  });

  function fillProductionReleaseForm() {
    fireEvent.click(screen.getByRole("button", { name: /^發布$/ }));
    for (const [label, value] of [
      [/Release Reason/, "Unit-only reviewed release reason"],
      [/Approver Principal/, "DisplayPersona"],
      [/Approval ID:/, "owned-approval"],
      [/Monitoring Window:/, "unit-only-window"],
      [/Success Criteria:/, "unit-only-success-rule"],
      [/Fail Criteria:/, "unit-only-failure-rule"],
      [/Affected Modules/, "Unit module"],
    ] as const) fireEvent.change(screen.getByLabelText(label), { target: { value } });
  }

  it.each([0, 7])("submits the actual release revision %s and lets the server verify the requester", async (revision) => {
    const model = { ...registeredModel(), release_revision: revision };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return new Response(JSON.stringify({ release_id: "owned-release-result", audit_event_id: "owned-release-audit" }), { status: 201 });
      return new Response(JSON.stringify({ items: String(input).includes("/releases") ? [] : [model] }), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ModelReleaseController roleId="ops-lead" actor="DisplayPersona" />);
    await screen.findByTestId("model-release-controller");
    fillProductionReleaseForm();
    fireEvent.click(screen.getByRole("button", { name: /送出發布/ }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")!;
    const body = JSON.parse(String(post[1]?.body));
    expect(body.expected_release_revision).toBe(revision);
    expect(body).not.toHaveProperty("requested_by");
    expect(body.approved_by).toBe("DisplayPersona");
    expect(body.reason).toBe("Unit-only reviewed release reason");
    expect(await screen.findByText(/Release owned-release-result/)).toBeInTheDocument();
    expect(screen.queryByText(/MODEL_RELEASE_SELF_REVIEW/)).not.toBeInTheDocument();
  });

  it("keeps an older readable model without a revision incomplete and never posts a guessed revision", async () => {
    const { release_revision: _revision, ...model } = registeredModel();
    const fetchMock = stubReads({ items: [model] });
    render(<ModelReleaseController roleId="ops-lead" />);
    await screen.findByTestId("model-release-controller");
    expect(screen.getAllByText("owned-version-1").length).toBeGreaterThan(0);
    expect(screen.getByText("Release revision 未提供，無法送出發布命令")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "開啟發布控制器 (Release Controller)" })).toBeDisabled();
    fillProductionReleaseForm();
    fireEvent.submit(screen.getByLabelText(/Release Reason/).closest("form")!);
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });

  it.each([-1, 1.5, "7"])("rejects a provided malformed release revision %s", async (revision) => {
    stubReads({ items: [{ ...registeredModel(), release_revision: revision }] });
    render(<ModelReleaseController roleId="ops-lead" />);
    await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error"));
    expect(screen.queryByTestId("model-release-controller")).not.toBeInTheDocument();
  });

  it("omits a persona evaluator so the original monitor endpoint owns the verified actor", async () => {
    const model = registeredModel();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return new Response(JSON.stringify({ release_id: "owned-release", status: "WARNING", audit_event_id: "owned-monitor-audit", evaluated_by: "verified-server-subject" }), { status: 201 });
      return new Response(JSON.stringify({ items: String(input).includes("/releases") ? [{ release_id: "owned-release", model_name: model.model_name }] : [model] }), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    // The adapter retains trusted-actor omission for an explicit policy caller;
    // the production controller separately refuses an unbound policy below.
    const result = await monitorRelease({
      release_id: "owned-release", observed_metrics: { mape: 0.12 },
      guardrails: [{ metric_name: "mape", max_value: 0.1 }],
      evaluated_by: "DisplayPersona", roleId: "ops-lead",
    });
    expect(result.ok).toBe(true);
    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")!;
    const body = JSON.parse(String(post[1]?.body));
    expect(String(post[0])).toContain("/releases/owned-release/monitor");
    expect(body).not.toHaveProperty("evaluated_by");
    expect(body.observed_metrics).toEqual({ mape: 0.12 });
    if (result.ok) expect(result.assessment.evaluated_by).toBe("verified-server-subject");
  });

  it.each([0, 7])("submits an explicit production ROLLBACK with the real revision %s and shared governance fields", async (revision) => {
    const model = { ...registeredModel(), stage: "production", release_revision: revision, rollback_target: "owned-prior-version" };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return new Response(JSON.stringify({ release_id: "owned-rollback-result", audit_event_id: "owned-rollback-audit" }), { status: 201 });
      return new Response(JSON.stringify({ items: String(input).includes("/releases") ? [] : [model] }), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ModelReleaseController roleId="ops-lead" actor="DisplayPersona" />);
    await screen.findByTestId("model-release-controller");
    fireEvent.click(screen.getByRole("button", { name: "開啟 Rollback Console" }));
    expect(screen.getByText("owned-prior-version")).toBeInTheDocument();
    expect(screen.getAllByLabelText(/Approver Principal/)).toHaveLength(1);
    expect(screen.getByLabelText(/Rollback Target Version:/)).toHaveValue("");
    for (const [label, value] of [
      [/Rollback Audit Reason/, "Unit-only rollback reason based on actual reviewed evidence"],
      [/Approver Principal/, "UnitReviewer"],
      [/Approval ID:/, "owned-rollback-approval"],
      [/Monitoring Window:/, "unit-only-rollback-window"],
      [/Success Criteria:/, "Unit-only rollback success rule"],
      [/Fail Criteria:/, "Unit-only rollback failure rule"],
      [/Affected Modules/, "Unit rollback module"],
    ] as const) fireEvent.change(screen.getByLabelText(label), { target: { value } });
    fireEvent.click(screen.getByRole("button", { name: "確認執行緊急回滾 (Execute Rollback)" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")!;
    const body = JSON.parse(String(post[1]?.body));
    expect(String(post[0])).toBe("/api/v1/learninghub/releases");
    expect(body).toMatchObject({
      model_name: model.model_name, version: model.version, release_type: "ROLLBACK",
      rollback_target: "owned-prior-version", expected_release_revision: revision,
      approved_by: "UnitReviewer", approval_id: "owned-rollback-approval",
      monitoring_window: "unit-only-rollback-window",
      success_criteria: ["Unit-only rollback success rule"],
      fail_criteria: ["Unit-only rollback failure rule"], affected_modules: ["Unit rollback module"],
    });
    expect(body).not.toHaveProperty("requested_by");
    expect(await screen.findByText(/Release owned-rollback-result \(ROLLBACK\)/)).toBeInTheDocument();
  });

  it("blocks a rollback without an actual target even after the release selector was SHADOW", async () => {
    const model = { ...registeredModel(), rollback_target: null };
    const fetchMock = stubReads({ items: [model] });
    render(<ModelReleaseController roleId="ops-lead" />);
    await screen.findByTestId("model-release-controller");
    fireEvent.click(screen.getByRole("button", { name: /^發布$/ }));
    fireEvent.change(screen.getByLabelText("Release Type (發布類型)"), { target: { value: "SHADOW" } });
    expect(screen.getByRole("button", { name: /送出發布/ })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "關閉" }));
    fireEvent.click(screen.getByRole("button", { name: "開啟 Rollback Console" }));
    expect(screen.getByText("回滾目標版本未提供，無法送出回滾命令。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "確認執行緊急回滾 (Execute Rollback)" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Rollback Audit Reason/), { target: { value: "Unit-only rollback reason cannot supply a missing target" } });
    fireEvent.submit(screen.getByLabelText(/Rollback Audit Reason/).closest("form")!);
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
    expect(screen.getByLabelText(/Rollback Target Version:/)).toHaveValue("");
    expect(screen.queryByText("v2.0.4")).not.toBeInTheDocument();
  });


  it("keeps a production release readable but refuses to turn validation thresholds into a monitoring policy", async () => {
    const model = registeredModel();
    const fetchMock = stubReads({ items: [model] }, { items: [{
      release_id: "owned-release-with-text-criteria", model_name: model.model_name,
      to_version: model.version, monitoring_window: "owned-monitoring-window",
      fail_criteria: ["Actual recorded condition is not a structured policy"],
    }] });
    render(<ModelReleaseController roleId="ops-lead" />);
    await screen.findByTestId("model-release-controller");
    fireEvent.click(screen.getByRole("button", { name: "評估 Guardrails" }));
    expect(screen.getByRole("status")).toHaveTextContent("發布監控政策未提供，無法評估");
    expect(screen.getByText("監控窗：owned-monitoring-window")).toBeInTheDocument();
    expect(screen.getByText("失敗條件：Actual recorded condition is not a structured policy")).toBeInTheDocument();
    expect(screen.getByLabelText("Observed Metrics (JSON format):")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Evaluated Guardrails (POST /monitor)" })).toBeDisabled();
    // A direct form event must still fail closed rather than trust disabled UI.
    fireEvent.submit(screen.getByLabelText("Observed Metrics (JSON format):").closest("form")!);
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
    expect(screen.getAllByText("owned-version-1").length).toBeGreaterThan(0);
    expect(screen.queryByText("HEALTHY")).not.toBeInTheDocument();
    expect(screen.queryByTestId("operator-data-unavailable")).not.toBeInTheDocument();
  });

  it("retains the explicit POC monitor demo without representing it as a production policy", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const model = registeredModel();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return new Response(JSON.stringify({
        release_id: "owned-poc-release", status: "HEALTHY", audit_event_id: "unit-poc-monitor-audit",
      }), { status: 201 });
      return new Response(JSON.stringify({ items: String(input).includes("/releases")
        ? [{ release_id: "owned-poc-release", model_name: model.model_name }] : [model] }), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ModelReleaseController roleId="ops-lead" />);
    await waitFor(() => expect(screen.getAllByText("owned-version-1").length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole("button", { name: "評估 Guardrails" }));
    fireEvent.click(screen.getByRole("button", { name: "Evaluated Guardrails (POST /monitor)" }));
    await screen.findByText("unit-poc-monitor-audit");
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(JSON.parse(String(post[1]?.body)).guardrails).toEqual([
      { metric_name: "mape", max_value: 0.10, warning_max_value: 0.09 },
      { metric_name: "rmse", max_value: 1500.0, warning_max_value: 1450.0 },
    ]);
    expect(screen.queryByText(/發布監控政策未提供/)).not.toBeInTheDocument();
  });

});
