import type { Confidence, DataQuality, EvidenceComparable, EvidenceTrend, Factor } from "./contracts.ts";
import { Card } from "./Card.tsx";
import { DataStatusBadge } from "./StatusBadges.tsx";
import { Badge } from "./Badge.tsx";

export type EvidencePanelProps = {
  positiveFactors: readonly Factor[];
  negativeFactors: readonly Factor[];
  comparables?: readonly EvidenceComparable[];
  trend?: EvidenceTrend;
  confidence: Confidence;
  limitations: readonly string[];
  dataQuality?: DataQuality;
  title?: string;
  className?: string;
};

function FactorList({ title, factors }: { title: string; factors: readonly Factor[] }) {
  return (
    <section>
      <h3>{title}</h3>
      <ul className="odp-evidence__list">
        {factors.map((factor) => (
          <li key={`${factor.label}-${factor.value ?? ""}`}>
            <strong>{factor.label}</strong>
            {factor.value !== undefined ? <span>{factor.value}</span> : null}
            {factor.evidenceStrength !== undefined ? <span>strength {factor.evidenceStrength}</span> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function EvidencePanel({
  positiveFactors,
  negativeFactors,
  comparables = [],
  trend,
  confidence,
  limitations,
  dataQuality,
  title = "Evidence",
  className,
}: EvidencePanelProps) {
  return (
    <Card title={title} type="evidence" className={["odp-evidence", className].filter(Boolean).join(" ")}>
      <div className="odp-evidence__summary">
        <Badge label={`Confidence: ${confidence.level}`} tone={confidence.level === "high" ? "green" : confidence.level === "medium" ? "yellow" : "orange"} />
        {trend ? <span>{trend.label}: {trend.value}</span> : null}
        {dataQuality ? (
          <DataStatusBadge
            status={dataQuality.status}
            snapshotTime={dataQuality.snapshotTime}
            sources={dataQuality.sources}
            knownLimitations={dataQuality.warnings}
          />
        ) : null}
      </div>
      {limitations.length > 0 ? (
        <section className="odp-inline-warning" role="note">
          <h3>Limitations</h3>
          <ul>{limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
        </section>
      ) : null}
      <div className="odp-evidence__grid">
        <FactorList title="Positive factors" factors={positiveFactors} />
        <FactorList title="Negative factors" factors={negativeFactors} />
      </div>
      <section>
        <h3>Confidence reasons</h3>
        <ul>{confidence.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
      </section>
      {comparables.length > 0 ? (
        <section>
          <h3>Comparables</h3>
          <ul className="odp-evidence__list">
            {comparables.map((comparable) => (
              <li key={comparable.id}>
                {comparable.href ? <a href={comparable.href}>{comparable.label}</a> : <strong>{comparable.label}</strong>}
                {comparable.score !== undefined ? <span>{comparable.score}</span> : null}
                {comparable.summary ? <span>{comparable.summary}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </Card>
  );
}
