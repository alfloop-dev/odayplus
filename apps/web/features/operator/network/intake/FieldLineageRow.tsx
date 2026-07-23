"use client";

import type { IntakeFieldCell } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";

export type FieldGroupId =
  | "identity"
  | "location"
  | "commercial"
  | "property"
  | "provenance";

export type FieldCorrectionStatus =
  | "PROPOSED"
  | "PENDING_REVIEW"
  | "APPLIED"
  | "REJECTED"
  | "SUPERSEDED"
  | "REVERSED";

export type FieldCorrectionLineage = {
  correctionId: string;
  status: FieldCorrectionStatus;
  correctedValue: unknown;
  beforeEffectiveValue: unknown;
  afterEffectiveValue: unknown;
  actorSubjectId: string;
  actorName: string | null;
  actorRole: string | null;
  correctedAt: string;
  reason: string;
  reviewerSubjectId: string | null;
  reviewerName: string | null;
  reviewedAt: string | null;
  sourceSnapshotId: string | null;
  parserRunId: string | null;
  supersedesCorrectionId: string | null;
  reversalOfCorrectionId: string | null;
  version: number;
};

export type FieldLineageField = {
  fieldPath: string;
  label: string;
  group: FieldGroupId;
  parsedValue: unknown;
  normalizedValue: unknown;
  correctedValue: unknown;
  effectiveValue: unknown;
  confidence: number | null;
  classification: "PUBLIC" | "INTERNAL" | "CONFIDENTIAL" | "RESTRICTED";
  masked: boolean;
  maskReasonCode: string | null;
  missing: boolean;
  lowConfidence: boolean;
  identityAffecting: boolean;
  sourceSnapshotId: string | null;
  parserRunId: string | null;
  corrections: readonly FieldCorrectionLineage[];
};

export type CanonicalFieldValueLike = {
  field_path: string;
  parsed?: unknown;
  normalized?: unknown;
  corrected?: unknown;
  effective?: unknown;
  confidence?: number | null;
  classification?: FieldLineageField["classification"];
  masked: boolean;
  mask_reason_code?: string | null;
};

export type LineageContext = {
  sourceSnapshotId?: string | null;
  parserRunId?: string | null;
  correctionsByField?: Readonly<Record<string, readonly FieldCorrectionLineage[]>>;
};

export type MaterialCorrectionRequirement = {
  material: boolean;
  reasonRequired: boolean;
  riskAcknowledgementRequired: boolean;
  independentReviewRequired: boolean;
  reasonCode: "MATERIAL_IDENTITY_CHANGE" | "STANDARD_FIELD_CORRECTION";
};

const GROUP_PATHS: Record<FieldGroupId, readonly string[]> = {
  identity: [
    "provider",
    "providerid",
    "providerlistingid",
    "sourceid",
    "sourcelistingid",
    "listingtype",
    "listingstatus",
    "matchoutcome",
  ],
  location: [
    "address",
    "addressraw",
    "normalizedaddress",
    "district",
    "latitude",
    "longitude",
    "geocodeconfidence",
  ],
  commercial: [
    "rent",
    "rentamount",
    "askingprice",
    "price",
    "currency",
    "areaping",
    "area",
    "managementfee",
    "deposit",
  ],
  property: [
    "floor",
    "totalfloors",
    "frontage",
    "frontagemeters",
    "parking",
    "temporarystop",
    "availabledate",
    "description",
    "feasibilityflags",
  ],
  provenance: [
    "sourceurl",
    "originalurl",
    "canonicalurl",
    "sourcesnapshot",
    "sourcesnapshotid",
    "observedat",
    "capturedat",
    "parserversion",
    "parserrunid",
  ],
};

const MATERIAL_PATHS = new Set([
  "provider",
  "providerid",
  "providerlistingid",
  "sourceid",
  "sourcelistingid",
  "listingtype",
  "listingstatus",
  "matchoutcome",
  "address",
  "addressraw",
  "normalizedaddress",
  "district",
  "rent",
  "rentamount",
  "askingprice",
  "price",
  "areaping",
  "area",
]);

const LABELS: Readonly<Record<string, string>> = {
  provider: "來源提供者",
  providerlistingid: "來源物件 ID",
  listingtype: "物件型態",
  listingstatus: "物件狀態",
  matchoutcome: "比對結果",
  addressraw: "原始地址",
  address: "正規化地址",
  district: "行政區",
  latitude: "緯度",
  longitude: "經度",
  geocodeconfidence: "地理編碼信心",
  rent: "租金",
  rentamount: "租金",
  askingprice: "開價",
  currency: "幣別",
  areaping: "面積（坪）",
  managementfee: "管理費",
  deposit: "押金",
  floor: "樓層",
  totalfloors: "總樓層",
  frontagemeters: "面寬",
  parking: "停車",
  temporarystop: "臨停條件",
  availabledate: "可用日期",
  feasibilityflags: "可行性標記",
  sourceurl: "來源 URL",
  sourcesnapshotid: "來源快照",
  observedat: "觀測時間",
  parserversion: "Parser 版本",
  parserrunid: "Parser Run",
};

export function fieldGroupForPath(fieldPath: string): FieldGroupId {
  const normalized = normalizePath(fieldPath);
  for (const group of Object.keys(GROUP_PATHS) as FieldGroupId[]) {
    if (GROUP_PATHS[group].some((candidate) => normalized === candidate || normalized.endsWith(candidate))) {
      return group;
    }
  }
  return "property";
}

export function materialCorrectionRequirement(
  field: Pick<FieldLineageField, "fieldPath" | "identityAffecting">,
): MaterialCorrectionRequirement {
  const material = field.identityAffecting || MATERIAL_PATHS.has(normalizePath(field.fieldPath));
  return {
    material,
    // The canonical correction command always carries an attributable reason
    // and risk acknowledgement. Material fields additionally require an
    // independent reviewer and cannot become effective on proposer submit.
    reasonRequired: true,
    riskAcknowledgementRequired: true,
    independentReviewRequired: material,
    reasonCode: material ? "MATERIAL_IDENTITY_CHANGE" : "STANDARD_FIELD_CORRECTION",
  };
}

export function fromCanonicalFieldValue(
  field: CanonicalFieldValueLike,
  context: LineageContext = {},
): FieldLineageField {
  const corrections = context.correctionsByField?.[field.field_path] ?? [];
  const latest = latestCorrection(corrections);
  const effectiveValue = Object.prototype.hasOwnProperty.call(field, "effective")
    ? field.effective
    : undefined;
  const correctedValue = Object.prototype.hasOwnProperty.call(field, "corrected")
    ? field.corrected
    : latest?.correctedValue;
  const normalizedPath = normalizePath(field.field_path);

  return {
    fieldPath: field.field_path,
    label: LABELS[normalizedPath] ?? humanizePath(field.field_path),
    group: fieldGroupForPath(field.field_path),
    parsedValue: field.parsed,
    normalizedValue: field.normalized,
    correctedValue,
    effectiveValue,
    confidence: field.confidence ?? null,
    classification: field.classification ?? "INTERNAL",
    masked: field.masked,
    maskReasonCode: field.mask_reason_code ?? null,
    missing: isMissing(field.parsed) && isMissing(field.normalized) && isMissing(effectiveValue),
    lowConfidence: field.confidence !== null && field.confidence !== undefined && field.confidence < 0.7,
    identityAffecting: MATERIAL_PATHS.has(normalizedPath),
    sourceSnapshotId: latest?.sourceSnapshotId ?? context.sourceSnapshotId ?? null,
    parserRunId: latest?.parserRunId ?? context.parserRunId ?? null,
    corrections,
  };
}

export function fromLegacyIntakeField(
  field: IntakeFieldCell,
  context: LineageContext = {},
): FieldLineageField {
  const corrections = context.correctionsByField?.[field.key] ?? [];
  const latest = latestCorrection(corrections);
  const latestApplied = latestAppliedCorrection(corrections);
  const legacyEffective = (field as IntakeFieldCell & { effectiveValue?: unknown }).effectiveValue;
  // The legacy facade did not expose an authoritative effective layer. Never
  // present a client-side corrected/normalized fallback as server truth.
  const effectiveValue = Object.prototype.hasOwnProperty.call(field, "effectiveValue")
    ? legacyEffective
    : latestApplied?.afterEffectiveValue;

  return {
    fieldPath: field.key,
    label: field.label || LABELS[normalizePath(field.key)] || humanizePath(field.key),
    group: fieldGroupForPath(field.key),
    parsedValue: field.sourceValue,
    normalizedValue: field.normalizedValue,
    correctedValue: latest?.correctedValue ?? field.correctedValue,
    effectiveValue,
    confidence: field.lowConfidence ? 0.5 : null,
    classification: "INTERNAL",
    masked: Boolean(field.masked),
    maskReasonCode: field.mask_reason_code ?? null,
    missing:
      isMissing(field.sourceValue) &&
      isMissing(field.normalizedValue) &&
      isMissing(field.correctedValue),
    lowConfidence: field.lowConfidence,
    identityAffecting: field.identity || MATERIAL_PATHS.has(normalizePath(field.key)),
    sourceSnapshotId: latest?.sourceSnapshotId ?? context.sourceSnapshotId ?? null,
    parserRunId: latest?.parserRunId ?? context.parserRunId ?? null,
    corrections,
  };
}

export function FieldLineageRow({
  field,
  canCorrect,
  onCorrect,
}: {
  field: FieldLineageField;
  canCorrect: boolean;
  onCorrect?: (field: FieldLineageField) => void;
}) {
  const review = materialCorrectionRequirement(field);
  const summary = fieldChangeSummary(field);

  return (
    <tr
      aria-label={summary}
      data-field-path={field.fieldPath}
      data-group={field.group}
      data-testid={`field-lineage-row-${field.fieldPath}`}
    >
      <th className={styles.fieldCell} scope="row">
        <span className={styles.fieldLabelText}>{field.label}</span>
        <code>{field.fieldPath}</code>
        {field.identityAffecting ? <span className={styles.identityMark}>IDENTITY</span> : null}
        {review.material ? <span className={styles.lowChip}>需獨立覆核</span> : null}
        {field.lowConfidence ? <span className={styles.lowChip}>低信心</span> : null}
        {field.missing ? <span className={styles.lowChip}>缺少</span> : null}
      </th>
      <ValueCell field={field} kind="parsed" value={field.parsedValue} />
      <ValueCell field={field} kind="normalized" value={field.normalizedValue} />
      <ValueCell field={field} kind="corrected" value={field.correctedValue} />
      <ValueCell field={field} kind="effective" value={field.effectiveValue} />
      <td className={styles.fieldCell}>
        <LineageDetails field={field} />
      </td>
      <td className={styles.fieldCell}>
        <button
          className={styles.fixButton}
          data-testid={`field-lineage-correct-${field.fieldPath}`}
          disabled={!canCorrect || field.masked}
          onClick={() => onCorrect?.(field)}
          type="button"
        >
          修正
        </button>
        {!canCorrect ? <span>唯讀</span> : null}
      </td>
    </tr>
  );
}

export function fieldChangeSummary(field: FieldLineageField): string {
  if (field.masked) {
    return `${field.label} 已遮罩，原因 ${field.maskReasonCode ?? "FIELD_MASKED"}。`;
  }
  const states = [
    `解析值 ${formatValue(field.parsedValue, "parsed")}`,
    `正規化值 ${formatValue(field.normalizedValue, "normalized")}`,
    `人工修正值 ${formatValue(field.correctedValue, "corrected")}`,
    `有效值 ${formatValue(field.effectiveValue, "effective")}`,
  ];
  if (field.lowConfidence) states.push("低信心");
  if (field.missing) states.push("欄位缺少");
  if (field.corrections.length) states.push(`共有 ${field.corrections.length} 筆修正 lineage`);
  return `${field.label}：${states.join("；")}。`;
}

function ValueCell({
  field,
  kind,
  value,
}: {
  field: FieldLineageField;
  kind: "parsed" | "normalized" | "corrected" | "effective";
  value: unknown;
}) {
  if (field.masked) {
    return (
      <td className={styles.fieldCell} data-value-layer={kind}>
        <span className={styles.maskedValue}>
          已遮罩 · {field.maskReasonCode ?? "FIELD_MASKED"}
        </span>
      </td>
    );
  }
  const className =
    kind === "corrected"
      ? styles.correctedValue
      : kind === "normalized" || kind === "effective"
        ? styles.normalizedValue
        : styles.sourceValue;
  return (
    <td className={styles.fieldCell} data-value-layer={kind}>
      <span className={className}>{formatValue(value, kind)}</span>
      {kind === "parsed" && field.confidence !== null ? (
        <span>信心 {Math.round(field.confidence * 100)}%</span>
      ) : null}
    </td>
  );
}

function LineageDetails({ field }: { field: FieldLineageField }) {
  if (!field.corrections.length) {
    return (
      <>
        <span>尚無人工修正</span>
        <span>Snapshot {field.sourceSnapshotId ?? "未提供"}</span>
        <span>Parser run {field.parserRunId ?? "未提供"}</span>
      </>
    );
  }

  return (
    <details>
      <summary>
        {field.corrections.length} 筆修正 · 最新{" "}
        {field.corrections[field.corrections.length - 1]?.status}
      </summary>
      <ol>
        {field.corrections.map((correction) => (
          <li data-testid={`field-correction-${correction.correctionId}`} key={correction.correctionId}>
            <div>
              <strong>
                {correction.actorName ?? correction.actorSubjectId}
                {correction.actorRole ? ` · ${correction.actorRole}` : ""}
              </strong>
              <time dateTime={correction.correctedAt}> · {correction.correctedAt}</time>
            </div>
            <div>原因：{correction.reason}</div>
            <div>
              Before {formatValue(correction.beforeEffectiveValue, "effective")} → After{" "}
              {formatValue(correction.afterEffectiveValue, "effective")}
            </div>
            <div>
              Correction {correction.correctionId} · v{correction.version} · {correction.status}
            </div>
            <div>
              Snapshot {correction.sourceSnapshotId ?? "未提供"} · Parser run{" "}
              {correction.parserRunId ?? "未提供"}
            </div>
            {correction.supersedesCorrectionId ? (
              <div>Supersedes {correction.supersedesCorrectionId}</div>
            ) : null}
            {correction.reversalOfCorrectionId ? (
              <div>Reversal of {correction.reversalOfCorrectionId}</div>
            ) : null}
            {correction.reviewerSubjectId ? (
              <div>
                Reviewer {correction.reviewerName ?? correction.reviewerSubjectId}
                {correction.reviewedAt ? ` · ${correction.reviewedAt}` : ""}
              </div>
            ) : correction.status === "PENDING_REVIEW" || correction.status === "PROPOSED" ? (
              <div>等待獨立覆核者</div>
            ) : null}
          </li>
        ))}
      </ol>
    </details>
  );
}

function latestCorrection(
  corrections: readonly FieldCorrectionLineage[],
): FieldCorrectionLineage | undefined {
  return [...corrections].sort((left, right) => right.version - left.version)[0];
}

function latestAppliedCorrection(
  corrections: readonly FieldCorrectionLineage[],
): FieldCorrectionLineage | undefined {
  return [...corrections]
    .filter((correction) => correction.status === "APPLIED")
    .sort((left, right) => right.version - left.version)[0];
}

function formatValue(
  value: unknown,
  kind: "parsed" | "normalized" | "corrected" | "effective",
): string {
  if (isMissing(value)) {
    if (kind === "corrected") return "尚無人工修正";
    if (kind === "effective") return "有效值尚未提供";
    return "缺少";
  }
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function isMissing(value: unknown): boolean {
  return value === null || value === undefined || value === "";
}

function normalizePath(fieldPath: string): string {
  const lastSegment = fieldPath.split(".").pop() ?? fieldPath;
  return lastSegment.replace(/[_\-\s]/g, "").toLowerCase();
}

function humanizePath(fieldPath: string): string {
  const lastSegment = fieldPath.split(".").pop() ?? fieldPath;
  return lastSegment.replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/[_-]/g, " ");
}
