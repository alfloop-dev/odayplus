import type { AuditMeta } from "./contracts.ts";

export type AuditMetadataProps = {
  meta: AuditMeta;
  title?: string;
  className?: string;
};

export function AuditMetadata({ meta, title = "Audit metadata", className }: AuditMetadataProps) {
  const rows = [
    ["Actor", meta.actor],
    ["Decision time", meta.timestamp],
    ["Reason", meta.reason],
    ["Model version", meta.modelVersion],
    ["Policy version", meta.policyVersion],
    ["Feature snapshot", meta.featureSnapshotTime],
  ].filter((row): row is [string, string] => Boolean(row[1]));

  return (
    <section className={["odp-audit", className].filter(Boolean).join(" ")} aria-label={title}>
      <h3>{title}</h3>
      <dl>
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {meta.before !== undefined || meta.after !== undefined ? (
        <details>
          <summary>Before / after payload</summary>
          <pre>{JSON.stringify({ before: meta.before, after: meta.after }, null, 2)}</pre>
        </details>
      ) : null}
    </section>
  );
}
