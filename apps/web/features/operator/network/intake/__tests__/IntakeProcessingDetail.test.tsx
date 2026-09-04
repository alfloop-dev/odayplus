import React, { useSyncExternalStore } from "react";
import { readFileSync } from "node:fs";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AssistedIntake, IntakeInboxPage } from "@oday-plus/openapi-client";
import { OperatorConsole, isIntakeDetailOpen } from "../../../OperatorConsole";
import {
  AssistedIntakeSection,
  authoritativeAssignmentVersion,
  authoritativeSlaVersion,
  guardAssignmentResource,
  guardSlaResource,
  validResourceVersion,
} from "../AssistedIntakeSection";
import { IntakeProcessingDetail, buildPreservedInput } from "../IntakeProcessingDetail";
import { jobStatusBadgeColors } from "../IntakeStageTimeline";
import { isSnapshotStale } from "../intakeFreshness";

const nav = vi.hoisted(() => {
  const state = { search: "", pathname: "/operator", pushCalls: [] as string[], replaceCalls: [] as string[], listeners: new Set<() => void>() };
  const navigate = (url: string) => {
    state.search = url.includes("?") ? url.slice(url.indexOf("?") + 1) : "";
    for (const listener of state.listeners) listener();
  };
  return {
    state,
    reset(search = "", pathname = "/operator") {
      state.search = search;
      state.pathname = pathname;
      state.pushCalls = [];
      state.replaceCalls = [];
      state.listeners.clear();
    },
    replace(url: string) {
      state.replaceCalls.push(url);
      navigate(url);
    },
    push(url: string) {
      state.pushCalls.push(url);
      navigate(url);
    },
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: nav.replace, push: nav.push }),
  usePathname: () => nav.state.pathname,
  useSearchParams: () => {
    const search = useSyncExternalStore(
      (callback) => {
        nav.state.listeners.add(callback);
        return () => nav.state.listeners.delete(callback);
      },
      () => nav.state.search,
      () => nav.state.search,
    );
    return new URLSearchParams(search);
  },
}));

function intake(overrides: Partial<AssistedIntake> = {}): AssistedIntake {
  return {
    id: "IN-PRODUCTION-101",
    sourceId: "src-591",
    originalUrl: "https://source.example/listing/101",
    canonicalUrl: "https://canonical.example/listing/101",
    stage: "NEEDS_REVIEW",
    policy: "APPROVED_RETRIEVAL",
    policyLabel: "核准單頁讀取",
    policyReason: "SRC-591 v4",
    submitter: "operator-1",
    owner: "reviewer-2",
    heatZoneId: "HZ-TPE",
    rawSnapshot: null,
    snapshotId: null,
    capturedAt: "2026-07-26T10:00:00Z",
    parserVersion: undefined as unknown as string,
    correlationId: "corr-101",
    version: 4,
    assignmentStatus: "ASSIGNED",
    slaState: "ON_TRACK",
    auditEvents: [],
    parsedFields: {
      address_raw: {
        key: "address_raw", label: "地址", sourceValue: "台北市信義區松高路 1 號",
        normalizedValue: "台北市信義區松高路1號", correctedValue: "台北市信義區松高路 1 號 1F",
        correctionReason: "現場門牌確認", identity: true, lowConfidence: false,
      },
      rent_amount: {
        key: "rent_amount", label: "租金", sourceValue: null, normalizedValue: null,
        correctedValue: null, correctionReason: null, identity: false, lowConfidence: true,
      },
    },
    matchResult: {
      outcome: "POSSIBLE_MATCH",
      outcomeLabel: "疑似重複",
      targetListingId: "LST-101",
      confidence: 0.82,
      agreeingSignals: [{ key: "normalizedAddress", label: "地址", agrees: true, detail: "API 地址一致" }],
      contradictingSignals: [{ key: "areaPing", label: "坪數", agrees: false, detail: "API 坪數矛盾" }],
      summary: "需人工決策",
    },
    ...overrides,
  };
}

const possibleMatch = intake();

beforeEach(() => nav.reset());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("IntakeProcessingDetail production composition", () => {
  it("renders the exact continuous section order with timeline and audit last", () => {
    render(<IntakeProcessingDetail record={possibleMatch} targetListing={{ id: "LST-101", address: "台北市信義區松高路1號", area: 30 }} onClose={vi.fn()} onDecide={vi.fn()} />);
    const root = screen.getByTestId("intake-detail-dialog");
    const orderedIds = [
      "intake-detail-header",
      "intake-submission-summary",
      "assignment-sla-summary",
      "intake-processing-stages",
      "intake-source-policy-evidence",
      "intake-parsed-lineage",
      "intake-match-evidence-section",
      "intake-comparison-decision-section",
      "intake-detail-actions",
      "intake-receipts-section",
      "intake-timeline-audit-section",
    ];
    const positions = orderedIds.map((id) => Array.from(root.querySelectorAll("[data-testid]")).indexOf(root.querySelector(`[data-testid="${id}"]`)!));
    expect(positions.every((position, index) => index === 0 || position > positions[index - 1]!)).toBe(true);
    expect(root.querySelector('[data-testid="intake-detail-tabs"]')).toBeNull();
    expect(root.querySelector('[data-testid="open-intake-state-matrix"]')).toBeNull();
    expect(root.querySelector('[data-testid="tab-promotion"]')).toBeNull();
  });

  it("shows recovery only for authoritative failure states", () => {
    const { rerender } = render(<IntakeProcessingDetail record={possibleMatch} onClose={vi.fn()} />);
    expect(screen.queryByTestId("intake-error-recovery-section")).toBeNull();
    rerender(<IntakeProcessingDetail record={intake({ stage: "FAILED" })} error={{ code: "PARSER_FAILED", summary: "解析失敗", retryable: true, nextAction: "RETRY", correlationId: "corr-error", status: 500, occurredAt: "2026-07-26T10:01:00Z" }} onClose={vi.fn()} />);
    expect(screen.getByTestId("intake-error-recovery-section")).toHaveTextContent("PARSER_FAILED");
  });

  it("renders authoritative record failure when no action error exists", () => {
    render(<IntakeProcessingDetail
      record={intake({
        stage: "QUARANTINED",
        correlationId: "corr-record-failure",
        failure: {
          code: "SOURCE_POLICY_BLOCKED",
          summary: "來源政策拒絕擷取",
          nextAction: "REQUEST_POLICY_REVIEW",
          retryable: false,
        },
      })}
      onClose={vi.fn()}
    />);
    expect(screen.getByTestId("error-code")).toHaveTextContent("SOURCE_POLICY_BLOCKED");
    expect(screen.getByTestId("error-message")).toHaveTextContent("來源政策拒絕擷取");
    expect(screen.getByTestId("error-next-action")).toHaveTextContent("REQUEST_POLICY_REVIEW");
    expect(screen.getByTestId("error-retryable-badge")).toHaveTextContent("不可直接重試");
    expect(screen.getByTestId("error-correlation-id")).toHaveTextContent("corr-record-failure");
    expect(screen.getByTestId("error-occurred-at")).toHaveTextContent("UNAVAILABLE");
  });

  // ODP-P10-CAN-001-R3D: retrieval can fail before parsing starts, so a
  // parsedFields-only preserved-input view rendered `{}` and lost the URL the
  // operator submitted. Preserved input now reads the durable intake record.
  it("preserves the durable submitted URL when retrieval fails before parsing", () => {
    render(<IntakeProcessingDetail
      record={intake({
        stage: "FAILED",
        originalUrl: "https://www.synthetic.example/detail-50000001.html",
        canonicalUrl: "https://www.synthetic.example/detail-50000001.html",
        heatZoneId: "HZ-TPE",
        parsedFields: {},
        matchResult: null,
        failure: {
          code: "ODP-INTAKE-RETRIEVAL-TIMEOUT",
          summary: "來源頁擷取逾時（上游未於 10 秒內回應）。",
          nextAction: "稍後重試；已填寫的修正內容會保留。",
          retryable: true,
        },
      })}
      onClose={vi.fn()}
      onRetry={vi.fn()}
    />);

    fireEvent.click(screen.getByTestId("error-toggle-preserved-input"));
    const box = screen.getByTestId("error-preserved-input-box");
    expect(box).toHaveTextContent("https://www.synthetic.example/detail-50000001.html");
    expect(box).toHaveTextContent("HZ-TPE");
    expect(box.textContent).not.toBe("{}");
    expect(screen.getByTestId("error-retryable-badge")).toHaveTextContent("可自動重試");
  });

  it("layers corrected field values over the durable submission context", () => {
    render(<IntakeProcessingDetail
      record={intake({
        stage: "FAILED",
        failure: { code: "PARSER_FAILED", summary: "解析失敗", nextAction: "RETRY", retryable: true },
      })}
      onClose={vi.fn()}
    />);

    fireEvent.click(screen.getByTestId("error-toggle-preserved-input"));
    const box = screen.getByTestId("error-preserved-input-box");
    // corrected wins over normalized/source for a field the operator fixed
    expect(box).toHaveTextContent("台北市信義區松高路 1 號 1F");
    expect(box).toHaveTextContent("https://source.example/listing/101");
    expect(box).toHaveTextContent("https://canonical.example/listing/101");
    // rent_amount carries no value at any lineage level: absent, never fabricated
    expect(box).not.toHaveTextContent("rent_amount");
  });

  it("builds preserved input only from values the intake record supplies", () => {
    expect(buildPreservedInput(intake({
      originalUrl: "https://www.synthetic.example/detail-50000001.html",
      canonicalUrl: "",
      heatZoneId: null,
      sourceId: "src-591",
      parsedFields: {},
    }))).toEqual({
      originalUrl: "https://www.synthetic.example/detail-50000001.html",
      sourceId: "src-591",
    });

    expect(buildPreservedInput(intake({
      originalUrl: "",
      canonicalUrl: "",
      heatZoneId: null,
      sourceId: "",
      intakeMethod: undefined,
      parsedFields: {},
    }))).toBeNull();

    expect(buildPreservedInput(intake({
      originalUrl: "https://source.example/listing/101",
      canonicalUrl: "",
      heatZoneId: null,
      sourceId: "",
      parsedFields: {
        contact_phone: {
          key: "contact_phone", label: "聯絡電話", sourceValue: "0912-345-678",
          normalizedValue: "0912345678", correctedValue: null, correctionReason: null,
          identity: false, lowConfidence: false, masked: true, mask_reason_code: "PII_CONTACT",
        },
      },
    }))).toEqual({
      originalUrl: "https://source.example/listing/101",
      contact_phone: "•••• [MASKED]",
    });

    // Below-CONFIDENTIAL clearance: the API nulls originalUrl and flags it.
    // Withheld must read as withheld, never as an absent submission.
    expect(buildPreservedInput({
      ...intake({ originalUrl: null as unknown as string, canonicalUrl: "", heatZoneId: null, sourceId: "", parsedFields: {} }),
      originalUrl_masked: true,
    } as AssistedIntake)).toEqual({ originalUrl: "•••• [MASKED]" });
  });

  it("scopes the 390px CSS contract to POSSIBLE_MATCH and preserves typed values", () => {
    const css = readFileSync("features/operator/network/intake/intake.module.css", "utf8");
    const mobileCss = css.slice(css.indexOf("@media (max-width: 759px)"));
    expect(mobileCss).toMatch(/\.possibleMatchOutcome \.compareGrid,[\s\S]*?display:\s*none/);
    expect(mobileCss).toMatch(/\.possibleMatchOutcome \.intakeDecisionSection,[\s\S]*?display:\s*none/);
    expect(mobileCss).not.toMatch(/(?:^|\n)\s*\.compareGrid\s*\{\s*display:\s*none/);
    expect(mobileCss).not.toMatch(/\.unambiguousMatchOutcome[^{]*\{[^}]*display:\s*none/);

    const normal = intake({ stage: "READY", matchResult: { ...possibleMatch.matchResult!, outcome: "NEW", outcomeLabel: "新物件", targetListingId: null } });
    const { rerender } = render(<IntakeProcessingDetail record={normal} onClose={vi.fn()} onDecide={vi.fn()} />);
    expect(screen.getByTestId("intake-comparison-decision-section").className).toContain("unambiguousMatchOutcome");
    expect(screen.getByTestId("compare-table-grid")).toBeInTheDocument();
    expect(screen.getByTestId("intake-detail-actions").className).not.toContain("possibleMatchDecision");
    expect(screen.queryByTestId("intake-desktop-required")).toBeNull();
    rerender(<IntakeProcessingDetail record={possibleMatch} onClose={vi.fn()} onDecide={vi.fn()} />);
    expect(screen.getByTestId("intake-comparison-decision-section").className).toContain("possibleMatchOutcome");
    expect(screen.getByTestId("compare-table-grid")).toBeInTheDocument();
    expect(screen.getByTestId("intake-detail-actions").className).toContain("possibleMatchDecision");
    const desktopRequired = screen.getByTestId("intake-desktop-required");
    expect(desktopRequired).toHaveTextContent("DESKTOP_REQUIRED");
    expect(desktopRequired.querySelector("a")).toHaveAttribute("href", `/intake/${possibleMatch.id}`);
    expect(screen.getByTestId("intake-mobile-preserved-values")).toHaveTextContent("台北市信義區松高路 1 號 1F");
  });

  // ADD-006 §3.2: `display: flex` on native th/td removed table-cell layout, so
  // lineage and comparison columns collapsed into the first narrow column and
  // wrapped one character per line while most of the 1160px detail width stayed
  // blank. Cells keep table-cell layout; vertical composition lives in a wrapper.
  it("keeps native table-cell layout for lineage and comparison columns", () => {
    render(<IntakeProcessingDetail record={possibleMatch} targetListing={{ id: "LST-101", address: "台北市信義區松高路 1 號 1F", area: 30 }} onClose={vi.fn()} />);

    const compare = screen.getByTestId("compare-table-grid");
    const lineage = screen.getByTestId("intake-parsed-lineage").querySelector("table")!;
    expect(compare.tagName).toBe("TABLE");
    expect(lineage.tagName).toBe("TABLE");
    for (const table of [compare, lineage]) {
      expect(table.querySelector("thead")).not.toBeNull();
      expect(table.querySelector("tbody")).not.toBeNull();
      expect(table.querySelectorAll("tr").length).toBeGreaterThan(1);
      const cells = Array.from(table.querySelectorAll("th, td"));
      expect(cells.length).toBeGreaterThan(0);
      for (const cell of cells) {
        expect(["TH", "TD"]).toContain(cell.tagName);
        expect((cell as HTMLElement).style.display).toBe("");
      }
    }
    // Every stacked cell body is a wrapper inside the cell, never the cell itself.
    for (const cell of Array.from(compare.querySelectorAll("tbody th, tbody td"))) {
      expect(cell.firstElementChild?.className).toContain("fieldCellStack");
    }
    for (const cell of Array.from(lineage.querySelectorAll("tbody th, tbody td"))) {
      expect(cell.firstElementChild?.className).toContain("fieldCellStack");
    }

    const css = readFileSync("features/operator/network/intake/intake.module.css", "utf8");
    const desktopCss = css.slice(0, css.indexOf("@media"));
    expect(desktopCss).toMatch(/\.fieldCell \{[^}]*\}/);
    expect(desktopCss.match(/\.fieldCell \{[^}]*\}/)![0]).not.toMatch(/display:\s*(flex|grid|block)/);
    expect(desktopCss).toMatch(/\.fieldCellStack \{[^}]*display:\s*flex/);
    // Tablet (<=1024px) must not turn cells into flex/grid/block boxes either.
    const tabletCss = css.slice(css.indexOf("@media (max-width: 1024px)"), css.indexOf("@media (max-width: 759px)"));
    expect(tabletCss).not.toMatch(/\.(lineageGrid|compareGrid|fieldCell)[^{]*\{[^}]*display:\s*(flex|grid|block)/);
    // Only at <=759px does lineage stack, and it keeps the native elements.
    const mobileCss = css.slice(css.indexOf("@media (max-width: 759px)"));
    expect(mobileCss).toMatch(/\.lineageGrid th,\s*\.lineageGrid td,[\s\S]*?display:\s*block/);
  });

  it("integrates target comparison, canonical signals, and truthful corrected/missing lineage", () => {
    render(<IntakeProcessingDetail record={possibleMatch} targetListing={{ id: "LST-101", address: "台北市信義區松高路 1 號 1F", area: 30 }} onClose={vi.fn()} />);
    expect(screen.getByTestId("signal-match-address")).toHaveTextContent("一致");
    expect(screen.getByTestId("signal-con-area")).toHaveTextContent("矛盾");
    expect(screen.getByTestId("lineage-row-address_raw")).toHaveTextContent("台北市信義區松高路 1 號 1F");
    expect(screen.getByTestId("lineage-row-address_raw")).toHaveTextContent("現場門牌確認");
    expect(screen.getByTestId("lineage-row-rent_amount")).toHaveTextContent("[MISSING]");
    expect(screen.getByTestId("lineage-row-rent_amount")).toHaveTextContent("[LOW_CONFIDENCE]");
    expect(screen.getByTestId("evidence-snapshot-id")).toHaveTextContent("UNAVAILABLE");
    expect(screen.getByTestId("evidence-parser-version")).toHaveTextContent("UNAVAILABLE");
    expect(screen.queryByText("PR-RUN-88412")).toBeNull();
    expect(screen.getByTestId("compare-table-grid").tagName).toBe("TABLE");
  });

  it("fails target-dependent decisions closed without exact authoritative evidence", () => {
    render(<IntakeProcessingDetail record={possibleMatch} onClose={vi.fn()} onDecide={vi.fn()} onRefresh={vi.fn()} />);
    expect(screen.getByTestId("intake-target-decision-lock")).toHaveTextContent("AUTHORITATIVE_TARGET_UNAVAILABLE");
    expect(screen.getByTestId("decide-action-revise")).toBeDisabled();
    expect(screen.getByTestId("decide-action-dup")).toBeDisabled();
  });

  it("wires authoritative promotion receipt and score job into receipts and audit timeline", () => {
    render(<IntakeProcessingDetail
      currentOperator={{ id: "subject-1", name: "Reviewer", role: "expansion-manager" }}
      decisionReceipt={{
        promotion_decision_id: "PROMO-1", intake_id: possibleMatch.id, listing_id: "LST-101",
        candidate_site_id: "CAND-1", site_score_job_id: "JOB-1", status: "COMPLETED",
        version: 5, correlation_id: "corr-promo", audit_event_id: "audit-promo",
      } as any}
      jobs={[{ job_id: "JOB-1", status: "COMPLETED", checkpoint: "sitescore", attempt: 1, correlation_id: "corr-job" } as any]}
      onClose={vi.fn()}
      record={possibleMatch}
    />);
    expect(screen.getByText("PROMO-1")).toBeInTheDocument();
    expect(screen.getByTestId("timeline-job-panel")).toHaveTextContent("JOB-1");
    expect(screen.getByTestId("timeline-sla-panel")).toHaveTextContent("SLA RECEIPT: UNAVAILABLE");
    expect(screen.getByTestId("timeline-history-unavailable")).toHaveTextContent("UNAVAILABLE");
  });

  it("renders submitted, history, job, SLA and audit evidence truthfully", () => {
    const authoritativeAudit = intake({
      auditEvents: [{
        id: "AUD-101",
        occurredAt: "2026-07-26T10:05:00Z",
        actorRoleId: "data-steward",
        actorName: "Steward A",
        action: "intake.review",
        targetId: possibleMatch.id,
        message: "Reviewed",
        correlationId: null,
        metadata: {
          beforeAfter: { rent: { before: "SECRET-BEFORE", after: "SECRET-AFTER" } },
          nestedSecret: "SECRET-METADATA",
        },
      }],
    });
    render(<IntakeProcessingDetail record={authoritativeAudit} onClose={vi.fn()} />);
    expect(screen.getByText("送件時間 Submitted At").parentElement).toHaveTextContent("UNAVAILABLE");
    expect(screen.getByText("擷取時間 Captured At").parentElement).toHaveTextContent("2026-07-26T10:00:00Z");
    expect(screen.getByTestId("timeline-sla-panel")).toHaveTextContent("SLA 狀態: ON_TRACK");
    expect(screen.getByTestId("timeline-sla-receipt-state")).toHaveTextContent("SLA RECEIPT: UNAVAILABLE");
    expect(screen.getByTestId("timeline-job-unavailable")).toHaveTextContent("UNAVAILABLE");
    expect(screen.getByTestId("timeline-history-unavailable")).toHaveTextContent("UNAVAILABLE");
    expect(screen.getByTestId("intake-audit-references")).toHaveTextContent("Actor Role data-steward");
    expect(screen.getByTestId("intake-audit-references")).toHaveTextContent("Event Version UNAVAILABLE");
    expect(screen.getByTestId("intake-audit-references")).toHaveTextContent("Before/After UNAVAILABLE");
    expect(screen.getByTestId("intake-audit-references")).toHaveTextContent("Metadata UNAVAILABLE");
    expect(screen.getByTestId("intake-audit-references")).not.toHaveTextContent("SECRET-BEFORE");
    expect(screen.getByTestId("intake-audit-references")).not.toHaveTextContent("SECRET-METADATA");
    expect(screen.getByTestId("intake-audit-references")).toHaveTextContent("Correlation UNAVAILABLE");
    expect(screen.getByTestId("audit-references-unavailable")).toHaveTextContent("UNAVAILABLE");
    expect(screen.getByTestId("receipt-unavailable-state")).toHaveTextContent("UNAVAILABLE");
  });

  it("marks stale snapshots without inventing timestamps", () => {
    expect(isSnapshotStale("2000-01-01T00:00:00Z")).toBe(true);
    expect(isSnapshotStale(null)).toBe(false);
  });

  it("uses independent authoritative resource versions and never the intake version", () => {
    const record = intake({ version: 91 }) as AssistedIntake & {
      assignmentVersion: number;
      slaVersion: number;
    };
    record.assignmentVersion = 12;
    record.slaVersion = 34;
    expect(authoritativeAssignmentVersion(record)).toBe(12);
    expect(authoritativeSlaVersion(record)).toBe(34);
    expect(authoritativeAssignmentVersion(record, { version: 13 } as any)).toBe(13);
    expect(authoritativeSlaVersion(record, { version: 35 } as any)).toBe(35);

    const source = readFileSync("features/operator/network/intake/AssistedIntakeSection.tsx", "utf8");
    const assignmentAndSlaHandlers = source.slice(
      source.indexOf("async function handleClaim"),
      source.indexOf("// ---- Promotion saga handlers"),
    );
    expect(assignmentAndSlaHandlers).not.toMatch(/ifMatch: `W\/"\\?\\$\\{selected\\.version\\}/);
    expect(assignmentAndSlaHandlers).not.toContain('ifMatch: `W/"${selected.version}"`');
    expect(assignmentAndSlaHandlers).not.toContain('ifMatch: `W/"${record.version}"`');
    expect(guardAssignmentResource(intake({ assignmentId: "ASG-1" })).ok).toBe(false);
    expect(guardSlaResource(intake({ slaInstanceId: "SLA-1" })).ok).toBe(false);
    const assignmentError = guardAssignmentResource(intake({ assignmentId: "ASG-1" }));
    const slaError = guardSlaResource(intake({ slaInstanceId: "SLA-1" }));
    expect(!assignmentError.ok && assignmentError.error).toMatchObject({
      code: "RESOURCE_VERSION_UNAVAILABLE", occurredAt: "UNAVAILABLE", correlationId: null,
    });
    expect(!slaError.ok && slaError.error).toMatchObject({
      code: "RESOURCE_VERSION_UNAVAILABLE", occurredAt: "UNAVAILABLE", correlationId: null,
    });
  });

  it("uses deterministic WCAG-AA job badge colors for hydrated states", () => {
    for (const [status, deliveryState] of [
      ["RUNNING", null],
      ["FAILED", null],
      ["RUNNING", "RETRYING"],
      ["FAILED", "DEAD_LETTER"],
    ] as const) {
      const colors = jobStatusBadgeColors(status, deliveryState);
      expect(contrastRatio(colors.background, colors.foreground)).toBeGreaterThanOrEqual(4.5);
    }
  });
});

describe("AssistedIntakeSection production container", () => {
  it("marks only durable intake detail contexts for the global narrow-width override", () => {
    const detailParams = { ws: "network", tab: "radar", selected: possibleMatch.id, dialog: "detail" };
    expect(isIntakeDetailOpen(detailParams)).toBe(true);
    expect(isIntakeDetailOpen({ ...detailParams, dialog: undefined })).toBe(false);

    render(<OperatorConsole searchParams={detailParams} />);
    expect(screen.getByTestId("operator-console")).toHaveClass("operatorIntakeDetailOpen");
    expect(screen.getByTestId("operator-console")).toHaveAttribute("data-intake-detail-open", "true");
  });

  it("replaces the inbox with detail and restores filters and selection on return", async () => {
    const record = possibleMatch;
    const page: IntakeInboxPage = {
      items: [record], total: 1, page: 1, pageSize: 10,
      counts: { needsReview: 1, awaitingEntry: 0, processing: 0, blocked: 0, ready: 0 },
      evidenceState: "complete",
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path === "/api/v1/operator/network-listings/intake") return json(page);
      if (path === `/api/v1/intakes/${record.id}/promotion-decision`) return json({ code: "NOT_FOUND" }, 404);
      return json({ code: "NOT_FOUND" }, 404);
    }));
    nav.reset("stage=NEEDS_REVIEW&source=src-591&sort=submitted_desc&view=list");
    render(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="subject-1" />);
    fireEvent.click(await screen.findByTestId(`intake-inbox-row-${record.id}`));
    expect(nav.state.pushCalls).toHaveLength(1);
    expect(nav.state.pushCalls[0]).toContain("dialog=detail");
    expect(await screen.findByTestId("intake-detail-layer")).toBeInTheDocument();
    expect(screen.queryByTestId("intake-inbox-view")).toBeNull();
    fireEvent.click(screen.getByTestId("intake-return-button"));
    await waitFor(() => expect(screen.getByTestId("intake-inbox-view")).toBeInTheDocument());
    expect(nav.state.search).toContain("stage=NEEDS_REVIEW");
    expect(nav.state.search).toContain("source=src-591");
    expect(nav.state.search).toContain(`selected=${record.id}`);
    expect(nav.state.search).not.toContain("dialog=detail");
    expect(nav.state.replaceCalls.at(-1)).toContain("selected=");
    expect(nav.state.pushCalls).toHaveLength(1);
  });

  it("mounts a real direct pathname while unrelated shell bootstrap fails", async () => {
    const record = possibleMatch;
    window.sessionStorage.setItem("oday.operator.role", "expansion-manager");
    nav.reset(`stage=NEEDS_REVIEW&selected=${record.id}&dialog=detail`, `/intake/${record.id}`);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path === "/api/v1/operator/bootstrap") return json({ code: "SHELL_DOWN" }, 503);
      if (path === "/api/v1/operator/network-listings/intake") return json({
        items: [record], total: 1, page: 1, pageSize: 10,
        counts: { needsReview: 1, awaitingEntry: 0, processing: 0, blocked: 0, ready: 0 },
        evidenceState: "complete",
      });
      if (path === `/api/v1/intakes/${record.id}/promotion-decision`) return json({ code: "NOT_FOUND" }, 404);
      return json({ code: "NOT_FOUND" }, 404);
    }));
    render(<OperatorConsole searchParams={{ ws: "network", tab: "radar", selected: record.id, dialog: "detail", stage: "NEEDS_REVIEW" }} />);
    expect(await screen.findByTestId("intake-detail-dialog")).toBeInTheDocument();
    expect(screen.queryByTestId("operator-data-unavailable")).toBeNull();
  });

  it("keeps read-only production controls fail closed", async () => {
    const record = possibleMatch;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path === "/api/v1/operator/network-listings/intake") return json({
        items: [record], total: 1, page: 1, pageSize: 10,
        counts: { needsReview: 1, awaitingEntry: 0, processing: 0, blocked: 0, ready: 0 },
        evidenceState: "complete",
      });
      if (path === `/api/v1/intakes/${record.id}/promotion-decision`) return json({ code: "NOT_FOUND" }, 404);
      return json({ code: "NOT_FOUND" }, 404);
    }));
    nav.reset(`selected=${record.id}&dialog=detail`);
    render(<AssistedIntakeSection activeRoleId="pm-audit" activeSubjectId="auditor-1" initialDialog="detail" initialSelectedId={record.id} />);
    expect(await screen.findByTestId("intake-detail-dialog")).toBeInTheDocument();
    expect(screen.queryByTestId("fix-field-address_raw")).toBeNull();
    expect(screen.queryByTestId("asg-btn-claim")).toBeNull();
    expect(screen.queryByTestId("asg-btn-transfer")).toBeNull();
    expect(screen.queryByTestId("intake-detail-actions")).toBeNull();
  });

  it("claims only the authoritative assignment with its resource-specific If-Match", async () => {
    const record = intake({
      owner: "reviewer-2",
      assignmentId: "ASG-AUTH-101",
      assignmentStatus: "ASSIGNED",
      slaInstanceId: "SLA-AUTH-101",
      slaState: "ON_TRACK",
      version: 7,
      assignmentVersion: 3,
      slaVersion: 5,
    } as Partial<AssistedIntake> & { assignmentVersion: number; slaVersion: number });
    const requests: Array<{ path: string; method: string; headers: Headers; body: string | null }> = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), "http://localhost").pathname;
      requests.push({ path, method: init?.method ?? "GET", headers: new Headers(init?.headers), body: init?.body?.toString() ?? null });
      if (path === "/api/v1/operator/network-listings/intake") return json({
        items: [record], total: 1, page: 1, pageSize: 10,
        counts: { needsReview: 1, awaitingEntry: 0, processing: 0, blocked: 0, ready: 0 },
        evidenceState: "complete",
      });
      if (path === `/api/v1/assignments/${record.assignmentId}/actions/claim`) return json({
        assignment_id: record.assignmentId,
        status: "CLAIMED",
        owner_subject_id: "subject-1",
        version: 8,
        audit_event_id: "AUD-CLAIM-101",
      });
      if (path === `/api/v1/intakes/${record.id}`) return json({ ...record, owner: "subject-1", assignmentStatus: "CLAIMED", version: 8 });
      if (path === `/api/v1/intakes/${record.id}/promotion-decision`) return json({ code: "NOT_FOUND" }, 404);
      return json({ code: "NOT_FOUND" }, 404);
    }));
    nav.reset(`selected=${record.id}&dialog=detail`);
    render(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="subject-1" initialDialog="detail" initialSelectedId={record.id} />);
    expect(await screen.findByTestId("asg-btn-pause")).toBeInTheDocument();
    expect(screen.queryByTestId("asg-btn-resume")).toBeNull();
    fireEvent.click(await screen.findByTestId("asg-btn-claim"));
    await waitFor(() => expect(requests.some((request) => request.path === `/api/v1/assignments/${record.assignmentId}/actions/claim`)).toBe(true));
    const claim = requests.find((request) => request.path === `/api/v1/assignments/${record.assignmentId}/actions/claim`)!;
    expect(claim.method).toBe("POST");
    expect(claim.headers.get("if-match")).toBe('W/"3"');
    expect(requests.some((request) => request.path === `/api/v1/intakes/${record.id}/assignment`)).toBe(false);
    expect(claim.body).not.toContain("owner_subject_id");
    expect(claim.body).not.toContain("owner_role");
    expect(claim.body).not.toContain("due_at");
  });

  it("transfers with the authoritative assignment resource version", async () => {
    const record = intake({
      owner: "reviewer-2", assignmentId: "ASG-TRANSFER-101", assignmentStatus: "CLAIMED",
      version: 71, assignmentVersion: 14, slaInstanceId: "SLA-101", slaState: "ON_TRACK", slaVersion: 24,
    } as Partial<AssistedIntake> & { assignmentVersion: number; slaVersion: number });
    const requests: Array<{ path: string; headers: Headers }> = [];
    stubActionFetch(record, requests);
    nav.reset(`selected=${record.id}&dialog=detail`);
    render(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="subject-1" initialDialog="detail" initialSelectedId={record.id} />);
    fireEvent.click(await screen.findByTestId("asg-btn-transfer"));
    fireEvent.change(await screen.findByTestId("transfer-handoff-note"), { target: { value: "authoritative transfer" } });
    fireEvent.click(screen.getByTestId("transfer-risk-ack"));
    fireEvent.click(screen.getByTestId("transfer-submit-btn"));
    const path = `/api/v1/assignments/${record.assignmentId}/actions/transfer`;
    await waitFor(() => expect(requests.some((request) => request.path === path)).toBe(true));
    expect(requests.find((request) => request.path === path)!.headers.get("if-match")).toBe('W/"14"');
  });

  it("pauses with the authoritative SLA resource version", async () => {
    const record = intake({
      owner: "reviewer-2", assignmentId: "ASG-101", assignmentStatus: "CLAIMED",
      version: 72, assignmentVersion: 15, slaInstanceId: "SLA-PAUSE-101", slaState: "ON_TRACK", slaVersion: 25,
    } as Partial<AssistedIntake> & { assignmentVersion: number; slaVersion: number });
    const requests: Array<{ path: string; headers: Headers }> = [];
    stubActionFetch(record, requests);
    nav.reset(`selected=${record.id}&dialog=detail`);
    render(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="subject-1" initialDialog="detail" initialSelectedId={record.id} />);
    fireEvent.click(await screen.findByTestId("asg-btn-pause"));
    fireEvent.change(await screen.findByTestId("pause-reason-input"), { target: { value: "waiting for evidence" } });
    fireEvent.change(screen.getByTestId("pause-resume-time-input"), { target: { value: "2026-07-27T10:00" } });
    fireEvent.click(screen.getByTestId("pause-risk-ack"));
    fireEvent.click(screen.getByTestId("pause-submit-btn"));
    const path = `/api/v1/sla-instances/${record.slaInstanceId}/actions/pause`;
    await waitFor(() => expect(requests.some((request) => request.path === path)).toBe(true));
    expect(requests.find((request) => request.path === path)!.headers.get("if-match")).toBe('W/"25"');
  });

  it("resumes with the authoritative SLA resource version", async () => {
    const record = intake({
      owner: "reviewer-2", assignmentId: "ASG-101", assignmentStatus: "CLAIMED",
      version: 73, assignmentVersion: 16, slaInstanceId: "SLA-RESUME-101", slaState: "PAUSED", slaVersion: 26,
    } as Partial<AssistedIntake> & { assignmentVersion: number; slaVersion: number });
    const requests: Array<{ path: string; headers: Headers }> = [];
    stubActionFetch(record, requests);
    nav.reset(`selected=${record.id}&dialog=detail`);
    render(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="subject-1" initialDialog="detail" initialSelectedId={record.id} />);
    fireEvent.click(await screen.findByTestId("asg-btn-resume"));
    const path = `/api/v1/sla-instances/${record.slaInstanceId}/actions/resume`;
    await waitFor(() => expect(requests.some((request) => request.path === path)).toBe(true));
    expect(requests.find((request) => request.path === path)!.headers.get("if-match")).toBe('W/"26"');
  });

  it("fails assignment and SLA controls closed when resource versions are missing", async () => {
    const record = intake({
      owner: "reviewer-2",
      assignmentId: "ASG-NO-VERSION",
      assignmentStatus: "ASSIGNED",
      slaInstanceId: "SLA-NO-VERSION",
      slaState: "ON_TRACK",
    });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path === "/api/v1/operator/network-listings/intake") return json({
        items: [record], total: 1, page: 1, pageSize: 10,
        counts: { needsReview: 1, awaitingEntry: 0, processing: 0, blocked: 0, ready: 0 },
        evidenceState: "complete",
      });
      if (path === `/api/v1/intakes/${record.id}/promotion-decision`) return json({ code: "NOT_FOUND" }, 404);
      return json({ code: "NOT_FOUND" }, 404);
    }));
    nav.reset(`selected=${record.id}&dialog=assignmentSla&decision=transfer`);
    render(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="subject-1" initialDialog="detail" initialSelectedId={record.id} />);
    expect(await screen.findByTestId("assignment-resource-version-unavailable")).toHaveTextContent("RESOURCE_VERSION_UNAVAILABLE");
    expect(screen.getByTestId("sla-resource-version-unavailable")).toHaveTextContent("RESOURCE_VERSION_UNAVAILABLE");
    expect(screen.queryByTestId("asg-btn-claim")).toBeNull();
    expect(screen.queryByTestId("asg-btn-transfer")).toBeNull();
    expect(screen.queryByTestId("asg-btn-pause")).toBeNull();
    expect(screen.queryByTestId("asg-btn-resume")).toBeNull();
    expect(screen.queryByTestId("transfer-intake-dialog")).toBeNull();
    const guardCalls = [
      guardAssignmentResource(record),
      guardSlaResource(record),
    ];
    expect(guardCalls.every((result) => !result.ok && result.error.code === "RESOURCE_VERSION_UNAVAILABLE")).toBe(true);
    expect((fetch as ReturnType<typeof vi.fn>).mock.calls.some(([input]) =>
      /\/api\/v1\/(assignments|sla-instances)\//.test(String(input)),
    )).toBe(false);
  });

  // ADD-006 §3.3: the API If-Match contract is `^W/"[1-9][0-9]*"$`, so version
  // zero is not a usable concurrency token and must fail closed alongside
  // negatives, fractions, strings, null and undefined.
  it("accepts positive integer resource versions only", () => {
    for (const rejected of [0, -1, -12, 1.5, 0.5, "1", "0", "", true, null, undefined, NaN, Infinity, {}, [3]]) {
      expect(validResourceVersion(rejected)).toBeNull();
    }
    for (const accepted of [1, 2, 12, 34, Number.MAX_SAFE_INTEGER]) {
      expect(validResourceVersion(accepted)).toBe(accepted);
    }
    expect(authoritativeAssignmentVersion(intake({ assignmentVersion: 0 } as Partial<AssistedIntake>))).toBeNull();
    expect(authoritativeSlaVersion(intake({ slaVersion: 0 } as Partial<AssistedIntake>))).toBeNull();
    expect(authoritativeAssignmentVersion(intake(), { version: 0 } as any)).toBeNull();
    expect(authoritativeSlaVersion(intake(), { version: 0 } as any)).toBeNull();
  });

  it.each([
    ["zero", 0, 0],
    ["negative", -1, -2],
    ["fraction", 1.5, 2.5],
    ["string", "3" as unknown as number, "4" as unknown as number],
    ["null", null as unknown as number, null as unknown as number],
    ["undefined", undefined as unknown as number, undefined as unknown as number],
  ])(
    "fails claim, transfer, pause and resume closed for %s resource versions",
    async (_label, assignmentVersion, slaVersion) => {
      const record = intake({
        owner: "reviewer-2",
        assignmentId: "ASG-REJECTED-VERSION",
        assignmentStatus: "ASSIGNED",
        slaInstanceId: "SLA-REJECTED-VERSION",
        slaState: "ON_TRACK",
        version: 91,
        assignmentVersion,
        slaVersion,
      } as Partial<AssistedIntake> & { assignmentVersion: unknown; slaVersion: unknown });
      vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
        const path = new URL(String(input), "http://localhost").pathname;
        if (path === "/api/v1/operator/network-listings/intake") return json({
          items: [record], total: 1, page: 1, pageSize: 10,
          counts: { needsReview: 1, awaitingEntry: 0, processing: 0, blocked: 0, ready: 0 },
          evidenceState: "complete",
        });
        if (path === `/api/v1/intakes/${record.id}/promotion-decision`) return json({ code: "NOT_FOUND" }, 404);
        return json({ code: "NOT_FOUND" }, 404);
      }));
      nav.reset(`selected=${record.id}&dialog=assignmentSla&decision=transfer`);
      render(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="subject-1" initialDialog="detail" initialSelectedId={record.id} />);

      expect(await screen.findByTestId("assignment-resource-version-unavailable")).toHaveTextContent("RESOURCE_VERSION_UNAVAILABLE");
      expect(screen.getByTestId("sla-resource-version-unavailable")).toHaveTextContent("RESOURCE_VERSION_UNAVAILABLE");
      expect(screen.queryByTestId("asg-btn-claim")).toBeNull();
      expect(screen.queryByTestId("asg-btn-transfer")).toBeNull();
      expect(screen.queryByTestId("asg-btn-pause")).toBeNull();
      expect(screen.queryByTestId("asg-btn-resume")).toBeNull();
      expect(screen.queryByTestId("transfer-intake-dialog")).toBeNull();
      expect(screen.queryByTestId("pause-sla-dialog")).toBeNull();

      const assignmentGuard = guardAssignmentResource(record);
      const slaGuard = guardSlaResource(record);
      expect(!assignmentGuard.ok && assignmentGuard.error.code).toBe("RESOURCE_VERSION_UNAVAILABLE");
      expect(!slaGuard.ok && slaGuard.error.code).toBe("RESOURCE_VERSION_UNAVAILABLE");
      expect((fetch as ReturnType<typeof vi.fn>).mock.calls.some(([input]) =>
        /\/api\/v1\/(assignments|sla-instances)\/[^/]+\/actions\/(claim|transfer|pause|resume)$/.test(
          new URL(String(input), "http://localhost").pathname,
        ),
      )).toBe(false);
    },
  );
});

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function stubActionFetch(
  record: AssistedIntake,
  requests: Array<{ path: string; headers: Headers }>,
) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), "http://localhost").pathname;
    requests.push({ path, headers: new Headers(init?.headers) });
    if (path === "/api/v1/operator/network-listings/intake") return json({
      items: [record], total: 1, page: 1, pageSize: 10,
      counts: { needsReview: 1, awaitingEntry: 0, processing: 0, blocked: 0, ready: 0 },
      evidenceState: "complete",
    });
    if (path === `/api/v1/intakes/${record.id}`) return json(record);
    if (path === `/api/v1/intakes/${record.id}/promotion-decision`) return json({ code: "NOT_FOUND" }, 404);
    if (path.includes("/assignments/")) return json({
      assignment_id: record.assignmentId, status: "CLAIMED", owner_subject_id: "subject-1",
      version: 99, audit_event_id: "AUD-ASG-101",
    });
    if (path.includes("/sla-instances/")) return json({
      sla_instance_id: record.slaInstanceId, state: path.endsWith("/pause") ? "PAUSED" : "ON_TRACK",
      version: 98, paused_duration_seconds: 0, correlation_id: "corr-sla",
      audit_event_id: "AUD-SLA-101",
    });
    return json({ code: "NOT_FOUND" }, 404);
  }));
}

function contrastRatio(background: string, foreground: string): number {
  const luminance = (hex: string) => {
    const rgb = [1, 3, 5].map((start) => Number.parseInt(hex.slice(start, start + 2), 16) / 255);
    const linear = rgb.map((value) => value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    return 0.2126 * linear[0]! + 0.7152 * linear[1]! + 0.0722 * linear[2]!;
  };
  const [lighter, darker] = [luminance(background), luminance(foreground)].sort((a, b) => b - a);
  return (lighter! + 0.05) / (darker! + 0.05);
}
