"use client";

import { useMemo, useState, type FormEvent } from "react";
import type { SourcePolicyState } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { FIELD_GROUP_LABEL, FIELD_GROUP_ORDER } from "./ParsedDataReview";
import {
  useCorrectionDraft,
  type CorrectionDraftFailure,
  type CorrectionDraftIdentity,
  type DraftValue,
} from "./useCorrectionDraft";
import type { FieldGroupId } from "./FieldLineageRow";

export type AssistedEntryDraftValues = Record<string, DraftValue>;

export type AssistedEntrySubmission = {
  fields: AssistedEntryDraftValues;
  reason: string;
  riskAcknowledged: true;
  requiresIndependentReview: true;
  operationId: string;
  ifMatchVersion: number | null;
  sourcePolicy: "ASSISTED_ENTRY_ONLY";
  retrievalAllowed: false;
};

export type AssistedEntryCommitResult =
  | {
      status: "COMMITTED";
      authoritativeVersion: number;
      correctionIds: readonly string[];
    }
  | {
      status: "CONFLICT";
      failure: CorrectionDraftFailure;
    }
  | {
      status: "FAILED";
      failure: CorrectionDraftFailure;
    };

export type AssistedEntryFormProps = {
  policy: SourcePolicyState;
  originalUrl: string;
  sourceId: string;
  draftIdentity: Omit<CorrectionDraftIdentity, "purpose" | "fieldPath">;
  baseVersion: number;
  initialValues?: AssistedEntryDraftValues;
  disabled?: boolean;
  onCancel?: () => void;
  onCommit: (submission: AssistedEntrySubmission) => Promise<AssistedEntryCommitResult>;
};

type AssistedFieldDefinition = {
  key: string;
  label: string;
  group: FieldGroupId;
  type: "text" | "number" | "date" | "datetime-local" | "textarea";
  required?: boolean;
  placeholder?: string;
};

const ASSISTED_FIELDS: readonly AssistedFieldDefinition[] = [
  { key: "providerListingId", label: "來源物件 ID", group: "identity", type: "text" },
  { key: "listingType", label: "物件型態", group: "identity", type: "text" },
  { key: "listingStatus", label: "物件狀態", group: "identity", type: "text" },
  {
    key: "address",
    label: "物件地址",
    group: "location",
    type: "text",
    required: true,
    placeholder: "例：台北市信義區松仁路 100 號",
  },
  { key: "district", label: "行政區", group: "location", type: "text" },
  { key: "latitude", label: "緯度", group: "location", type: "number" },
  { key: "longitude", label: "經度", group: "location", type: "number" },
  { key: "rent", label: "月租金", group: "commercial", type: "number", required: true },
  { key: "currency", label: "幣別", group: "commercial", type: "text" },
  { key: "areaPing", label: "面積（坪）", group: "commercial", type: "number", required: true },
  { key: "managementFee", label: "管理費", group: "commercial", type: "number" },
  { key: "deposit", label: "押金", group: "commercial", type: "text" },
  { key: "floor", label: "樓層", group: "property", type: "text" },
  { key: "totalFloors", label: "總樓層", group: "property", type: "number" },
  { key: "frontageMeters", label: "面寬（公尺）", group: "property", type: "number" },
  { key: "parking", label: "停車條件", group: "property", type: "text" },
  { key: "temporaryStop", label: "臨停條件", group: "property", type: "text" },
  { key: "availableDate", label: "可用日期", group: "property", type: "date" },
  {
    key: "feasibilityFlags",
    label: "描述衍生可行性註記",
    group: "property",
    type: "textarea",
  },
  { key: "observedAt", label: "人工觀測時間", group: "provenance", type: "datetime-local" },
  { key: "sourceNote", label: "來源註記", group: "provenance", type: "textarea" },
];

export function AssistedEntryForm({
  policy,
  originalUrl,
  sourceId,
  draftIdentity,
  baseVersion,
  initialValues = {},
  disabled = false,
  onCancel,
  onCommit,
}: AssistedEntryFormProps) {
  const initialSignature = JSON.stringify(initialValues);
  const stableInitialValues = useMemo<AssistedEntryDraftValues>(
    () => ({ currency: "TWD", ...initialValues }),
    // The serialized value is the intended reset boundary; callers frequently
    // construct the initial map inline.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [initialSignature],
  );
  const controller = useCorrectionDraft({
    identity: { ...draftIdentity, purpose: "assisted-entry" },
    initialFields: stableInitialValues,
    baseVersion,
  });
  const [localError, setLocalError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (policy !== "ASSISTED_ENTRY_ONLY") {
    return (
      <section className={styles.errorPanel} data-testid="assisted-entry-policy-guard" role="alert">
        <span className={styles.errorSummary}>人工補錄未開放</span>
        <span>
          此元件只接受 `ASSISTED_ENTRY_ONLY`。目前政策為 {policy}，未執行 retrieval，也未送出任何資料。
        </span>
      </section>
    );
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled || submitting) return;

    const validationError = validateAssistedEntry(controller.draft.fields, controller.draft.reason);
    if (validationError) {
      setLocalError(validationError);
      return;
    }
    if (!controller.draft.riskAcknowledged) {
      setLocalError("請確認你了解重要欄位會進入獨立人工覆核。");
      return;
    }

    setLocalError(null);
    setSubmitting(true);
    controller.markSubmitting();
    const submission: AssistedEntrySubmission = {
      fields: serializeAssistedFields(controller.draft.fields),
      reason: controller.draft.reason.trim(),
      riskAcknowledged: true,
      requiresIndependentReview: true,
      operationId: controller.draft.operationId,
      ifMatchVersion: controller.draft.baseVersion,
      sourcePolicy: "ASSISTED_ENTRY_ONLY",
      retrievalAllowed: false,
    };

    try {
      const result = await onCommit(submission);
      if (result.status === "COMMITTED") {
        controller.clearAfterCommit();
      } else {
        controller.markFailure(result.failure, result.status === "CONFLICT");
      }
    } catch (error) {
      controller.markFailure({
        code: "NETWORK_RESULT_UNKNOWN",
        summary: error instanceof Error ? error.message : "網路錯誤，送出結果尚未確認。",
        occurredAt: new Date().toISOString(),
        retryable: true,
      });
    } finally {
      setSubmitting(false);
    }
  }

  const failure = controller.draft.lastFailure;

  return (
    <form data-testid="assisted-entry-form" onSubmit={submit}>
      <div className={styles.noteBox}>
        <strong>僅人工補錄 ASSISTED_ENTRY_ONLY</strong>
        <div>系統不會讀取此來源頁面，也不會要求密碼、Cookie、Token 或私人 API。</div>
        <div>
          來源：{sourceId} ·{" "}
          <a href={originalUrl} rel="noreferrer noopener" target="_blank">
            {originalUrl}
          </a>
        </div>
        <div data-testid="assisted-entry-draft-state">
          草稿 {controller.draft.status} · operation {controller.draft.operationId} · 更新{" "}
          {controller.draft.updatedAt}
        </div>
      </div>

      {FIELD_GROUP_ORDER.map((group) => {
        const definitions = ASSISTED_FIELDS.filter((field) => field.group === group);
        return (
          <fieldset
            className={styles.sectionBox}
            data-testid={`assisted-entry-group-${group}`}
            key={group}
          >
            <legend className={styles.sectionHead}>{FIELD_GROUP_LABEL[group]}</legend>
            {group === "provenance" ? (
              <div className={styles.noteBox}>
                原始 URL 與來源政策由系統保留；人工補錄不會產生 parser run 或來源快照。
              </div>
            ) : null}
            <div className={styles.grid2} style={{ padding: "10px" }}>
              {definitions.map((definition) => (
                <AssistedField
                  definition={definition}
                  disabled={disabled || submitting}
                  key={definition.key}
                  onChange={(value) => controller.setField(definition.key, value)}
                  value={controller.draft.fields[definition.key] ?? ""}
                />
              ))}
            </div>
          </fieldset>
        );
      })}

      <div className={styles.sectionBox}>
        <div className={styles.sectionHead}>重要變更覆核 MATERIAL REVIEW</div>
        <div style={{ padding: "10px" }}>
          <label className={styles.fieldLabel} htmlFor="assisted-entry-reason">
            人工補錄原因（必填）
          </label>
          <textarea
            className={styles.textarea}
            data-testid="assisted-entry-reason"
            disabled={disabled || submitting}
            id="assisted-entry-reason"
            onChange={(event) => controller.setReason(event.target.value)}
            rows={3}
            value={controller.draft.reason}
          />
        </div>
        <div className={styles.riskSummaryText}>
          地址、租金、面積、來源識別與比對結果屬重要欄位。送出後只會建立修正提案，不會在前端直接成為
          authoritative value；提案者不能自行核准，必須由獨立覆核者確認。
        </div>
        <label className={styles.checkboxRow} htmlFor="assisted-entry-risk-ack">
          <input
            checked={controller.draft.riskAcknowledged}
            data-testid="assisted-entry-risk-ack"
            disabled={disabled || submitting}
            id="assisted-entry-risk-ack"
            onChange={(event) => controller.setRiskAcknowledged(event.target.checked)}
            type="checkbox"
          />
          <span>我了解這些值會留下 before/after 與 lineage，且重要變更需要獨立人工覆核。</span>
        </label>
      </div>

      {localError ? (
        <div className={styles.errorPanel} data-testid="assisted-entry-validation-error" role="alert">
          <span className={styles.errorSummary}>{localError}</span>
        </div>
      ) : null}

      {failure ? (
        <div className={styles.errorPanel} data-testid="assisted-entry-submit-error" role="alert">
          <span className={styles.errorSummary}>{failure.summary}</span>
          <span className={styles.errorMeta}>
            {failure.code} · {failure.occurredAt}
            {failure.correlationId ? ` · correlation ${failure.correlationId}` : ""}
          </span>
          <span className={styles.errorNext}>
            草稿與 operation ID 已保留
            {failure.currentVersion ? ` · server version ${failure.currentVersion}` : ""}。
          </span>
          {controller.draft.status === "CONFLICT" && failure.currentVersion ? (
            <button
              className={styles.secondaryButton}
              onClick={() => controller.rebase(failure.currentVersion!)}
              type="button"
            >
              套用最新版本並保留草稿
            </button>
          ) : null}
        </div>
      ) : null}

      {!controller.persistenceAvailable ? (
        <div className={styles.warnNote} role="status">
          瀏覽器無法使用 durable storage；離開頁面前請勿關閉此表單。
        </div>
      ) : null}

      <div className={styles.dialogFooter}>
        {onCancel ? (
          <button
            className={styles.secondaryButton}
            disabled={submitting}
            onClick={onCancel}
            type="button"
          >
            關閉（保留草稿）
          </button>
        ) : null}
        <button
          className={styles.primaryButton}
          data-testid="assisted-entry-submit"
          disabled={disabled || submitting}
          type="submit"
        >
          {submitting ? "送出中…" : failure?.retryable ? "以相同 operation 重試" : "送出人工補錄"}
        </button>
      </div>
    </form>
  );
}

function AssistedField({
  definition,
  disabled,
  onChange,
  value,
}: {
  definition: AssistedFieldDefinition;
  disabled: boolean;
  onChange: (value: string) => void;
  value: DraftValue;
}) {
  const id = `assisted-entry-${definition.key}`;
  return (
    <div>
      <label className={styles.fieldLabel} htmlFor={id}>
        {definition.label}
        {definition.required ? "（必填）" : ""}
      </label>
      {definition.type === "textarea" ? (
        <textarea
          className={styles.textarea}
          data-testid={id}
          disabled={disabled}
          id={id}
          onChange={(event) => onChange(event.target.value)}
          rows={3}
          value={String(value ?? "")}
        />
      ) : (
        <input
          className={styles.input}
          data-testid={id}
          disabled={disabled}
          id={id}
          inputMode={definition.type === "number" ? "decimal" : undefined}
          onChange={(event) => onChange(event.target.value)}
          placeholder={definition.placeholder}
          required={definition.required}
          type={definition.type}
          value={String(value ?? "")}
        />
      )}
    </div>
  );
}

function validateAssistedEntry(fields: AssistedEntryDraftValues, reason: string): string | null {
  if (!String(fields.address ?? "").trim()) return "請填寫物件地址。";
  if (!isPositiveNumber(fields.rent)) return "租金必須是大於 0 的數值。";
  if (!isPositiveNumber(fields.areaPing)) return "面積必須是大於 0 的數值。";
  if (reason.trim().length < 3) return "人工補錄原因至少需要 3 個字元。";
  return null;
}

function isPositiveNumber(value: DraftValue | undefined): boolean {
  if (value === null || value === undefined || value === "") return false;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0;
}

function serializeAssistedFields(fields: AssistedEntryDraftValues): AssistedEntryDraftValues {
  const numericPaths = new Set(
    ASSISTED_FIELDS.filter((definition) => definition.type === "number").map(
      (definition) => definition.key,
    ),
  );
  return Object.fromEntries(
    Object.entries(fields)
      .filter(([, value]) => value !== "" && value !== null && value !== undefined)
      .map(([key, value]) => [key, numericPaths.has(key) ? Number(value) : value]),
  );
}
