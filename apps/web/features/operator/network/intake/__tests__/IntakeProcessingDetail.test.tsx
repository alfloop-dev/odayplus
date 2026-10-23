import React, { useSyncExternalStore } from "react";
import { readFileSync } from "node:fs";
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AssistedIntake, IntakeInboxPage } from "@oday-plus/openapi-client";
import { OperatorConsole, isIntakeDetailOpen } from "../../../OperatorConsole";
import "../../../NetworkFindAreasWorkspace";
import {
  AssistedIntakeSection,
  authoritativeAssignmentVersion,
  authoritativeSlaVersion,
  guardAssignmentResource,
  guardSlaResource,
  validResourceVersion,
} from "../AssistedIntakeSection";
import { IntakeProcessingDetail, buildPreservedInput, hasAuthoritativeTargetEvidence } from "../IntakeProcessingDetail";
import { IntakeDecisionDialog } from "../IntakeDecisionDialog";
import { decisionOptions, matchConfidence } from "../intakeTypes";
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

const transferTarget = { subject_id: "10000000-0000-4000-8000-000000000002", display_name: "Synthetic steward", owner_role: "data-steward" };
const transferTargetValue = JSON.stringify([transferTarget.subject_id, transferTarget.owner_role]);
async function selectTransferTarget() {
  await waitFor(() => expect(screen.getByTestId("transfer-target-select")).not.toBeDisabled());
  fireEvent.change(screen.getByTestId("transfer-target-select"), { target: { value: transferTargetValue } });
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
    const record = intake({ version: 91, assignmentId: "ASG-VERSION", slaInstanceId: "SLA-VERSION" }) as AssistedIntake & {
      assignmentVersion: number;
      slaVersion: number;
    };
    record.assignmentVersion = 12;
    record.slaVersion = 34;
    expect(authoritativeAssignmentVersion(record)).toBe(12);
    expect(authoritativeSlaVersion(record)).toBe(34);
    expect(authoritativeAssignmentVersion(record, { assignment_id: record.assignmentId, version: 13 } as any)).toBe(13);
    expect(authoritativeSlaVersion(record, { sla_instance_id: record.slaInstanceId, version: 35 } as any)).toBe(35);
    // A successful command receipt stays visible as evidence; it cannot pin a
    // later refreshed resource to an obsolete concurrency token.
    expect(authoritativeAssignmentVersion(record, { assignment_id: record.assignmentId, version: 11 } as any)).toBe(12);
    expect(authoritativeSlaVersion(record, { sla_instance_id: record.slaInstanceId, version: 33 } as any)).toBe(34);
    expect(authoritativeAssignmentVersion(record, { assignment_id: "OTHER", version: 99 } as any)).toBe(12);
    expect(authoritativeSlaVersion(record, { sla_instance_id: "OTHER", version: 99 } as any)).toBe(34);

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
  it("restores direct inbox filters on the first API request and preserves other parameters", async () => {
    const requests: URL[] = [];
    nav.reset("ws=network&tab=radar&search=台北&savedView=needsReview&page=3&pageSize=25&intakeStage=NEEDS_REVIEW&heatZoneId=HZ-TPE&sortBy=sourceId&sortOrder=asc&extra=a&extra=b&source=shell-context&sourceId=source-1&submittedBy=subject-1&ownerId=subject-2&assignmentStatus=CLAIMED&assignedAreaId=area-1&masked=false&restricted=true&retryable=false&observedFrom=2026-10-01T00%3A00%3A00Z&observedTo=2026-10-02T00%3A00%3A00Z&updatedFrom=2026-10-03T00%3A00%3A00Z&updatedTo=2026-10-04T00%3A00%3A00Z");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost"); requests.push(url);
      return json({ items: [possibleMatch], total: 60, page: 3, pageSize: 25, counts: { needsReview: 60, awaitingEntry: 0, processing: 0, blocked: 0, ready: 0 }, evidenceState: "complete" });
    }));
    render(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="subject-1" selectedHeatZoneId="HZ-DEFAULT" />);
    expect(await screen.findByTestId("intake-inbox-view")).toBeInTheDocument();
    await waitFor(() => expect(requests.length).toBeGreaterThan(0));
    expect(screen.getByTestId("intake-page-size-select")).toHaveValue("25");
    expect(Object.fromEntries(requests[0]!.searchParams)).toMatchObject({ search: "台北", savedView: "needsReview", page: "3", pageSize: "25", intakeStage: "NEEDS_REVIEW", selectedHeatZoneId: "HZ-TPE", sortBy: "sourceId", sortOrder: "asc",
      sourceId: "source-1", submittedBy: "subject-1", ownerId: "subject-2", assignmentStatus: "CLAIMED", assignedAreaId: "area-1",
      masked: "false", restricted: "true", retryable: "false", observedFrom: "2026-10-01T00:00:00Z",
      observedTo: "2026-10-02T00:00:00Z", updatedFrom: "2026-10-03T00:00:00Z", updatedTo: "2026-10-04T00:00:00Z" });
    expect(requests[0]!.searchParams.has("source")).toBe(false);
    expect(nav.state.replaceCalls).toHaveLength(0);
    fireEvent.click(screen.getByTestId("intake-view-mode-map"));
    expect(new URLSearchParams(nav.state.search).getAll("extra")).toEqual(["a", "b"]);
    expect(new URLSearchParams(nav.state.search).get("viewMode")).toBe("map");
    expect(new URLSearchParams(nav.state.search).get("source")).toBe("shell-context");
  });

  it("discards a delayed inbox response after navigation to another filter", async () => {
    let resolveOld!: (response: Response) => void;
    nav.reset("search=old");
    const page = (record: AssistedIntake) => ({ items: [record], total: 1, page: 1, pageSize: 10, counts: { needsReview: 1, awaitingEntry: 0, processing: 0, blocked: 0, ready: 0 }, evidenceState: "complete" });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      return url.searchParams.get("search") === "old" ? new Promise<Response>((resolve) => { resolveOld = resolve; }) : Promise.resolve(json(page(intake({ id: "IN-NEW" }))));
    }));
    render(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="subject-1" />);
    await waitFor(() => expect(resolveOld).toBeDefined());
    fireEvent.change(screen.getByTestId("intake-search-input"), { target: { value: "new" } });
    expect(await screen.findByTestId("intake-inbox-row-IN-NEW")).toBeInTheDocument();
    await act(async () => { resolveOld(json(page(intake({ id: "IN-OLD" })))); });
    expect(screen.queryByTestId("intake-inbox-row-IN-OLD")).toBeNull();
  });

  it.each(["selected=IN-1&selected=IN-2&dialog=detail", "selected=&dialog=detail"])("refuses an ambiguous direct detail link %s", async (query) => {
    nav.reset(query);
    vi.stubGlobal("fetch", vi.fn(async () => json({ items: [possibleMatch], total: 1, page: 1, pageSize: 10, counts: {}, evidenceState: "complete" })));
    render(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="subject-1" initialSelectedId={possibleMatch.id} />);
    expect(await screen.findByTestId("intake-direct-detail-error")).toHaveTextContent("連結未指定唯一");
    expect(screen.queryByTestId("intake-detail-dialog")).toBeNull();
  });

  it("does not replace a malformed pathname identifier with a valid query record", async () => {
    nav.reset(`selected=${possibleMatch.id}&dialog=detail`, "/intake/%ZZ");
    const fetch = vi.fn(async (_input: RequestInfo | URL) => json({ items: [possibleMatch], total: 1, page: 1, pageSize: 10, counts: {}, evidenceState: "complete" }));
    vi.stubGlobal("fetch", fetch);
    render(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="subject-1" />);
    expect(await screen.findByTestId("intake-direct-detail-error")).toHaveTextContent("連結未指定唯一");
    expect(screen.queryByTestId("intake-detail-dialog")).toBeNull();
    expect(fetch.mock.calls.every((call) => !String(call[0]).includes(`/intake/${possibleMatch.id}`))).toBe(true);
  });

  it("uses the durable pathname record and remembers the interacted detail section", async () => {
    nav.reset("selected=IN-OTHER&dialog=detail&extra=a&extra=b", `/intake/${possibleMatch.id}`);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path === `/api/v1/operator/network-listings/intake/${possibleMatch.id}`) return json(possibleMatch);
      if (path.endsWith("/promotion-decision")) return json({ code: "NOT_FOUND" }, 404);
      return json({ items: [], total: 0, page: 1, pageSize: 10, counts: {}, evidenceState: "complete" });
    }));
    render(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="subject-1" />);
    fireEvent.click(await screen.findByTestId("intake-match-evidence-section"));
    const saved = new URLSearchParams(nav.state.search);
    expect(saved.get("section")).toBe("identity");
    expect(saved.get("selected")).toBe(possibleMatch.id);
    expect(saved.getAll("extra")).toEqual(["a", "b"]);
  });

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
    // The test module imports the real panel before the timed interaction.
    // Exercise routing and authoritative intake binding; fresh-browser cold
    // module loading is verified separately.
    render(<OperatorConsole searchParams={{ ws: "network", tab: "radar", selected: record.id, dialog: "detail", stage: "NEEDS_REVIEW" }} />);
    expect(await screen.findByTestId("intake-detail-dialog")).toBeInTheDocument();
    expect(screen.queryByTestId("operator-data-unavailable")).toBeNull();
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(screen.getByRole("region", { name: "收件處理詳情" })).toBeInTheDocument();
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
    await selectTransferTarget();
    fireEvent.change(await screen.findByTestId("transfer-handoff-note"), { target: { value: "authoritative transfer" } });
    fireEvent.click(screen.getByTestId("transfer-risk-ack"));
    fireEvent.click(screen.getByTestId("transfer-submit-btn"));
    const path = `/api/v1/assignments/${record.assignmentId}/actions/transfer`;
    await waitFor(() => expect(requests.some((request) => request.path === path)).toBe(true));
    expect(requests.find((request) => request.path === path)!.headers.get("if-match")).toBe('W/"14"');
  });

  it.each(["unavailable", "empty", "wrong-resource", "newer-version"])(
    "does not offer a transfer from a %s directory response",
    async (state) => {
      const record = intake({ assignmentId: "ASG-DIRECTORY", assignmentVersion: 14 } as Partial<AssistedIntake> & { assignmentVersion: number });
      const mutations: string[] = [];
      vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input), "http://localhost").pathname;
        if (init?.method === "POST") mutations.push(path);
        if (path === "/api/v1/operator/network-listings/intake") return json({
          items: [record], total: 1, page: 1, pageSize: 10,
          counts: { needsReview: 1, awaitingEntry: 0, processing: 0, blocked: 0, ready: 0 }, evidenceState: "complete",
        });
        if (path.endsWith("/transfer-targets")) return state === "unavailable"
          ? json({ code: "DIRECTORY_UNAVAILABLE" }, 503)
          : json({ assignment_id: state === "wrong-resource" ? "OTHER" : record.assignmentId,
            assignment_version: state === "newer-version" ? 15 : 14, items: state === "empty" ? [] : [transferTarget] });
        return json({ code: "NOT_FOUND" }, 404);
      }));
      nav.reset(`selected=${record.id}&dialog=assignmentSla&decision=transfer`);
      render(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="subject-1" />);
      expect(await screen.findByTestId("transfer-targets-unavailable")).toBeInTheDocument();
      expect(screen.getByTestId("transfer-submit-btn")).toBeDisabled();
      expect(screen.queryByRole("option", { name: /Synthetic steward/ })).toBeNull();
      fireEvent.click(screen.getByTestId("transfer-submit-btn"));
      expect(mutations).toEqual([]);
    },
  );

  it("reports an unsuccessful conflict refresh while preserving the entered pause draft", async () => {
    const record = intake({ slaInstanceId: "SLA-REFRESH", slaState: "ON_TRACK", slaVersion: 24 } as Partial<AssistedIntake> & { slaVersion: number });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path === "/api/v1/operator/network-listings/intake") return json({
        items: [record], total: 1, page: 1, pageSize: 10,
        counts: { needsReview: 1, awaitingEntry: 0, processing: 0, blocked: 0, ready: 0 }, evidenceState: "complete",
      });
      if (path === "/api/v1/sla-instances/SLA-REFRESH/actions/pause") return json({ code: "VERSION_CONFLICT" }, 409);
      if (path === `/api/v1/operator/network-listings/intake/${record.id}`) return json({ code: "REFRESH_UNAVAILABLE" }, 503);
      return json({ code: "NOT_FOUND" }, 404);
    }));
    nav.reset(`selected=${record.id}&dialog=detail`);
    render(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="subject-1" />);
    fireEvent.click(await screen.findByTestId("asg-btn-pause"));
    fireEvent.change(await screen.findByTestId("pause-reason-input"), { target: { value: "Keep this reason" } });
    fireEvent.change(screen.getByTestId("pause-resume-time-input"), { target: { value: "2026-10-14T11:00" } });
    fireEvent.click(screen.getByTestId("pause-risk-ack"));
    fireEvent.click(screen.getByTestId("pause-submit-btn"));
    fireEvent.click(await screen.findByTestId("pause-conflict-refresh-btn"));
    expect(await screen.findByTestId("pause-error-panel")).toHaveTextContent("503");
    expect(screen.getByTestId("pause-reason-input")).toHaveValue("Keep this reason");
    expect(screen.getByTestId("pause-resume-time-input")).toHaveValue("2026-10-14T11:00");
    expect(screen.getByTestId("pause-record-version")).toHaveTextContent("v24");
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

  it.each(["assignment", "sla"] as const)(
    "retries %s conflicts with the refreshed resource version after an earlier successful receipt",
    async (resource) => {
      let record = intake({
        owner: "subject-1", assignmentId: "ASG-CONFLICT", assignmentStatus: "ASSIGNED",
        version: 71, assignmentVersion: 14, slaInstanceId: "SLA-CONFLICT", slaState: "ON_TRACK", slaVersion: 24,
      } as Partial<AssistedIntake> & { assignmentVersion: number; slaVersion: number });
      const commands: Array<{ path: string; headers: Headers; body: Record<string, unknown> }> = [];
      let conflicted = false;
      const resourcePath = resource === "assignment" ? "/api/v1/assignments/ASG-CONFLICT" : "/api/v1/sla-instances/SLA-CONFLICT";
      vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input), "http://localhost").pathname;
        if (path === "/api/v1/operator/network-listings/intake") return json({
          items: [record], total: 1, page: 1, pageSize: 10,
          counts: { needsReview: 1, awaitingEntry: 0, processing: 0, blocked: 0, ready: 0 }, evidenceState: "complete",
        });
        if (path === `/api/v1/operator/network-listings/intake/${record.id}`) return json(record);
        if (path.endsWith("/transfer-targets")) return json({ assignment_id: record.assignmentId, assignment_version: (record as any).assignmentVersion, items: [transferTarget] });
        if (path.startsWith(`${resourcePath}/actions/`)) {
          commands.push({ path, headers: new Headers(init?.headers), body: JSON.parse(String(init?.body)) });
          if (commands.length === 1) {
            record = { ...record, assignmentStatus: "CLAIMED", assignmentVersion: 15, slaState: "PAUSED", slaVersion: 25 } as AssistedIntake;
            return resource === "assignment"
              ? json({ assignment_id: record.assignmentId, version: 15, status: "CLAIMED", owner_subject_id: "subject-1", audit_event_id: "AUD-CLAIM" })
              : json({ sla_instance_id: record.slaInstanceId, version: 25, state: "PAUSED", paused_duration_seconds: 0, audit_event_id: "AUD-PAUSE", correlation_id: "CORR-PAUSE" });
          }
          if (!conflicted) {
            conflicted = true;
            record = { ...record, owner: "updated-owner", assignmentVersion: 17, slaState: "ON_TRACK", slaVersion: 27 } as AssistedIntake;
            return json({ code: "OWNER_CONFLICT", message: "Version changed" }, 409);
          }
          return resource === "assignment"
            ? json({ assignment_id: record.assignmentId, version: 18, status: "CLAIMED", owner_subject_id: "updated-owner", audit_event_id: "AUD-TRANSFER" })
            : json({ sla_instance_id: record.slaInstanceId, version: 28, state: "PAUSED", paused_duration_seconds: 0, audit_event_id: "AUD-PAUSE-2", correlation_id: "CORR-PAUSE-2" });
        }
        return json({ code: "NOT_FOUND" }, 404);
      }));
      nav.reset(`selected=${record.id}&dialog=detail`);
      render(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="subject-1" />);
      if (resource === "assignment") {
        fireEvent.click(await screen.findByTestId("asg-btn-claim"));
        await waitFor(() => expect(screen.getByTestId("receipt-asg-id")).toHaveTextContent("ASG-CONFLICT"));
        fireEvent.click(screen.getByTestId("asg-btn-transfer"));
        await selectTransferTarget();
    fireEvent.change(await screen.findByTestId("transfer-handoff-note"), { target: { value: "Preserve this handoff" } });
        fireEvent.click(screen.getByTestId("transfer-risk-ack"));
        fireEvent.click(screen.getByTestId("transfer-submit-btn"));
        fireEvent.click(await screen.findByTestId("transfer-conflict-refresh-btn"));
        await waitFor(() => expect(screen.queryByTestId("transfer-conflict-panel")).toBeNull());
        expect(screen.getByTestId("transfer-handoff-note")).toHaveValue("Preserve this handoff");
        await waitFor(() => expect(screen.getByTestId("transfer-submit-btn")).not.toBeDisabled());
        fireEvent.click(screen.getByTestId("transfer-submit-btn"));
      } else {
        fireEvent.click(await screen.findByTestId("asg-btn-pause"));
        fireEvent.change(await screen.findByTestId("pause-reason-input"), { target: { value: "Initial hold" } });
        fireEvent.change(screen.getByTestId("pause-resume-time-input"), { target: { value: "2026-10-14T10:00" } });
        fireEvent.click(screen.getByTestId("pause-risk-ack"));
        fireEvent.click(screen.getByTestId("pause-submit-btn"));
        fireEvent.click(await screen.findByTestId("asg-btn-resume"));
        await waitFor(() => expect(screen.getByTestId("intake-detail-dialog")).toHaveTextContent("ODP-INTAKE-CONFLICT"));
        // The failed resume reports that another actor has resumed the SLA.
        fireEvent.click(screen.getByTestId("intake-refresh-button"));
        fireEvent.click(await screen.findByTestId("asg-btn-pause"));
        fireEvent.change(await screen.findByTestId("pause-reason-input"), { target: { value: "Preserve this reason" } });
        fireEvent.change(screen.getByTestId("pause-resume-time-input"), { target: { value: "2026-10-14T11:00" } });
        fireEvent.click(screen.getByTestId("pause-risk-ack"));
        fireEvent.click(screen.getByTestId("pause-submit-btn"));
      }
      await waitFor(() => expect(commands).toHaveLength(3));
      expect(commands[0]!.headers.get("if-match")).toBe(resource === "assignment" ? 'W/"14"' : 'W/"24"');
      expect(commands[1]!.headers.get("if-match")).toBe(resource === "assignment" ? 'W/"15"' : 'W/"25"');
      expect(commands[2]!.headers.get("if-match")).toBe(resource === "assignment" ? 'W/"17"' : 'W/"27"');
    },
  );

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

  it.each(["claim", "transfer", "pause", "resume"] as const)(
    "replays an unacknowledged %s with the same key, payload and original If-Match",
    async (action) => {
      const flow = setupResourceCommand(action, ["lost", "success"]);
      await flow.open();
      fireEvent.click(screen.getByTestId(flow.submitId));
      await waitFor(() => expect(screen.getByTestId(flow.errorId)).toHaveTextContent("操作結果尚未確認"));
      expect(screen.getByTestId(flow.errorId)).toHaveTextContent("同一 Idempotency-Key");
      fireEvent.click(screen.getByTestId(flow.submitId));
      await waitFor(() => expect(flow.commands).toHaveLength(2));
      expect(flow.commands[0]!.key).toBeTruthy();
      expect(flow.commands[1]).toEqual(flow.commands[0]);
    },
  );

  it.each(["claim", "transfer", "pause", "resume"] as const)(
    "rejects a foreign %s acknowledgement and retains the original retry intent",
    async (action) => {
      const flow = setupResourceCommand(action, ["foreign", "success"]);
      await flow.open();
      fireEvent.click(screen.getByTestId(flow.submitId));
      await waitFor(() => expect(screen.getByTestId(flow.errorId)).toHaveTextContent("ODP-INTAKE-ACK-MISMATCH"));
      expect(screen.getByTestId("intake-detail-dialog")).not.toHaveTextContent("FOREIGN-RESOURCE");
      expect(screen.getByTestId(action === "claim" || action === "transfer" ? "receipt-asg-id" : "receipt-sla-id")).toHaveTextContent("UNAVAILABLE");
      fireEvent.click(screen.getByTestId(flow.submitId));
      await waitFor(() => expect(flow.commands).toHaveLength(2));
      expect(flow.commands[1]).toEqual(flow.commands[0]);
    },
  );

  it("keeps the original claim precondition after an unknown outcome and a newer GET snapshot", async () => {
    const flow = setupResourceCommand("claim", ["lost", "success"]);
    await flow.open();
    fireEvent.click(screen.getByTestId(flow.submitId));
    await waitFor(() => expect(screen.getByTestId(flow.errorId)).toHaveTextContent("操作結果尚未確認"));
    flow.advanceVersion();
    fireEvent.click(screen.getByTestId("intake-refresh-button"));
    await waitFor(() => expect(screen.getByTestId("intake-detail-id")).toHaveTextContent(flow.intakeId));
    await waitFor(() => expect(screen.queryByTestId("intake-error-recovery-section")).toBeNull());
    fireEvent.click(screen.getByTestId(flow.submitId));
    await waitFor(() => expect(flow.commands).toHaveLength(2));
    expect(flow.commands[0]!.ifMatch).toBe('W/"7"');
    expect(flow.commands[1]).toEqual(flow.commands[0]);
  });

  it.each(["transfer", "pause"] as const)("creates a new %s intent when the user edits the request", async (action) => {
    const flow = setupResourceCommand(action, ["lost", "success"]);
    await flow.open();
    fireEvent.click(screen.getByTestId(flow.submitId));
    await waitFor(() => expect(screen.getByTestId(flow.errorId)).toHaveTextContent("操作結果尚未確認"));
    fireEvent.change(screen.getByTestId(action === "transfer" ? "transfer-handoff-note" : "pause-reason-input"), { target: { value: "Edited operator request" } });
    fireEvent.click(screen.getByTestId(flow.submitId));
    await waitFor(() => expect(flow.commands).toHaveLength(2));
    expect(flow.commands[1]!.key).not.toBe(flow.commands[0]!.key);
    expect(flow.commands[1]!.body).not.toEqual(flow.commands[0]!.body);
  });

  it.each(["claim", "transfer", "pause", "resume"] as const)("uses a new %s key after confirmed conflict and an updated resource version", async (action) => {
    const flow = setupResourceCommand(action, ["conflict", "success"]);
    await flow.open();
    fireEvent.click(screen.getByTestId(flow.submitId));
    const conflictId = action === "transfer" ? "transfer-conflict-panel" : action === "pause" ? "pause-conflict-panel" : flow.errorId;
    await screen.findByTestId(conflictId);
    flow.advanceVersion();
    fireEvent.click(screen.getByTestId(action === "transfer" ? "transfer-conflict-refresh-btn" : action === "pause" ? "pause-conflict-refresh-btn" : "intake-refresh-button"));
    await waitFor(() => expect(screen.queryByTestId(conflictId)).toBeNull());
    await waitFor(() => expect(screen.getByTestId(flow.submitId)).not.toBeDisabled());
    fireEvent.click(screen.getByTestId(flow.submitId));
    await waitFor(() => expect(flow.commands).toHaveLength(2));
    expect(flow.commands[1]!.key).not.toBe(flow.commands[0]!.key);
    expect(flow.commands[1]!.ifMatch).toBe(action === "claim" || action === "transfer" ? 'W/"8"' : 'W/"10"');
  });

  it.each(["success", "failure"])("ignores a late refresh %s for the previously selected intake", async (outcome) => {
    let resolveRefresh: (response: Response) => void = () => {};
    const first = intake({ id: "INTAKE-FIRST" });
    const next = intake({ id: "INTAKE-NEXT" });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path === "/api/v1/operator/network-listings/intake") return json({
        items: [first, next], total: 2, page: 1, pageSize: 10,
        counts: { needsReview: 2, awaitingEntry: 0, processing: 0, blocked: 0, ready: 0 }, evidenceState: "complete",
      });
      if (path === `/api/v1/operator/network-listings/intake/${first.id}`) return new Promise<Response>((resolve) => { resolveRefresh = resolve; });
      return json({ code: "NOT_FOUND" }, 404);
    }));
    nav.reset(`selected=${first.id}&dialog=detail`);
    render(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="subject-1" />);
    fireEvent.click(await screen.findByTestId("intake-refresh-button"));
    act(() => nav.push(`/operator?selected=${next.id}&dialog=detail`));
    await waitFor(() => expect(screen.getByTestId("intake-detail-id")).toHaveTextContent(next.id));
    await act(async () => resolveRefresh(outcome === "success"
      ? json({ ...first, owner: "Late old owner" }) : json({ detail: "OLD_REFRESH_FAILED" }, 503)));
    expect(screen.getByTestId("intake-detail-id")).toHaveTextContent(next.id);
    expect(nav.state.search).toContain(`selected=${next.id}`);
    expect(screen.getByTestId("intake-detail-dialog")).not.toHaveTextContent("Late old owner");
    expect(screen.getByTestId("intake-detail-dialog")).not.toHaveTextContent("OLD_REFRESH_FAILED");
    expect(screen.queryByTestId("intake-error-recovery-section")).toBeNull();
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

function setupResourceCommand(
  action: "claim" | "transfer" | "pause" | "resume",
  outcomes: Array<"lost" | "foreign" | "success" | "conflict">,
) {
  let record = intake({ assignmentId: "ASG-INTENT", assignmentVersion: 7, assignmentStatus: "ASSIGNED",
    slaInstanceId: "SLA-INTENT", slaVersion: 9, slaState: action === "resume" ? "PAUSED" : "ON_TRACK",
  } as Partial<AssistedIntake> & { assignmentVersion: number; slaVersion: number });
  const assignment = action === "claim" || action === "transfer";
  const commands: Array<{ key: string | null; ifMatch: string | null; correlation: string | null; body: unknown }> = [];
  const submitId = ({ claim: "asg-btn-claim", transfer: "transfer-submit-btn", pause: "pause-submit-btn", resume: "asg-btn-resume" })[action];
  const errorId = action === "transfer" ? "transfer-error-panel" : action === "pause" ? "pause-error-panel" : "intake-error-recovery-section";
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), "http://localhost").pathname;
    if (path === "/api/v1/operator/network-listings/intake") return json({ items: [record], total: 1, page: 1, pageSize: 10,
      counts: { needsReview: 1, awaitingEntry: 0, processing: 0, blocked: 0, ready: 0 }, evidenceState: "complete" });
    if (path === `/api/v1/operator/network-listings/intake/${record.id}`) return json(record);
    if (path.endsWith("/transfer-targets")) return json({ assignment_id: record.assignmentId,
      assignment_version: (record as any).assignmentVersion, items: [transferTarget] });
    if (path.endsWith(`/actions/${action}`)) {
      const headers = new Headers(init?.headers);
      commands.push({ key: headers.get("idempotency-key"), ifMatch: headers.get("if-match"),
        correlation: headers.get("x-correlation-id"), body: JSON.parse(String(init?.body)) });
      const outcome = outcomes[commands.length - 1];
      if (outcome === "lost") throw new TypeError("Connection lost after server processing");
      if (outcome === "conflict") return json({ detail: "VERSION_CONFLICT" }, 409);
      return assignment
        ? json({ assignment_id: outcome === "foreign" ? "FOREIGN-RESOURCE" : record.assignmentId,
          version: 10, status: "CLAIMED", owner_subject_id: "subject-1", audit_event_id: "AUD-INTENT" })
        : json({ sla_instance_id: outcome === "foreign" ? "FOREIGN-RESOURCE" : record.slaInstanceId,
          version: 10, state: action === "pause" ? "PAUSED" : "ON_TRACK", paused_duration_seconds: 0,
          audit_event_id: "AUD-INTENT", correlation_id: "CORR-INTENT" });
    }
    return json({ code: "NOT_FOUND" }, 404);
  }));
  nav.reset(`selected=${record.id}&dialog=detail`);
  render(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="subject-1" />);
  return {
    commands, submitId, errorId, intakeId: record.id,
    advanceVersion() { record = { ...record, assignmentVersion: 8, slaVersion: 10 } as AssistedIntake; },
    async open() {
      if (action === "transfer") {
        fireEvent.click(await screen.findByTestId("asg-btn-transfer"));
        await selectTransferTarget();
        fireEvent.change(screen.getByTestId("transfer-handoff-note"), { target: { value: "Original handoff" } });
        fireEvent.click(screen.getByTestId("transfer-risk-ack"));
      } else if (action === "pause") {
        fireEvent.click(await screen.findByTestId("asg-btn-pause"));
        fireEvent.change(await screen.findByTestId("pause-reason-input"), { target: { value: "Original reason" } });
        fireEvent.change(screen.getByTestId("pause-resume-time-input"), { target: { value: "2026-10-14T11:00" } });
        fireEvent.click(screen.getByTestId("pause-risk-ack"));
      }
      await screen.findByTestId(submitId);
    },
  };
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
    if (path.endsWith("/transfer-targets")) return json({ assignment_id: record.assignmentId, assignment_version: (record as any).assignmentVersion, items: [transferTarget] });
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

describe("partial recorded match facts do not establish decision authority", () => {
  it("keeps a target-only partial match readable without enabling warm decision actions", () => {
    const record = intake({ stage: "READY", matchResult: {
      targetListingId: "OWN-TARGET", confidence: 0.82, summary: "Recorded partial match",
    } });
    const onDecide = vi.fn();
    render(<IntakeProcessingDetail record={record} onClose={vi.fn()} canDecide onDecide={onDecide} />);
    expect(screen.getByTestId("evidence-target-id")).toHaveTextContent("OWN-TARGET");
    expect(screen.getByTestId("evidence-code-val")).toHaveTextContent("UNAVAILABLE");
    expect(screen.queryByTestId("intake-detail-actions")).toBeNull();
    expect(decisionOptions(record)).toEqual([]);
    expect(onDecide).not.toHaveBeenCalled();
  });
  it("requires recorded target-dependent classification even if target and named signals are available", () => {
    const record = intake({ matchResult: {
      targetListingId: "OWN-TARGET", confidence: 0.82, summary: "Recorded partial match",
      agreeingSignals: [{ key: "address", label: "Address", detail: "Recorded signal", agrees: true }],
    } });
    expect(hasAuthoritativeTargetEvidence(record, { id: "OWN-TARGET", address: "Recorded target address" })).toBe(false);
  });
  it("does not submit a manually opened target decision for a partial match", () => {
    const record = intake({ stage: "READY", matchResult: {
      targetListingId: "OWN-TARGET", confidence: 0.82, summary: "Recorded partial match",
    } });
    const onSubmit = vi.fn();
    render(<IntakeDecisionDialog record={record} kind="revise" busy={false} error={null} onClose={vi.fn()} onSubmit={onSubmit} />);
    fireEvent.change(screen.getByTestId("intake-decide-reason"), { target: { value: "Own reason" } });
    fireEvent.click(screen.getByTestId("intake-decide-risk-ack"));
    expect(screen.getByTestId("intake-decide-submit")).toBeDisabled();
    fireEvent.click(screen.getByTestId("intake-decide-submit"));
    expect(onSubmit).not.toHaveBeenCalled();
  });
  it.each([null, undefined, NaN, Infinity, -0.1, 1.1, true, "0.7"])("does not coerce unavailable or invalid confidence %s to zero", (value) => {
    expect(matchConfidence(value)).toBeNull();
  });
  it.each([0, 0.7, 1])("retains recorded confidence boundary %s", (value) => {
    expect(matchConfidence(value)).toBe(value);
  });
});


describe("decision drafts belong to one client, principal, record and decision intent", () => {
  it("survives list omission, delayed failing direct GET and 503, then retries the same command key", async () => {
    const record = intake();
    let listed = true;
    let finishGet!: (response: Response) => void;
    const writes: Array<{ key: string | null; reason: string }> = [];
    nav.reset(`selected=${record.id}&dialog=decide&decision=create`);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path === "/api/v1/operator/network-listings/intake") return json({
        items: listed ? [record] : [], total: listed ? 1 : 0, page: 1, pageSize: 10,
        counts: {}, evidenceState: "complete",
      });
      if (path === `/api/v1/operator/network-listings/intake/${record.id}`) {
        return new Promise<Response>((resolve) => { finishGet = resolve; });
      }
      if (path.endsWith(`/${record.id}/decide`)) {
        writes.push({ key: new Headers(init?.headers).get("idempotency-key"),
          reason: JSON.parse(String(init?.body)).reason });
        return writes.length === 1 ? json({ detail: "Decision temporarily unavailable" }, 503)
          : json({ ...record, stage: "READY" });
      }
      return json({ code: "NOT_FOUND" }, 404);
    }));
    render(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="draft-owner" />);
    fireEvent.change(await screen.findByTestId("intake-decide-reason"), { target: { value: "Recorded same-scope decision reason" } });
    fireEvent.click(screen.getByTestId("intake-decide-risk-ack"));
    fireEvent.click(screen.getByTestId("intake-decide-submit"));
    await screen.findByTestId("intake-decide-error");
    expect(screen.getByTestId("intake-decide-reason")).toHaveValue("Recorded same-scope decision reason");
    listed = false;
    await act(async () => nav.replace(`/operator?selected=${record.id}&dialog=decide&decision=create&refresh=omitted`));
    await screen.findByTestId("intake-direct-detail-loading");
    expect(screen.queryByTestId("intake-decide-dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(finishGet).toBeDefined());
    await act(async () => finishGet(json({ detail: "Direct read temporarily unavailable" }, 503)));
    await screen.findByTestId("intake-direct-detail-error");
    expect(writes).toHaveLength(1);
    listed = true;
    await act(async () => nav.replace(`/operator?selected=${record.id}&dialog=decide&decision=create&refresh=restored`));
    expect(await screen.findByTestId("intake-decide-reason")).toHaveValue("Recorded same-scope decision reason");
    expect(screen.getByTestId("intake-decide-risk-ack")).toBeChecked();
    expect(screen.getByTestId("intake-decide-error")).toHaveTextContent("Decision temporarily unavailable");
    fireEvent.click(screen.getByTestId("intake-decide-submit"));
    await waitFor(() => expect(screen.queryByTestId("intake-decide-dialog")).not.toBeInTheDocument());
    expect(writes).toHaveLength(2);
    expect(writes[0].key).toBeTruthy();
    expect(writes[1]).toEqual(writes[0]);
  });

  it.each(["principal", "role", "record", "kind", "cancel"])("does not carry a draft across a %s change or A-B-A navigation", async (change) => {
    const record = intake();
    const second = intake({ id: "IN-SECOND-DECISION" });
    nav.reset(`selected=${record.id}&dialog=decide&decision=create`);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path === "/api/v1/operator/network-listings/intake") return json({ items: [record, second], total: 2,
        page: 1, pageSize: 10, counts: {}, evidenceState: "complete" });
      return json({ code: "NOT_FOUND" }, 404);
    }));
    const view = render(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="draft-owner" />);
    fireEvent.change(await screen.findByTestId("intake-decide-reason"), { target: { value: "Must stay with the original intent" } });
    fireEvent.click(screen.getByTestId("intake-decide-risk-ack"));
    if (change === "principal") {
      view.rerender(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="other-owner" />);
    } else if (change === "role") {
      view.rerender(<AssistedIntakeSection activeRoleId="expansion-staff" activeSubjectId="draft-owner" />);
    } else {
      const next = change === "record" ? `selected=${second.id}&dialog=decide&decision=create`
        : change === "kind" ? `selected=${record.id}&dialog=decide&decision=dup`
          : `selected=${record.id}&dialog=detail`;
      await act(async () => nav.replace(`/operator?${next}`));
    }
    if (change !== "role" && change !== "cancel") {
      await waitFor(() => expect(screen.getByTestId("intake-decide-reason")).toHaveValue(""));
      expect(screen.getByTestId("intake-decide-risk-ack")).not.toBeChecked();
    }
    view.rerender(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="draft-owner" />);
    await act(async () => nav.replace(`/operator?selected=${record.id}&dialog=decide&decision=create`));
    expect(await screen.findByTestId("intake-decide-reason")).toHaveValue("");
    expect(screen.getByTestId("intake-decide-risk-ack")).not.toBeChecked();
  });

  it.each([200, 503])("ignores an abandoned decision's late %s result after a new principal is ready", async (status) => {
    const record = intake();
    let finishWrite!: (response: Response) => void;
    nav.reset(`selected=${record.id}&dialog=decide&decision=create`);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path === "/api/v1/operator/network-listings/intake") return json({ items: [record], total: 1,
        page: 1, pageSize: 10, counts: {}, evidenceState: "complete" });
      if (path.endsWith(`/${record.id}/decide`)) return new Promise<Response>((resolve) => { finishWrite = resolve; });
      return json({ code: "NOT_FOUND" }, 404);
    }));
    const view = render(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="draft-owner" />);
    fireEvent.change(await screen.findByTestId("intake-decide-reason"), { target: { value: "Old principal reason" } });
    fireEvent.click(screen.getByTestId("intake-decide-risk-ack"));
    fireEvent.click(screen.getByTestId("intake-decide-submit"));
    await waitFor(() => expect(finishWrite).toBeDefined());
    view.rerender(<AssistedIntakeSection activeRoleId="expansion-manager" activeSubjectId="new-owner" />);
    await waitFor(() => expect(screen.getByTestId("intake-decide-reason")).toBeEnabled());
    fireEvent.change(screen.getByTestId("intake-decide-reason"), { target: { value: "New principal reason" } });
    await act(async () => finishWrite(status === 200 ? json({ ...record, stage: "READY" })
      : json({ detail: "Old principal refusal" }, 503)));
    expect(screen.getByTestId("intake-decide-reason")).toHaveValue("New principal reason");
    expect(screen.getByTestId("intake-decide-risk-ack")).not.toBeChecked();
    expect(screen.queryByTestId("intake-decide-error")).not.toBeInTheDocument();
    expect(new URLSearchParams(nav.state.search).get("dialog")).toBe("decide");
  });
});
