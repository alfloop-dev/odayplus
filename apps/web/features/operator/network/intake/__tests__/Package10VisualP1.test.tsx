import React, { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { IntakeProcessingDetail } from "../IntakeProcessingDetail";
import { resolveTargetListing } from "../AssistedIntakeSection";
import { toTargetListingData } from "../../ListingRadarPanel";

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

describe("Package 10 intake visual P1", () => {
  it("uses the exact canonical intake detail screen label", () => {
    render(<IntakeProcessingDetail onClose={vi.fn()} record={possibleMatchRecord} />);

    const surface = document.body.querySelector(
      '[data-screen-label="Intake 收件處理詳情頁"]',
    );
    expect(surface).not.toBeNull();
  });

  it("renders a three-column desktop current/submitted comparison with text markers and a mobile fallback", () => {
    render(<IntakeProcessingDetail onClose={vi.fn()} record={possibleMatchRecord} />);

    const headers = Array.from(byTestId("compare-table-grid").querySelectorAll("thead th"));
    expect(headers).toHaveLength(3);
    expect(headers[1]?.textContent).toContain("既有物件");
    expect(headers[2]?.textContent).toContain("本次送件");
    expect(byTestId("signal-con-rent").textContent).toContain("▲ 矛盾");
    expect(byTestId("agreeing-signals-list").textContent).toContain("地址一致");
    expect(byTestId("contradicting-signals-list").textContent).toContain("35,000 → 38,000");
    expect(byTestId("intake-desktop-required").textContent).toContain("DESKTOP_REQUIRED");
    expect(byTestId("intake-desktop-required").textContent).toContain("/intake/IN-3011");
  });

  it("passes authoritative target fields by targetListingId and normalizes signal aliases", () => {
    const record = {
      ...possibleMatchRecord,
      matchResult: {
        ...possibleMatchRecord.matchResult,
        agreeingSignals: [
          { key: "normalizedAddress", label: "地址", agrees: true, detail: "API 地址一致" },
        ],
        contradictingSignals: [
          { key: "areaPing", label: "坪數", agrees: false, detail: "API 坪數矛盾" },
        ],
      },
    };
    const authoritativeTarget = toTargetListingData({
      id: "LST-1002",
      sourceId: "SRC-AUTH-22",
      sourceUrl: "https://example.test/listings/LST-1002",
      address: "台北市信義區松高路12號",
      areaPing: 45,
      floor: "5F",
      rentPerMonth: 35000,
      status: "parsed",
      heatZoneId: "HZ-TPE-XINYI",
      geocodeConfidence: 0.99,
      hardRuleFailures: [],
    });
    const target = resolveTargetListing(
      [{ id: "LST-OTHER", address: "不應顯示" }, authoritativeTarget],
      record.matchResult.targetListingId,
    );

    render(<IntakeProcessingDetail onClose={vi.fn()} record={record} targetListing={target} />);

    expect(byTestId("compare-row-sourceId").textContent).toContain("SRC-AUTH-22");
    expect(byTestId("compare-row-sourceUrl").textContent).toContain("https://example.test/listings/LST-1002");
    expect(byTestId("signal-unavailable-canonicalUrl")).toBeTruthy();
    expect(byTestId("signal-match-address").textContent).toContain("✓ 一致");
    expect(byTestId("signal-con-area").textContent).toContain("▲ 矛盾");
    expect(byTestId("compare-row-area").textContent).toContain("API 坪數矛盾");
  });

  it("marks absent authoritative values unavailable and never matched", () => {
    render(
      <IntakeProcessingDetail
        onClose={vi.fn()}
        record={possibleMatchRecord}
        targetListing={{ id: "LST-1002", address: "台北市信義區松高路12號" }}
      />,
    );

    expect(byTestId("signal-unavailable-listingType").textContent).toContain("[UNAVAILABLE]");
    expect(byTestId("compare-row-listingType").textContent).not.toContain("Matched");
    expect(byTestId("signal-unavailable-canonicalUrl").textContent).toContain("不可用");
    expect(document.body.querySelector('[data-testid="signal-match-listingType"]')).toBeNull();
  });
});
