"use client";

import { useMemo } from "react";
import styles from "./identity.module.css";
import {
  IDENTITY_COMPARISON_FIELD_ORDER,
  IDENTITY_FIELD_LABEL,
  type IdentityComparableValue,
  type IdentityComparisonContract,
} from "./identityTypes";

function renderValue(value: IdentityComparableValue | null): string {
  if (!value) return "未提供";
  if (value.masked) return "已遮罩";
  return value.displayValue || "未提供";
}

export function ListingCompareTable({
  comparison,
  className,
}: {
  comparison: IdentityComparisonContract;
  className?: string;
}) {
  const summary = useMemo(() => {
    const changed = IDENTITY_COMPARISON_FIELD_ORDER.filter(
      (key) => comparison.fields[key].state === "CHANGED",
    );
    const contradictions = IDENTITY_COMPARISON_FIELD_ORDER.filter(
      (key) => comparison.fields[key].state === "CONTRADICTION",
    );
    const target = comparison.currentListingId
      ? `既有物件 ${comparison.currentListingId}`
      : "沒有既有物件";

    return [
      `比對結果 ${comparison.outcome}，${target}。`,
      `信心度 ${(comparison.confidence * 100).toFixed(1)}%。`,
      changed.length > 0 ? `變更欄位：${changed.map((key) => IDENTITY_FIELD_LABEL[key]).join("、")}。` : "沒有變更欄位。",
      contradictions.length > 0
        ? `矛盾欄位：${contradictions.map((key) => IDENTITY_FIELD_LABEL[key]).join("、")}。`
        : "沒有矛盾欄位。",
    ].join(" ");
  }, [comparison]);

  return (
    <section
      aria-labelledby="identity-compare-title"
      className={`${styles.section} ${className ?? ""}`}
      data-testid="listing-compare-table"
    >
      <div className={styles.headingRow}>
        <h3 className={styles.title} id="identity-compare-title">
          既有物件與本次收件比對
        </h3>
        <span
          className={styles.badge}
          data-outcome={comparison.outcome}
          data-testid="compare-outcome-badge"
        >
          {comparison.outcome}
        </span>
      </div>

      <p className={styles.subtitle}>
        Match case <code className={styles.code}>{comparison.matchCaseId}</code>
        {" · "}version {comparison.matchCaseVersion}
        {" · "}confidence {comparison.confidence.toFixed(2)}
      </p>

      <p className={styles.notice} data-testid="intake-change-summary" role="status">
        {summary}
      </p>

      <div className={styles.tableWrap}>
        <table className={styles.compareTable} data-testid="compare-table-grid">
          <caption>
            欄位值均由 identity comparison response 提供；未提供的值不會由前端推測。
          </caption>
          <thead>
            <tr>
              <th scope="col">比對欄位</th>
              <th scope="col">
                既有物件
                <br />
                <code className={styles.code}>{comparison.currentListingId ?? "無"}</code>
              </th>
              <th scope="col">
                本次收件
                <br />
                <code className={styles.code}>{comparison.submittedIntakeId}</code>
              </th>
              <th scope="col">比對判定</th>
            </tr>
          </thead>
          <tbody>
            {IDENTITY_COMPARISON_FIELD_ORDER.map((key) => {
              const field = comparison.fields[key];
              return (
                <tr data-state={field.state} data-testid={`compare-row-${key}`} key={key}>
                  <th scope="row">{IDENTITY_FIELD_LABEL[key]}</th>
                  <td className={styles.value} data-testid={`compare-current-${key}`}>
                    {renderValue(field.current)}
                  </td>
                  <td className={styles.value} data-testid={`compare-submitted-${key}`}>
                    {renderValue(field.submitted)}
                  </td>
                  <td>
                    <strong>{field.state}</strong>
                    <div className={styles.hint}>{field.detail}</div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
