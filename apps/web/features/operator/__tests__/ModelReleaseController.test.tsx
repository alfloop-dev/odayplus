import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ModelReleaseController } from "../governance/ModelReleaseController";
import { fallbackLearningHubModels } from "../governance/learningHubLoader";
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
    render(<GovernanceWorkspace roleId="ops-lead" />);

    fireEvent.click(screen.getByTestId("governance-tab-learningHub"));

    expect(await screen.findByTestId("model-release-controller")).toBeInTheDocument();
    expect(screen.getByText("Model Release Controller UI")).toBeInTheDocument();
  });
});
