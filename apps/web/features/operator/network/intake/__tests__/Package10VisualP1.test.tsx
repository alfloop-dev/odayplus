import React, { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { IntakeProcessingDetail } from "../IntakeProcessingDetail";
import { IntakeStageTimeline } from "../IntakeStageTimeline";
import { ListingCompareTable } from "../ListingCompareTable";
import {
  INTAKE_ERROR_MATRIX,
  INTAKE_STAGE_MATRIX,
  MATCH_OUTCOME_MATRIX,
  SOURCE_POLICY_MATRIX,
} from "../StateMatrix";

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(ui: React.ReactNode) {
  act(() => root.render(ui));
}

function byTestId(testId: string): HTMLElement {
  const element = document.body.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
  if (!element) throw new Error(`Missing data-testid=${testId}`);
  return element;
}

const possibleMatchRecord: any = {
  id: "IN-3011",
  sourceId: "591_123456",
  originalUrl: "https://rent.591.com.tw/123456?utm_source=manual",
  canonicalUrl: "https://rent.591.com.tw/123456",
  submitter: "OP-100",
  capturedAt: "2026-07-20T10:00:00Z",
  owner: "OP-100",
  heatZoneId: "HZ-TPE-XINYI",
  policy: "APPROVED_RETRIEVAL",
  policyLabel: "核准單頁讀取",
  policyReason: "核准領域白名單",
  stage: "NEEDS_REVIEW",
  parserVersion: "v2.1.0",
  snapshotId: "SNAP-9001",
  rawSnapshot: null,
  correlationId: "CORR-778899",
  version: 4,
  auditEvents: [],
  parsedFields: {
    address_raw: {
      key: "address_raw",
      label: "地址",
      sourceValue: "台北市信義區松高路12號",
      normalizedValue: "台北市信義區松高路12號",
      correctedValue: null,
      correctionReason: null,
      identity: true,
      lowConfidence: false,
    },
    area_ping: {
      key: "area_ping",
      label: "坪數",
      sourceValue: "45",
      normalizedValue: "45",
      correctedValue: null,
      correctionReason: null,
      identity: true,
      lowConfidence: false,
    },
    floor: {
      key: "floor",
      label: "樓層",
      sourceValue: "5F-2",
      normalizedValue: "5F-2",
      correctedValue: null,
      correctionReason: null,
      identity: true,
      lowConfidence: false,
    },
    rent_amount: {
      key: "rent_amount",
      label: "租金",
      sourceValue: "38000",
      normalizedValue: "38000",
      correctedValue: null,
      correctionReason: null,
      identity: false,
      lowConfidence: false,
    },
  },
  matchResult: {
    targetListingId: "LST-1002",
    outcome: "POSSIBLE_MATCH",
    outcomeLabel: "疑似重複",
    confidence: 0.78,
    summary: "地址一致，租金與樓層有差異。",
    agreeingSignals: [{ key: "address", label: "地址", agrees: true, detail: "地址一致" }],
    contradictingSignals: [
      { key: "rent", label: "租金", agrees: false, detail: "35,000 → 38,000" },
      { key: "floor", label: "樓層", agrees: false, detail: "5F → 5F-2" },
    ],
  },
};

const targetListing = {
  id: "LST-1002",
  sourceId: "591_123456",
  canonicalUrl: "https://rent.591.com.tw/123456",
  address: "台北市信義區松高路12號",
  area: "45",
  floor: "5F",
  listingType: "店面",
  rent: "35000",
  status: "ACTIVE",
};

describe("Package 10 intake visual P1", () => {
  it("uses the exact canonical intake detail screen label", () => {
    render(<IntakeProcessingDetail onClose={vi.fn()} record={possibleMatchRecord} />);

    const surface = document.body.querySelector(
      '[data-screen-label="Intake 收件處理詳情頁"]',
    );
    expect(surface).not.toBeNull();
  });

  it("opens the canonical state matrix, renders every required contract, and closes with Escape", () => {
    render(<IntakeStageTimeline record={possibleMatchRecord} />);

    const trigger = byTestId("open-intake-state-matrix") as HTMLButtonElement;
    trigger.focus();
    act(() => trigger.click());

    const matrix = byTestId("intake-state-matrix");
    expect(matrix.getAttribute("data-screen-label")).toBe("Intake 狀態矩陣");
    expect(INTAKE_STAGE_MATRIX).toHaveLength(12);
    expect(SOURCE_POLICY_MATRIX).toHaveLength(5);
    expect(MATCH_OUTCOME_MATRIX).toHaveLength(5);
    expect(INTAKE_ERROR_MATRIX).toHaveLength(15);
    expect(byTestId("matrix-stages-CANCELLED").textContent).toContain("CANCELLED");
    expect(byTestId("matrix-source-policy-POLICY_UNKNOWN").textContent).toContain("fail-closed");
    expect(byTestId("matrix-match-outcomes-POSSIBLE_MATCH").textContent).toContain("絕不自動合併");
    expect(
      byTestId("matrix-errors-422 RISK_ACKNOWLEDGEMENT_REQUIRED").textContent,
    ).toContain("風險");

    act(() => {
      document.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Escape" }));
    });
    expect(document.body.querySelector('[data-testid="intake-state-matrix"]')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("renders a three-column desktop current/submitted comparison with text markers and a mobile fallback", () => {
    render(<ListingCompareTable record={possibleMatchRecord} targetListing={targetListing} />);

    const headers = Array.from(byTestId("compare-table-grid").querySelectorAll('[role="columnheader"]'));
    expect(headers).toHaveLength(3);
    expect(headers[1]?.textContent).toContain("既有物件");
    expect(headers[2]?.textContent).toContain("本次送件");
    expect(byTestId("signal-con-rent").textContent).toContain("▲ 矛盾");
    expect(byTestId("signal-match-address").textContent).toContain("✓ 一致");
    expect(byTestId("intake-desktop-required").textContent).toContain("DESKTOP_REQUIRED");
    expect(byTestId("intake-desktop-required").textContent).toContain("#intake/IN-3011");
  });
});
