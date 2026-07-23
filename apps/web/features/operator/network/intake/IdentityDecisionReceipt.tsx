"use client";

import styles from "./identity.module.css";
import { IDENTITY_ACTION_LABEL, type IdentityDecisionReceipt } from "./identityTypes";

function ReceiptValue({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className={styles.receiptTerm}>{label}</div>
      <div className={styles.receiptValue}>{value}</div>
    </div>
  );
}

export function IdentityDecisionReceiptView({
  receipt,
  className,
}: {
  receipt: IdentityDecisionReceipt;
  className?: string;
}) {
  return (
    <section
      aria-labelledby="identity-receipt-title"
      className={`${styles.section} ${className ?? ""}`}
      data-testid="identity-durable-receipt"
    >
      <div className={styles.headingRow}>
        <h3 className={styles.title} id="identity-receipt-title">
          Identity decision receipt
        </h3>
        <span className={styles.badge}>{receipt.status}</span>
      </div>

      <p className={styles.success}>
        此區只呈現 command response 回傳的 authoritative receipt；缺少的實體不會由前端產生。
      </p>

      <div className={styles.receiptGrid}>
        <ReceiptValue label="Decision ID" value={receipt.decisionId} />
        <ReceiptValue label="Match case" value={receipt.matchCaseId} />
        <ReceiptValue
          label="Action"
          value={
            receipt.graphOperation ??
            (receipt.outcomeAction ? IDENTITY_ACTION_LABEL[receipt.outcomeAction] : "未提供")
          }
        />
        <ReceiptValue label="Graph plan" value={receipt.graphPlanId ?? "不適用"} />
        <ReceiptValue
          label="Original decision"
          value={receipt.originalDecisionId ?? "不適用"}
        />
        <ReceiptValue label="Occurred at" value={receipt.occurredAt} />
        <ReceiptValue
          label="Proposer"
          value={`${receipt.proposer.displayName} (${receipt.proposer.subjectId})`}
        />
        <ReceiptValue
          label="Reviewer"
          value={
            receipt.reviewer
              ? `${receipt.reviewer.displayName} (${receipt.reviewer.subjectId})`
              : "尚未審查"
          }
        />
        <ReceiptValue label="Listing ID" value={receipt.listingId ?? "未建立"} />
        <ReceiptValue label="ListingRevision ID" value={receipt.listingRevisionId ?? "未建立"} />
        <ReceiptValue label="Audit event" value={receipt.auditEventId} />
        <ReceiptValue label="Correlation ID" value={receipt.correlationId} />
        <ReceiptValue
          label="Effective edges"
          value={receipt.effectiveEdgeIds.length > 0 ? receipt.effectiveEdgeIds.join(", ") : "無"}
        />
        <ReceiptValue
          label="Superseded edges"
          value={receipt.supersededEdgeIds.length > 0 ? receipt.supersededEdgeIds.join(", ") : "無"}
        />
        <ReceiptValue
          label="Redirects"
          value={receipt.redirectIds.length > 0 ? receipt.redirectIds.join(", ") : "無"}
        />
        <ReceiptValue
          label="Resource versions"
          value={Object.entries(receipt.resourceVersions)
            .map(([resource, version]) => `${resource}=v${version}`)
            .join(", ")}
        />
      </div>

      <div>
        <h4>Decision reason</h4>
        <p className={styles.subtitle}>{receipt.reason}</p>
      </div>

      <div>
        <h4>Persisted lineage impact</h4>
        {receipt.lineageImpact.length > 0 ? (
          <ul className={styles.lineageList} data-testid="receipt-lineage-impact">
            {receipt.lineageImpact.map((impact) => (
              <li key={impact}>{impact}</li>
            ))}
          </ul>
        ) : (
          <p className={styles.hint}>Command response 未列出 lineage impact。</p>
        )}
      </div>
    </section>
  );
}
