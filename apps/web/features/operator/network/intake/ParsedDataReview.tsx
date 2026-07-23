"use client";

import type { IntakeFieldCell } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import {
  FieldLineageRow,
  fieldChangeSummary,
  fromCanonicalFieldValue,
  fromLegacyIntakeField,
  type CanonicalFieldValueLike,
  type FieldCorrectionLineage,
  type FieldGroupId,
  type FieldLineageField,
  type LineageContext,
} from "./FieldLineageRow";

export const FIELD_GROUP_ORDER: readonly FieldGroupId[] = [
  "identity",
  "location",
  "commercial",
  "property",
  "provenance",
];

export const FIELD_GROUP_LABEL: Readonly<Record<FieldGroupId, string>> = {
  identity: "識別 Identity",
  location: "位置 Location",
  commercial: "商務條件 Commercial",
  property: "物件 Property",
  provenance: "來源與沿革 Provenance",
};

export type ParsedDataReviewProps = {
  fields: readonly FieldLineageField[];
  canCorrect: boolean;
  onCorrect?: (field: FieldLineageField) => void;
  heading?: string;
  emptyMessage?: string;
};

export function ParsedDataReview({
  fields,
  canCorrect,
  onCorrect,
  heading = "解析資料覆核",
  emptyMessage = "目前沒有可覆核欄位。",
}: ParsedDataReviewProps) {
  const groups = groupFields(fields);
  const summary = buildScreenReaderChangeSummary(fields);

  return (
    <section aria-labelledby="parsed-data-review-heading" data-testid="parsed-data-review">
      <div className={styles.sectionLabel} id="parsed-data-review-heading">
        {heading}
      </div>
      <p aria-live="polite" className={styles.srSummary} data-testid="parsed-data-change-summary">
        {summary}
      </p>

      {!fields.length ? <div className={styles.emptyState}>{emptyMessage}</div> : null}

      {FIELD_GROUP_ORDER.map((groupId) => {
        const groupFieldsForDisplay = groups[groupId];
        return (
          <section
            aria-labelledby={`field-group-${groupId}`}
            className={styles.sectionBox}
            data-field-group={groupId}
            data-testid={`field-group-${groupId}`}
            key={groupId}
          >
            <h3 className={styles.sectionHead} id={`field-group-${groupId}`}>
              {FIELD_GROUP_LABEL[groupId]}
              <span className={styles.sectionHeadHint}>{groupFieldsForDisplay.length} 個欄位</span>
            </h3>
            {groupFieldsForDisplay.length ? (
              <div style={{ maxWidth: "100%", overflowX: "auto" }}>
                <table style={{ borderCollapse: "collapse", minWidth: "1120px", width: "100%" }}>
                  <caption style={visuallyHidden}>
                    {FIELD_GROUP_LABEL[groupId]}，依序顯示解析值、正規化值、人工修正值、有效值與修正沿革
                  </caption>
                  <thead>
                    <tr>
                      <th className={styles.fieldsHeadCell} scope="col">
                        欄位
                      </th>
                      <th className={styles.fieldsHeadCell} scope="col">
                        解析值 Parsed
                      </th>
                      <th className={styles.fieldsHeadCell} scope="col">
                        正規化值 Normalized
                      </th>
                      <th className={styles.fieldsHeadCell} scope="col">
                        人工修正值 Corrected
                      </th>
                      <th className={styles.fieldsHeadCell} scope="col">
                        有效值 Effective
                      </th>
                      <th className={styles.fieldsHeadCell} scope="col">
                        修正沿革 Lineage
                      </th>
                      <th className={styles.fieldsHeadCell} scope="col">
                        動作
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {groupFieldsForDisplay.map((field) => (
                      <FieldLineageRow
                        canCorrect={canCorrect}
                        field={field}
                        key={field.fieldPath}
                        onCorrect={onCorrect}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className={styles.emptyState}>此群組目前沒有伺服器提供的欄位。</div>
            )}
          </section>
        );
      })}
    </section>
  );
}

export function groupFields(
  fields: readonly FieldLineageField[],
): Record<FieldGroupId, FieldLineageField[]> {
  const grouped: Record<FieldGroupId, FieldLineageField[]> = {
    identity: [],
    location: [],
    commercial: [],
    property: [],
    provenance: [],
  };
  for (const field of fields) grouped[field.group].push(field);
  return grouped;
}

export function buildScreenReaderChangeSummary(fields: readonly FieldLineageField[]): string {
  if (!fields.length) return "目前沒有可覆核欄位。";
  const corrected = fields.filter((field) => field.corrections.length || hasValue(field.correctedValue));
  const masked = fields.filter((field) => field.masked);
  const missing = fields.filter((field) => field.missing);
  const lowConfidence = fields.filter((field) => field.lowConfidence);
  const materialPending = fields.filter((field) =>
    field.corrections.some(
      (correction) => correction.status === "PENDING_REVIEW" || correction.status === "PROPOSED",
    ),
  );

  const detail = corrected.map(fieldChangeSummary).join(" ");
  return [
    `共 ${fields.length} 個欄位。`,
    `${corrected.length} 個有人工修正。`,
    `${masked.length} 個已遮罩。`,
    `${missing.length} 個缺少。`,
    `${lowConfidence.length} 個低信心。`,
    `${materialPending.length} 個等待獨立覆核。`,
    detail,
  ]
    .filter(Boolean)
    .join(" ");
}

export function buildCanonicalFieldReview(
  fields: readonly CanonicalFieldValueLike[],
  context: LineageContext = {},
): FieldLineageField[] {
  return fields.map((field) => fromCanonicalFieldValue(field, context));
}

export function buildLegacyFieldReview(
  fields: Readonly<Record<string, IntakeFieldCell>>,
  context: {
    sourceSnapshotId?: string | null;
    parserRunId?: string | null;
    correctionsByField?: Readonly<Record<string, readonly FieldCorrectionLineage[]>>;
  } = {},
): FieldLineageField[] {
  return Object.values(fields).map((field) => fromLegacyIntakeField(field, context));
}

function hasValue(value: unknown): boolean {
  return value !== null && value !== undefined && value !== "";
}

const visuallyHidden = {
  border: 0,
  clip: "rect(0 0 0 0)",
  height: "1px",
  margin: "-1px",
  overflow: "hidden",
  padding: 0,
  position: "absolute" as const,
  whiteSpace: "nowrap" as const,
  width: "1px",
};
