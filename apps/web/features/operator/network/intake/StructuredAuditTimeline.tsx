"use client";

import styles from "./intake.module.css";
import type {
  StructuredAuditBeforeAfter,
  StructuredAuditEvent,
} from "./evidenceContracts";

export type StructuredAuditTimelineProps = {
  events: StructuredAuditEvent[];
  testId?: string;
};

function displayValue(value: unknown): string {
  if (value === null) return "null";
  if (value === undefined) return "未提供";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function BeforeAfterTable({
  values,
  eventId,
}: {
  values: StructuredAuditBeforeAfter;
  eventId: string;
}) {
  const rows = Object.entries(values);
  if (rows.length === 0) return null;
  return (
    <div className={styles.tableScroll}>
      <table
        className={styles.auditChangeTable}
        data-testid={`audit-before-after-${eventId}`}
      >
        <caption>Before / after change set</caption>
        <thead>
          <tr>
            <th scope="col">Field</th>
            <th scope="col">Before</th>
            <th scope="col">After</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([field, change]) => (
            <tr key={field}>
              <th scope="row">{field}</th>
              <td>{displayValue(change.before)}</td>
              <td>{displayValue(change.after)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function StructuredAuditTimeline({
  events,
  testId = "structured-audit-timeline",
}: StructuredAuditTimelineProps) {
  return (
    <section className={styles.sectionBox} data-testid={testId}>
      <div className={styles.sectionLabel}>時間軸與結構化稽核 AUDIT TIMELINE</div>
      {events.length === 0 ? (
        <p className={styles.emptyState}>API 未回傳稽核事件。</p>
      ) : (
        <ol className={styles.structuredAuditList}>
          {[...events].reverse().map((event) => {
            const relatedIds = Object.entries(event.related_ids ?? {}).filter(
              ([, value]) => value !== null && value !== undefined && value !== "",
            );
            return (
              <li key={event.id} data-testid={`audit-event-${event.id}`}>
                <header className={styles.auditEventHeader}>
                  <div>
                    <strong>{event.action}</strong>
                    {event.result ? <span className={styles.chip}>{event.result}</span> : null}
                  </div>
                  <time dateTime={event.occurred_at}>{event.occurred_at}</time>
                </header>

                <dl className={styles.receiptList}>
                  <AuditValue label="Event ID" value={event.id} />
                  <AuditValue label="Audit event ID" value={event.audit_event_id} />
                  <AuditValue label="Actor" value={event.actor_name} />
                  <AuditValue label="Actor role" value={event.actor_role_id} />
                  <AuditValue label="Reason" value={event.reason} />
                  <AuditValue label="Reason code" value={event.reason_code} />
                  <AuditValue label="Message" value={event.message} />
                  <AuditValue label="Snapshot ID" value={event.source_snapshot_id} />
                  <AuditValue label="Parser run ID" value={event.parser_run_id} />
                  <AuditValue label="Parser version" value={event.parser_version} />
                  <AuditValue label="Correlation ID" value={event.correlation_id} />
                  <AuditValue label="Version" value={event.version} />
                  <AuditValue label="Evidence state" value={event.evidence_state} />
                  {relatedIds.map(([kind, value]) => (
                    <AuditValue key={kind} label={`Related ${kind}`} value={value} />
                  ))}
                </dl>

                {event.before_after ? (
                  <BeforeAfterTable values={event.before_after} eventId={event.id} />
                ) : null}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

function AuditValue({
  label,
  value,
}: {
  label: string;
  value: string | number | null | undefined;
}) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div className={styles.receiptValue}>
      <dt>{label}</dt>
      <dd>{String(value)}</dd>
    </div>
  );
}
