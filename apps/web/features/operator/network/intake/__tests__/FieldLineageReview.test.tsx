import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildCanonicalFieldReview,
  buildScreenReaderChangeSummary,
  ParsedDataReview,
} from "../ParsedDataReview";
import {
  materialCorrectionRequirement,
  type CanonicalFieldValueLike,
  type FieldCorrectionLineage,
} from "../FieldLineageRow";

afterEach(() => cleanup());

const correction: FieldCorrectionLineage = {
  correctionId: "corr-address-002",
  status: "PENDING_REVIEW",
  correctedValue: "台北市信義區松仁路 100 號",
  beforeEffectiveValue: "台北市信義區松仁路 10 號",
  afterEffectiveValue: "台北市信義區松仁路 100 號",
  actorSubjectId: "subject-expansion-01",
  actorName: "王小明",
  actorRole: "Expansion staff",
  correctedAt: "2026-07-23T04:10:00Z",
  reason: "依房東提供的謄本校正門牌",
  reviewerSubjectId: null,
  reviewerName: null,
  reviewedAt: null,
  sourceSnapshotId: "snapshot-100",
  parserRunId: "parser-run-200",
  supersedesCorrectionId: "corr-address-001",
  reversalOfCorrectionId: null,
  version: 2,
};

const canonicalFields: CanonicalFieldValueLike[] = [
  {
    field_path: "identity.providerListingId",
    parsed: "SRC-88",
    normalized: "SRC-88",
    corrected: null,
    effective: "SRC-88",
    confidence: 0.99,
    classification: "INTERNAL",
    masked: false,
  },
  {
    field_path: "location.address",
    parsed: "台北市信義區松仁路10號",
    normalized: "台北市信義區松仁路 10 號",
    corrected: "台北市信義區松仁路 100 號",
    effective: "台北市信義區松仁路 10 號",
    confidence: 0.55,
    classification: "CONFIDENTIAL",
    masked: false,
  },
  {
    field_path: "commercial.rent",
    parsed: 180000,
    normalized: 180000,
    corrected: null,
    effective: 180000,
    confidence: 0.91,
    classification: "CONFIDENTIAL",
    masked: false,
  },
  {
    field_path: "property.floor",
    parsed: null,
    normalized: null,
    corrected: null,
    effective: null,
    confidence: null,
    classification: "INTERNAL",
    masked: false,
  },
  {
    field_path: "provenance.sourceUrl",
    parsed: "https://secret.example/listing/88",
    normalized: "https://secret.example/listing/88",
    corrected: null,
    effective: "https://secret.example/listing/88",
    confidence: 1,
    classification: "RESTRICTED",
    masked: true,
    mask_reason_code: "FIELD_MASKED",
  },
];

describe("ParsedDataReview", () => {
  it("renders all five groups and every parsed/normalized/corrected/effective layer", () => {
    const fields = buildCanonicalFieldReview(canonicalFields, {
      sourceSnapshotId: "snapshot-100",
      parserRunId: "parser-run-200",
      correctionsByField: { "location.address": [correction] },
    });

    render(<ParsedDataReview canCorrect fields={fields} onCorrect={vi.fn()} />);

    for (const group of ["identity", "location", "commercial", "property", "provenance"]) {
      expect(screen.getByTestId(`field-group-${group}`)).toBeInTheDocument();
    }

    const addressRow = screen.getByTestId("field-lineage-row-location.address");
    expect(within(addressRow).getByText("台北市信義區松仁路10號")).toBeInTheDocument();
    expect(within(addressRow).getAllByText("台北市信義區松仁路 10 號")).toHaveLength(2);
    expect(within(addressRow).getByText("台北市信義區松仁路 100 號")).toBeInTheDocument();
    expect(addressRow.querySelector('[data-value-layer="effective"]')).toHaveTextContent(
      "台北市信義區松仁路 10 號",
    );
    expect(addressRow).toHaveTextContent("低信心");
    expect(addressRow).toHaveTextContent("需獨立覆核");
  });

  it("renders complete correction, actor, time, reason, snapshot, parser and supersession lineage", () => {
    const fields = buildCanonicalFieldReview(canonicalFields, {
      correctionsByField: { "location.address": [correction] },
    });
    render(<ParsedDataReview canCorrect fields={fields} />);

    const addressRow = screen.getByTestId("field-lineage-row-location.address");
    fireEvent.click(within(addressRow).getByText(/1 筆修正/));

    expect(addressRow).toHaveTextContent("王小明");
    expect(addressRow).toHaveTextContent("Expansion staff");
    expect(addressRow).toHaveTextContent("2026-07-23T04:10:00Z");
    expect(addressRow).toHaveTextContent("依房東提供的謄本校正門牌");
    expect(addressRow).toHaveTextContent("snapshot-100");
    expect(addressRow).toHaveTextContent("parser-run-200");
    expect(addressRow).toHaveTextContent("Supersedes corr-address-001");
    expect(addressRow).toHaveTextContent("等待獨立覆核者");
  });

  it("keeps masked values undisclosed and identifies missing values without relying on colour", () => {
    const fields = buildCanonicalFieldReview(canonicalFields);
    render(<ParsedDataReview canCorrect fields={fields} />);

    const maskedRow = screen.getByTestId("field-lineage-row-provenance.sourceUrl");
    expect(maskedRow).toHaveTextContent("已遮罩 · FIELD_MASKED");
    expect(maskedRow).not.toHaveTextContent("https://secret.example/listing/88");
    expect(within(maskedRow).getByRole("button", { name: "修正" })).toBeDisabled();

    const missingRow = screen.getByTestId("field-lineage-row-property.floor");
    expect(missingRow).toHaveTextContent("缺少");
    expect(missingRow.querySelector('[data-value-layer="effective"]')).toHaveTextContent(
      "有效值尚未提供",
    );
  });

  it("provides a screen-reader-readable aggregate and per-row change summary", () => {
    const fields = buildCanonicalFieldReview(canonicalFields, {
      correctionsByField: { "location.address": [correction] },
    });
    const summary = buildScreenReaderChangeSummary(fields);

    expect(summary).toContain("共 5 個欄位");
    expect(summary).toContain("1 個有人工修正");
    expect(summary).toContain("1 個已遮罩");
    expect(summary).toContain("1 個缺少");
    expect(summary).toContain("1 個低信心");
    expect(summary).toContain("1 個等待獨立覆核");

    render(<ParsedDataReview canCorrect fields={fields} />);
    expect(screen.getByTestId("parsed-data-change-summary")).toHaveTextContent(summary);
    expect(screen.getByTestId("field-lineage-row-location.address")).toHaveAccessibleName(
      /解析值/,
    );
  });

  it("defines reason, risk acknowledgement and independent review for material fields", () => {
    const [identityField, addressField, rentField, propertyField] = buildCanonicalFieldReview(
      canonicalFields,
    );

    for (const field of [identityField, addressField, rentField]) {
      expect(materialCorrectionRequirement(field)).toEqual({
        material: true,
        reasonRequired: true,
        riskAcknowledgementRequired: true,
        independentReviewRequired: true,
        reasonCode: "MATERIAL_IDENTITY_CHANGE",
      });
    }
    expect(materialCorrectionRequirement(propertyField).independentReviewRequired).toBe(false);
  });
});
