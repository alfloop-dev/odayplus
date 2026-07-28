import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { IntakeErrorRecovery } from "../IntakeErrorRecovery";

afterEach(cleanup);

function openPreservedInput() {
  fireEvent.click(screen.getByTestId("error-toggle-preserved-input"));
}

describe("IntakeErrorRecovery preserved input drawer", () => {
  it("renders the durable submission input handed down by the detail page", () => {
    render(
      <IntakeErrorRecovery
        stage="FAILED"
        correlationId="corr-timeout"
        error={{
          code: "ODP-INTAKE-RETRIEVAL-TIMEOUT",
          summary: "來源頁擷取逾時（上游未於 10 秒內回應）。",
          nextAction: "稍後重試；已填寫的修正內容會保留。",
          retryable: true,
        }}
        preservedInput={{
          originalUrl: "https://www.synthetic.example/detail-50000001.html",
          canonicalUrl: "https://www.synthetic.example/detail-50000001.html",
          heatZoneId: "HZ-TPE",
        }}
      />,
    );

    openPreservedInput();

    const box = screen.getByTestId("error-preserved-input-box");
    expect(box).toHaveTextContent("https://www.synthetic.example/detail-50000001.html");
    expect(box).toHaveTextContent("canonicalUrl");
    expect(box).toHaveTextContent("HZ-TPE");
    expect(screen.queryByTestId("error-preserved-input-unavailable")).toBeNull();
  });

  it("declares preserved input unavailable instead of rendering an empty object", () => {
    render(<IntakeErrorRecovery stage="FAILED" preservedInput={null} />);

    openPreservedInput();

    expect(screen.getByTestId("error-preserved-input-unavailable")).toHaveTextContent("UNAVAILABLE");
    expect(screen.queryByTestId("error-preserved-input-json")).toBeNull();
    expect(screen.getByTestId("error-preserved-input-box")).not.toHaveTextContent("{}");
  });

  it("redacts credential-class keys under purpose binding while keeping business values", () => {
    render(
      <IntakeErrorRecovery
        stage="FAILED"
        preservedInput={{
          originalUrl: "https://www.synthetic.example/detail-50000001.html",
          sourceAccessToken: "tok-live-123456",
          upstreamPassword: "hunter2",
          clientSecret: "sec-abcdef",
          providerCredential: "cred-999",
          address_raw: "台北市信義區松高路 1 號 1F",
        }}
      />,
    );

    openPreservedInput();

    const box = screen.getByTestId("error-preserved-input-box");
    expect(box).toHaveTextContent("[REDACTED_PURPOSE_BINDING]");
    expect(box).not.toHaveTextContent("tok-live-123456");
    expect(box).not.toHaveTextContent("hunter2");
    expect(box).not.toHaveTextContent("sec-abcdef");
    expect(box).not.toHaveTextContent("cred-999");
    expect(box).toHaveTextContent("台北市信義區松高路 1 號 1F");
    expect(box).toHaveTextContent("https://www.synthetic.example/detail-50000001.html");
  });

  it("keeps the retry affordance for retryable failures only", () => {
    const { rerender } = render(
      <IntakeErrorRecovery
        stage="FAILED"
        error={{ code: "ODP-INTAKE-RETRIEVAL-TIMEOUT", summary: "逾時", nextAction: "RETRY", retryable: true }}
        onRetry={() => undefined}
      />,
    );
    expect(screen.getByTestId("error-action-retry")).toBeVisible();

    rerender(
      <IntakeErrorRecovery
        stage="QUARANTINED"
        error={{ code: "SOURCE_POLICY_BLOCKED", summary: "政策拒絕", nextAction: "REVIEW", retryable: false }}
        onRetry={() => undefined}
      />,
    );
    expect(screen.queryByTestId("error-action-retry")).toBeNull();
  });
});
