import type { CSSProperties, ReactNode } from "react";
import {
  dataStatusTone,
  fourLightTone,
  type AdLiftReportCardContract,
  type AuditMeta,
  type CandidateSiteCardContract,
  type ComparableStore,
  type Confidence,
  type DataQuality,
  type DecisionAuditStep,
  type DecisionAuditTimelineContract,
  type EntityRef,
  type Factor,
  type ForecastBandChartContract,
  type FourLightBadgeContract,
  type HeatZoneScore,
  type InterventionTimelineContract,
  type InterventionTimelineStep,
  type Interval,
  type ModelReleaseCardContract,
  type NetPlanAction,
  type NetPlanScenarioCardContract,
  type PricingPlanComparisonContract,
  type RiskLevel,
  type RootCauseEvidenceCardContract,
  type SiteScoreReportSummaryContract,
  type StatusTone,
  type TimelineEvent,
  type ValuationLens,
  type ValuationRangeChartContract,
} from "@oday-plus/domain-types";

type Testable = {
  "data-testid"?: string;
};

type ActionProps = {
  onOpen?: () => void;
};

export type HeatZoneScoreCardProps = Testable &
  ActionProps & {
    score: HeatZoneScore;
    dataQuality?: DataQuality;
    audit?: AuditMeta;
  };

export type CandidateSiteCardProps = Testable &
  ActionProps & {
    candidate: CandidateSiteCardContract;
  };

export type SiteScoreReportSummaryProps = Testable & {
  report: SiteScoreReportSummaryContract;
  onRequestReview?: () => void;
};

export type ForecastBandChartProps = Testable & {
  forecast: ForecastBandChartContract;
  height?: number;
  showSiteScoreBaseline?: boolean;
  showInterventions?: boolean;
  showLegend?: boolean;
  onPointClick?: (point: ForecastBandChartContract["points"][number]) => void;
};

export type FourLightBadgeProps = Testable & {
  badge: FourLightBadgeContract;
};

export type RootCauseEvidenceCardProps = Testable & {
  evidence: RootCauseEvidenceCardContract;
};

export type InterventionTimelineProps = Testable & {
  timeline: InterventionTimelineContract;
};

export type PricingPlanComparisonProps = Testable & {
  comparison: PricingPlanComparisonContract;
  onRequestApproval?: () => void;
};

export type AdLiftReportCardProps = Testable & {
  report: AdLiftReportCardContract;
};

export type ValuationRangeChartProps = Testable & {
  valuation: ValuationRangeChartContract;
};

export type NetPlanScenarioCardProps = Testable & {
  scenario: NetPlanScenarioCardContract;
};

export type ModelReleaseCardProps = Testable & {
  release: ModelReleaseCardContract;
  onOpenAudit?: () => void;
};

export type DecisionAuditTimelineProps = Testable & {
  timeline: DecisionAuditTimelineContract;
};

const riskTone: Record<RiskLevel, StatusTone> = {
  low: "green",
  medium: "yellow",
  high: "orange",
  critical: "red",
};

const netPlanActions: readonly NetPlanAction[] = ["OPEN", "KEEP", "IMPROVE", "MOVE", "EXIT", "HOLD"];
const valuationLenses: readonly ValuationLens[] = ["income", "asset", "market", "blended"];

const interventionSteps: readonly InterventionTimelineStep[] = [
  "Triggered",
  "Eligibility checked",
  "Action built",
  "Conflict checked",
  "Approved",
  "Executed",
  "Observation started",
  "Outcome collected",
  "Effect evaluated",
  "Closed",
];

const decisionAuditSteps: readonly DecisionAuditStep[] = [
  "Prediction generated",
  "Recommendation generated",
  "Human review requested",
  "Human decision submitted",
  "Execution started",
  "Outcome observed",
  "Feedback written to label registry",
];

function cx(...classes: Array<string | false | undefined>): string {
  return classes.filter(Boolean).join(" ");
}

function entityLabel(entity: EntityRef): string {
  return `${entity.label} (${entity.entityType}:${entity.entityId})`;
}

function formatNumber(value: number, options?: Intl.NumberFormatOptions): string {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2, ...options }).format(value);
}

function formatMoney(value: number): string {
  return formatNumber(value, { style: "currency", currency: "TWD", maximumFractionDigits: 0 });
}

function formatPercent(value: number): string {
  return `${formatNumber(value * 100, { maximumFractionDigits: 1 })}%`;
}

function formatInterval(interval: Interval): string {
  const unit = interval.unit ? ` ${interval.unit}` : "";
  return `P10 ${formatNumber(interval.p10)}${unit} / P50 ${formatNumber(interval.p50)}${unit} / P90 ${formatNumber(interval.p90)}${unit}`;
}

function confidenceTone(confidence: Confidence): StatusTone {
  if (confidence.level === "high") return "green";
  if (confidence.level === "medium") return "yellow";
  return "orange";
}

function dataQualityLabel(dataQuality?: DataQuality): string {
  if (!dataQuality) return "Data quality not provided";
  return `${dataQuality.status} snapshot ${dataQuality.snapshotTime}`;
}

function visibilityFor(field: string, permissions?: { field: string; visibility: string; reason?: string }[]): string {
  return permissions?.find((permission) => permission.field === field)?.visibility ?? "visible";
}

function displayProtectedValue(
  value: ReactNode,
  field: string,
  permissions?: { field: string; visibility: string; reason?: string }[],
): ReactNode {
  const visibility = visibilityFor(field, permissions);
  if (visibility === "hidden") return null;
  if (visibility === "masked") return <span className="odp-domain-muted">Masked</span>;
  if (visibility === "aggregated") return <span className="odp-domain-muted">Aggregated only</span>;
  return value;
}

function toneStyle(tone: StatusTone): CSSProperties {
  return {
    "--odp-domain-tone-bg": `var(--odp-color-status-${tone}-soft)`,
    "--odp-domain-tone-fg": `var(--odp-color-status-${tone}-on)`,
  } as CSSProperties;
}

function StatusBadge({ label, tone = "gray", marker }: { label: string; tone?: StatusTone; marker?: string }) {
  return (
    <span className="odp-domain-badge" style={toneStyle(tone)}>
      {marker ? <span aria-hidden="true">{marker}</span> : null}
      <span>{label}</span>
    </span>
  );
}

function DomainCard({
  title,
  eyebrow,
  badge,
  children,
  testId,
}: {
  title: string;
  eyebrow?: string;
  badge?: ReactNode;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <article className="odp-domain-card" data-testid={testId}>
      <header className="odp-domain-card__header">
        <div>
          {eyebrow ? <p className="odp-domain-eyebrow">{eyebrow}</p> : null}
          <h2 className="odp-domain-card__title">{title}</h2>
        </div>
        {badge ? <div className="odp-domain-card__badge">{badge}</div> : null}
      </header>
      {children}
    </article>
  );
}

function Field({ label, value }: { label: string; value: ReactNode }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div className="odp-domain-field">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function FieldGrid({ children }: { children: ReactNode }) {
  return <dl className="odp-domain-field-grid">{children}</dl>;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="odp-domain-section" aria-label={title}>
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function SimpleList({ items, emptyLabel = "None" }: { items: readonly ReactNode[]; emptyLabel?: string }) {
  if (items.length === 0) return <p className="odp-domain-muted">{emptyLabel}</p>;
  return (
    <ul className="odp-domain-list">
      {items.map((item, index) => (
        <li key={String(index)}>{item}</li>
      ))}
    </ul>
  );
}

function FactorList({ factors }: { factors: readonly Factor[] }) {
  return (
    <SimpleList
      items={factors.map((factor) => (
        <span className="odp-domain-factor">
          <span>{factor.label}</span>
          {factor.value !== undefined ? <strong>{factor.value}</strong> : null}
          {factor.impact ? <StatusBadge label={factor.impact} tone={factor.impact === "positive" ? "green" : factor.impact === "negative" ? "red" : "gray"} /> : null}
          {factor.evidenceStrength !== undefined ? <span>Evidence {formatPercent(factor.evidenceStrength)}</span> : null}
        </span>
      ))}
    />
  );
}

function ConfidencePanel({ confidence }: { confidence: Confidence }) {
  return (
    <div className="odp-domain-evidence-panel">
      <StatusBadge label={`Confidence ${confidence.level}`} tone={confidenceTone(confidence)} />
      <SimpleList items={confidence.reasons} emptyLabel="No confidence reasons provided" />
    </div>
  );
}

function DataQualityPanel({ dataQuality }: { dataQuality?: DataQuality }) {
  return (
    <div className="odp-domain-meta" aria-label="Data quality">
      <StatusBadge label={dataQuality ? dataQuality.status : "DATA QUALITY UNKNOWN"} tone={dataQuality ? dataStatusTone[dataQuality.status] : "gray"} />
      <span>{dataQualityLabel(dataQuality)}</span>
      {dataQuality?.sources.length ? <span>Sources: {dataQuality.sources.join(", ")}</span> : null}
      {dataQuality?.warnings.length ? <span>Warnings: {dataQuality.warnings.join("; ")}</span> : null}
    </div>
  );
}

function AuditMetaPanel({ audit }: { audit?: AuditMeta }) {
  if (!audit) return null;
  return (
    <div className="odp-domain-meta" aria-label="Audit metadata">
      <span>Audit actor: {audit.actor}</span>
      <span>At: {audit.timestamp}</span>
      {audit.reason ? <span>Reason: {audit.reason}</span> : null}
      {audit.modelVersion ? <span>Model: {audit.modelVersion}</span> : null}
      {audit.policyVersion ? <span>Policy: {audit.policyVersion}</span> : null}
      {audit.featureSnapshotTime ? <span>Feature snapshot: {audit.featureSnapshotTime}</span> : null}
    </div>
  );
}

function IntervalBand({ label, interval }: { label: string; interval: Interval }) {
  const max = Math.max(Math.abs(interval.p10), Math.abs(interval.p50), Math.abs(interval.p90), 1);
  const p10 = Math.max(0, Math.min(100, (Math.abs(interval.p10) / max) * 100));
  const p50 = Math.max(0, Math.min(100, (Math.abs(interval.p50) / max) * 100));
  const p90 = Math.max(0, Math.min(100, (Math.abs(interval.p90) / max) * 100));
  const style = {
    "--odp-domain-p10": `${p10}%`,
    "--odp-domain-p50": `${p50}%`,
    "--odp-domain-p90": `${p90}%`,
  } as CSSProperties;

  return (
    <div className="odp-domain-interval" style={style}>
      <div className="odp-domain-interval__label">
        <span>{label}</span>
        <strong>{formatInterval(interval)}</strong>
      </div>
      <div className="odp-domain-interval__track" aria-hidden="true">
        <span className="odp-domain-interval__band" />
        <span className="odp-domain-interval__median" />
      </div>
    </div>
  );
}

function TimelineList<TStep extends string>({
  steps,
  nodes,
}: {
  steps: readonly TStep[];
  nodes: ReadonlyArray<TimelineEvent & { step: TStep }>;
}) {
  return (
    <ol className="odp-domain-timeline">
      {steps.map((step) => {
        const node = nodes.find((item) => item.step === step);
        return (
          <li key={step} className={cx("odp-domain-timeline__item", !node && "odp-domain-timeline__item--missing")}>
            <div className="odp-domain-timeline__marker" aria-hidden="true" />
            <div>
              <h3>{step}</h3>
              {node ? (
                <>
                  <p>{node.description}</p>
                  <div className="odp-domain-meta">
                    <span>{node.status}</span>
                    <span>{node.actor}</span>
                    <span>{node.timestamp}</span>
                    {node.relatedArtifact ? <span>{entityLabel(node.relatedArtifact)}</span> : null}
                  </div>
                </>
              ) : (
                <p className="odp-domain-muted">Audit node missing</p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function ComparableStoreTable({ stores }: { stores: readonly ComparableStore[] }) {
  if (stores.length === 0) return <p className="odp-domain-muted">No comparable stores provided</p>;
  return (
    <div className="odp-domain-table-wrap">
      <table className="odp-domain-table">
        <thead>
          <tr>
            <th scope="col">Store</th>
            <th scope="col">Similarity</th>
            <th scope="col">Distance</th>
            <th scope="col">Revenue M6</th>
          </tr>
        </thead>
        <tbody>
          {stores.map((store) => (
            <tr key={store.storeId}>
              <td>{store.storeId}</td>
              <td>{formatPercent(store.similarityScore)}</td>
              <td>{store.distance !== undefined ? `${formatNumber(store.distance)} km` : "n/a"}</td>
              <td>{store.revenueM6 !== undefined ? formatMoney(store.revenueM6) : "n/a"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function HeatZoneScoreCard({ score, dataQuality, audit, onOpen, "data-testid": testId = "heat-zone-score-card" }: HeatZoneScoreCardProps) {
  const title = [score.admin_city, score.admin_district].filter(Boolean).join(" / ") || score.heat_zone_id;
  return (
    <DomainCard title={title} eyebrow={`HeatZone ${score.heat_zone_id}`} badge={<StatusBadge label={score.state} tone={score.confidence >= 0.75 ? "green" : score.confidence >= 0.5 ? "yellow" : "orange"} />} testId={testId}>
      <FieldGrid>
        <Field label="H3 index" value={score.h3_index} />
        <Field label="H3 resolution" value={score.h3_resolution} />
        <Field label="Heat zone score" value={formatNumber(score.score)} />
        <Field label="Priority rank" value={score.priority_rank} />
        <Field label="Confidence" value={formatPercent(score.confidence)} />
        <Field label="Last scored" value={score.last_scored_at} />
      </FieldGrid>
      <Section title="Score breakdown">
        <FieldGrid>
          <Field label="Unmet demand" value={formatNumber(score.unmet_demand_score)} />
          <Field label="ODay G2 fit" value={formatNumber(score.format_fit_score)} />
          <Field label="Cannibalization risk" value={formatNumber(score.cannibalization_risk_score)} />
          <Field label="Rent feasibility" value={formatNumber(score.rent_feasibility_score)} />
          <Field label="Listing availability" value={formatNumber(score.listing_availability_score)} />
        </FieldGrid>
      </Section>
      <Section title="Evidence">
        <SimpleList items={score.reasons} emptyLabel="No positive reasons provided" />
        {score.warnings.length ? <SimpleList items={score.warnings.map((warning) => <strong>{warning}</strong>)} /> : null}
      </Section>
      <DataQualityPanel dataQuality={dataQuality} />
      <div className="odp-domain-meta">
        <span>Model {score.model_version}</span>
        <span>Feature {score.feature_version}</span>
        <span>Feature snapshot {score.feature_snapshot_time}</span>
        <span>Prediction origin {score.prediction_origin_time}</span>
      </div>
      <AuditMetaPanel audit={audit} />
      {onOpen ? (
        <button className="odp-domain-button" type="button" onClick={onOpen}>
          Open detail
        </button>
      ) : null}
    </DomainCard>
  );
}

export function CandidateSiteCard({ candidate, onOpen, "data-testid": testId = "candidate-site-card" }: CandidateSiteCardProps) {
  return (
    <DomainCard title={candidate.address} eyebrow={`Candidate ${candidate.candidateSiteId}`} badge={<StatusBadge label={candidate.status} tone={candidate.status === "FAILED_HARD_RULE" || candidate.status === "REJECTED" ? "red" : "blue"} />} testId={testId}>
      <FieldGrid>
        <Field label="HeatZone" value={entityLabel(candidate.heatZone)} />
        <Field label="Geocode confidence" value={<span>{candidate.geocodeConfidence.level}: {candidate.geocodeConfidence.reasons.join("; ")}</span>} />
        <Field label="Rent" value={displayProtectedValue(candidate.rent !== undefined ? formatMoney(candidate.rent) : "n/a", "rent", candidate.fieldPermissions)} />
        <Field label="Area" value={displayProtectedValue(candidate.area !== undefined ? `${formatNumber(candidate.area)} ping` : "n/a", "area", candidate.fieldPermissions)} />
        <Field label="Frontage" value={displayProtectedValue(candidate.frontage !== undefined ? `${formatNumber(candidate.frontage)} m` : "n/a", "frontage", candidate.fieldPermissions)} />
        <Field label="Floor" value={displayProtectedValue(candidate.floor ?? "n/a", "floor", candidate.fieldPermissions)} />
        <Field label="Parking / temporary stop" value={displayProtectedValue(candidate.parkingOrTemporaryStop ?? "n/a", "parkingOrTemporaryStop", candidate.fieldPermissions)} />
        <Field label="Listing source" value={candidate.listingSource} />
      </FieldGrid>
      <Section title="Feasibility flags">
        <SimpleList items={candidate.feasibilityFlags} emptyLabel="No feasibility flags" />
      </Section>
      <DataQualityPanel dataQuality={candidate.dataQuality} />
      {onOpen ? (
        <button className="odp-domain-button" type="button" onClick={onOpen}>
          Open candidate
        </button>
      ) : null}
    </DomainCard>
  );
}

export function SiteScoreReportSummary({ report, onRequestReview, "data-testid": testId = "site-score-report-summary" }: SiteScoreReportSummaryProps) {
  return (
    <DomainCard title={entityLabel(report.candidateSite)} eyebrow={`SiteScore ${report.reportId}`} badge={<StatusBadge label={report.recommendation} tone={report.recommendation === "GO" ? "green" : report.recommendation === "REJECT" ? "red" : "yellow"} />} testId={testId}>
      <Section title="Forecast intervals">
        <IntervalBand label="M1" interval={report.m1} />
        <IntervalBand label="M3" interval={report.m3} />
        <IntervalBand label="M6" interval={report.m6} />
        <IntervalBand label="M12" interval={report.m12} />
        {report.mature ? <IntervalBand label="Mature" interval={report.mature} /> : null}
        <IntervalBand label="Payback period" interval={report.paybackPeriod} />
      </Section>
      <FieldGrid>
        <Field label="Rent reasonableness" value={<StatusBadge label={report.rentReasonableness} tone={riskTone[report.rentReasonableness]} />} />
        <Field label="Cannibalization risk" value={<StatusBadge label={report.cannibalizationRisk} tone={riskTone[report.cannibalizationRisk]} />} />
        <Field label="Decision status" value={report.decisionStatus} />
        <Field label="Model version" value={report.modelVersion} />
        <Field label="Policy version" value={report.policyVersion ?? "n/a"} />
        <Field label="Feature snapshot" value={report.featureSnapshotTime} />
      </FieldGrid>
      <Section title="Positive evidence">
        <FactorList factors={report.keyPositiveFactors} />
      </Section>
      <Section title="Negative evidence">
        <FactorList factors={report.keyNegativeFactors} />
      </Section>
      <Section title="Comparable stores">
        <ComparableStoreTable stores={report.comparableStores} />
      </Section>
      <ConfidencePanel confidence={report.confidence} />
      <DataQualityPanel dataQuality={report.dataQuality} />
      <AuditMetaPanel audit={report.audit} />
      {onRequestReview ? (
        <button className="odp-domain-button" type="button" onClick={onRequestReview}>
          Request review
        </button>
      ) : null}
    </DomainCard>
  );
}

export function ForecastBandChart({
  forecast,
  height = 280,
  showSiteScoreBaseline = true,
  showInterventions = true,
  showLegend = true,
  onPointClick,
  "data-testid": testId = "forecast-band-chart",
}: ForecastBandChartProps) {
  const max = Math.max(...forecast.points.map((point) => point.forecastP90), 1);
  return (
    <DomainCard title={entityLabel(forecast.store)} eyebrow={`${forecast.metric} forecast · ${forecast.horizon} · ${forecast.granularity}`} badge={<StatusBadge label={`Confidence ${forecast.confidence.level}`} tone={confidenceTone(forecast.confidence)} />} testId={testId}>
      {showLegend ? (
        <div className="odp-domain-legend" aria-label="Forecast legend">
          <span>P10-P90 band</span>
          <span>P50 forecast</span>
          <span>Actual</span>
          {showSiteScoreBaseline ? <span>SiteScore baseline</span> : null}
          {showInterventions ? <span>Intervention marker</span> : null}
        </div>
      ) : null}
      <div className="odp-domain-chart" style={{ minHeight: height }} role="img" aria-label={`${forecast.metric} forecast with P10, P50, and P90 uncertainty bands`}>
        {forecast.points.map((point) => {
          const band = Math.max(2, (point.forecastP90 / max) * 100);
          const median = Math.max(2, (point.forecastP50 / max) * 100);
          const actual = point.actual !== undefined ? Math.max(2, (point.actual / max) * 100) : undefined;
          return (
            <button
              className={cx("odp-domain-chart__point", point.anomaly && "odp-domain-chart__point--anomaly")}
              key={point.date}
              type="button"
              onClick={() => onPointClick?.(point)}
              style={{ "--odp-domain-band": `${band}%`, "--odp-domain-median": `${median}%`, "--odp-domain-actual": actual ? `${actual}%` : "0%" } as CSSProperties}
              aria-label={`${point.date}: ${formatNumber(point.forecastP50)} forecast P50, range ${formatNumber(point.forecastP10)} to ${formatNumber(point.forecastP90)}`}
            >
              <span className="odp-domain-chart__bar" />
              <span className="odp-domain-chart__median" />
              {actual ? <span className="odp-domain-chart__actual" /> : null}
              {showSiteScoreBaseline && point.siteScoreBaseline !== undefined ? <span className="odp-domain-chart__baseline">{formatNumber(point.siteScoreBaseline)}</span> : null}
              {showInterventions && point.interventionMarker ? <span className="odp-domain-chart__marker">{point.interventionMarker.label}</span> : null}
              <span className="odp-domain-chart__date">{point.date}</span>
            </button>
          );
        })}
      </div>
      <div className="odp-domain-table-wrap">
        <table className="odp-domain-table">
          <caption>Forecast data table</caption>
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">Actual</th>
              <th scope="col">P10</th>
              <th scope="col">P50</th>
              <th scope="col">P90</th>
              <th scope="col">Model</th>
            </tr>
          </thead>
          <tbody>
            {forecast.points.map((point) => (
              <tr key={point.date}>
                <td>{point.date}</td>
                <td>{point.actual !== undefined ? formatNumber(point.actual) : "n/a"}</td>
                <td>{formatNumber(point.forecastP10)}</td>
                <td>{formatNumber(point.forecastP50)}</td>
                <td>{formatNumber(point.forecastP90)}</td>
                <td>{point.modelVersion ?? "n/a"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ConfidencePanel confidence={forecast.confidence} />
      <DataQualityPanel dataQuality={forecast.dataQuality} />
    </DomainCard>
  );
}

export function FourLightBadge({ badge, "data-testid": testId = "four-light-badge" }: FourLightBadgeProps) {
  const content = (
    <>
      <StatusBadge label={badge.light} tone={fourLightTone[badge.light]} marker={badge.light === "GREEN" ? "G" : badge.light === "YELLOW" ? "Y" : badge.light === "ORANGE" ? "O" : "R"} />
      <span className="odp-domain-sr-only">Trigger conditions: {badge.triggerConditions.join("; ")}</span>
    </>
  );
  return (
    <span className="odp-domain-four-light" data-testid={testId} title={badge.triggerConditions.join("; ")}>
      {badge.alertHref ? (
        <a href={badge.alertHref} className="odp-domain-link">
          {content}
        </a>
      ) : (
        content
      )}
    </span>
  );
}

export function RootCauseEvidenceCard({ evidence, "data-testid": testId = "root-cause-evidence-card" }: RootCauseEvidenceCardProps) {
  return (
    <DomainCard title={evidence.causeCandidate} eyebrow="Root cause evidence" badge={<StatusBadge label={`Evidence ${formatPercent(evidence.evidenceStrength)}`} tone={evidence.evidenceStrength >= 0.75 ? "green" : evidence.evidenceStrength >= 0.45 ? "yellow" : "orange"} />} testId={testId}>
      <Section title="Supporting signals">
        <FactorList factors={evidence.supportingSignals} />
      </Section>
      <Section title="Contradicting signals">
        <FactorList factors={evidence.contradictingSignals} />
      </Section>
      <FieldGrid>
        <Field label="Recommended next check" value={evidence.recommendedNextCheck} />
      </FieldGrid>
      <ConfidencePanel confidence={evidence.dataConfidence} />
      <DataQualityPanel dataQuality={evidence.dataQuality} />
    </DomainCard>
  );
}

export function InterventionTimeline({ timeline, "data-testid": testId = "intervention-timeline" }: InterventionTimelineProps) {
  return (
    <DomainCard title={timeline.interventionType} eyebrow={`Intervention ${timeline.interventionId} · ${entityLabel(timeline.store)}`} badge={<StatusBadge label={`Evidence ${timeline.evidenceLevel}`} tone={timeline.evidenceLevel === "high" ? "green" : timeline.evidenceLevel === "medium" ? "yellow" : "orange"} />} testId={testId}>
      <FieldGrid>
        <Field label="Eligibility" value={timeline.eligibilityStatus} />
        <Field label="Conflict" value={timeline.conflictStatus} />
        <Field label="Approval" value={timeline.approvalStatus} />
        <Field label="Execution" value={timeline.executionStatus} />
        <Field label="Observation window" value={`${timeline.observationWindow.startsAt} to ${timeline.observationWindow.endsAt}`} />
        <Field label="Outcome" value={timeline.outcomeStatus} />
      </FieldGrid>
      <TimelineList steps={interventionSteps} nodes={timeline.nodes} />
      <AuditMetaPanel audit={timeline.audit} />
    </DomainCard>
  );
}

export function PricingPlanComparison({ comparison, onRequestApproval, "data-testid": testId = "pricing-plan-comparison" }: PricingPlanComparisonProps) {
  return (
    <DomainCard title={comparison.plan.label} eyebrow={`Pricing plan ${comparison.plan.entityId}`} badge={<StatusBadge label={comparison.constraintStatus} tone={comparison.constraintStatus === "PASS" ? "green" : comparison.constraintStatus === "WARNING" ? "yellow" : "red"} />} testId={testId}>
      <FieldGrid>
        <Field label="Current price" value={formatMoney(comparison.currentPrice)} />
        <Field label="Candidate price" value={formatMoney(comparison.candidatePrice)} />
        <Field label="Price change" value={formatMoney(comparison.priceChange)} />
        <Field label="Risk" value={<StatusBadge label={comparison.risk} tone={riskTone[comparison.risk]} />} />
        <Field label="Approval status" value={comparison.approvalStatus} />
        <Field label="Rollback plan" value={comparison.rollbackPlan} />
      </FieldGrid>
      <Section title="Expected intervals">
        <IntervalBand label="Demand" interval={comparison.expectedDemand} />
        <IntervalBand label="Revenue" interval={comparison.expectedRevenue} />
        <IntervalBand label="Gross margin" interval={comparison.expectedGrossMargin} />
      </Section>
      <Section title="Hard constraint violations">
        <SimpleList items={comparison.hardConstraintViolations.map((violation) => <strong>{violation}</strong>)} emptyLabel="No hard constraint violations" />
      </Section>
      <DataQualityPanel dataQuality={comparison.dataQuality} />
      {onRequestApproval ? (
        <button className="odp-domain-button" type="button" onClick={onRequestApproval}>
          Request human approval
        </button>
      ) : null}
    </DomainCard>
  );
}

export function AdLiftReportCard({ report, "data-testid": testId = "ad-lift-report-card" }: AdLiftReportCardProps) {
  const hasControls = report.controlStores.length > 0;
  return (
    <DomainCard title={report.campaign.label} eyebrow={`Campaign ${report.campaign.entityId}`} badge={<StatusBadge label={report.continueStopRecommendation} tone={report.continueStopRecommendation === "CONTINUE" ? "green" : report.continueStopRecommendation === "STOP" ? "red" : "yellow"} />} testId={testId}>
      {!hasControls ? <p className="odp-domain-alert">No control stores are present, so this report must not be treated as causal lift evidence.</p> : null}
      {report.preTrendStatus !== "PASS" ? <p className="odp-domain-alert">Pre-trend status is {report.preTrendStatus}; review validity before action.</p> : null}
      <FieldGrid>
        <Field label="Treatment stores" value={report.treatmentStores.length} />
        <Field label="Control stores" value={report.controlStores.length} />
        <Field label="Pre-trend" value={report.preTrendStatus} />
        <Field label="Evidence level" value={report.evidenceLevel} />
      </FieldGrid>
      <Section title="Lift intervals">
        <IntervalBand label="Incremental revenue" interval={report.incrementalRevenue} />
        <IntervalBand label="Incremental gross margin" interval={report.incrementalGrossMargin} />
        <IntervalBand label="IROMI" interval={report.iromi} />
      </Section>
      <Section title="Contamination warnings">
        <SimpleList items={report.contaminationWarnings.map((warning) => <strong>{warning}</strong>)} emptyLabel="No contamination warnings" />
      </Section>
      <DataQualityPanel dataQuality={report.dataQuality} />
    </DomainCard>
  );
}

export function ValuationRangeChart({ valuation, "data-testid": testId = "valuation-range-chart" }: ValuationRangeChartProps) {
  return (
    <DomainCard title={valuation.valuation.label} eyebrow={`Valuation ${valuation.valuation.entityId}`} badge={<StatusBadge label={valuation.financeApprovalStatus} tone="blue" />} testId={testId}>
      <Section title="Fair value range">
        <IntervalBand label="Fair value" interval={valuation.fairValue} />
        <FieldGrid>
          <Field label="Reserve price" value={displayProtectedValue(valuation.reservePrice !== undefined ? formatMoney(valuation.reservePrice) : "n/a", "reservePrice", valuation.fieldPermissions)} />
          <Field label="Asking price" value={displayProtectedValue(valuation.askingPrice !== undefined ? formatMoney(valuation.askingPrice) : "n/a", "askingPrice", valuation.fieldPermissions)} />
          <Field label="Liquidity score" value={formatPercent(valuation.liquidityScore)} />
        </FieldGrid>
      </Section>
      <Section title="Lens ranges">
        {valuationLenses.map((lens) => (valuation.lensRanges[lens] ? <IntervalBand key={lens} label={lens} interval={valuation.lensRanges[lens]} /> : null))}
      </Section>
      <Section title="Comparable transaction markers">
        <SimpleList items={valuation.comparableTransactionMarkers.map((marker) => formatMoney(marker))} emptyLabel="No comparable transaction markers" />
      </Section>
      <Section title="Data room completeness">
        <FieldGrid>
          {Object.entries(valuation.dataRoomCompleteness).map(([label, status]) => (
            <Field key={label} label={label} value={status} />
          ))}
        </FieldGrid>
      </Section>
      <DataQualityPanel dataQuality={valuation.dataQuality} />
    </DomainCard>
  );
}

export function NetPlanScenarioCard({ scenario, "data-testid": testId = "net-plan-scenario-card" }: NetPlanScenarioCardProps) {
  const budgetPercent = scenario.budgetUsage.limit > 0 ? scenario.budgetUsage.used / scenario.budgetUsage.limit : 0;
  return (
    <DomainCard title={scenario.scenarioName} eyebrow="Network plan scenario" badge={<StatusBadge label={scenario.solverStatus} tone={scenario.solverStatus === "SUCCEEDED" ? "green" : scenario.solverStatus === "FAILED" ? "red" : "yellow"} />} testId={testId}>
      <FieldGrid>
        <Field label="Objective value" value={formatNumber(scenario.objectiveValue)} />
        <Field label="Budget usage" value={`${formatNumber(scenario.budgetUsage.used)} / ${formatNumber(scenario.budgetUsage.limit)} ${scenario.budgetUsage.unit} (${formatPercent(budgetPercent)})`} />
        <Field label="Risk" value={<StatusBadge label={scenario.risk} tone={riskTone[scenario.risk]} />} />
        <Field label="Alternative plan" value={scenario.alternativePlanAvailable ? "Available" : "Unavailable"} />
        <Field label="Approval status" value={scenario.approvalStatus} />
      </FieldGrid>
      <Section title="Action counts">
        <FieldGrid>
          {netPlanActions.map((action) => (
            <Field key={action} label={action} value={scenario.actionCounts[action] ?? 0} />
          ))}
        </FieldGrid>
      </Section>
      <Section title="Expected gross margin">
        <IntervalBand label="Gross margin" interval={scenario.expectedGrossMargin} />
      </Section>
      <Section title="Binding constraints">
        <SimpleList items={scenario.bindingConstraints} emptyLabel="No binding constraints" />
      </Section>
      {scenario.infeasibilityDiagnosis?.length ? (
        <Section title="Infeasibility diagnosis">
          <SimpleList
            items={scenario.infeasibilityDiagnosis.map((diagnosis) => (
              <span>
                <strong>{diagnosis.violatedConstraint}</strong>: relax {diagnosis.requiredRelaxation}; impact {diagnosis.businessImpact}; suggested action {diagnosis.suggestedAction}
              </span>
            ))}
          />
        </Section>
      ) : null}
      <DataQualityPanel dataQuality={scenario.dataQuality} />
    </DomainCard>
  );
}

export function ModelReleaseCard({ release, onOpenAudit, "data-testid": testId = "model-release-card" }: ModelReleaseCardProps) {
  return (
    <DomainCard title={`${release.modelId} ${release.version}`} eyebrow={release.championOrChallenger} badge={<StatusBadge label={release.releaseStage} tone={release.releaseStage === "PRODUCTION" || release.releaseStage === "CHAMPION" ? "purple" : release.releaseStage === "ROLLED_BACK" || release.releaseStage === "BLOCKED" ? "red" : "blue"} />} testId={testId}>
      <FieldGrid>
        <Field label="Approval status" value={release.approvalStatus} />
        <Field label="Data quality" value={<StatusBadge label={release.dataQualityStatus} tone={dataStatusTone[release.dataQualityStatus]} />} />
        <Field label="Drift" value={<StatusBadge label={release.driftStatus} tone={dataStatusTone[release.driftStatus]} />} />
        <Field label="Rollback target" value={release.rollbackTarget ? `${release.rollbackTarget.modelId} ${release.rollbackTarget.version}` : "n/a"} />
      </FieldGrid>
      <Section title="Metric summary">
        <FieldGrid>
          {Object.entries(release.metricSummary).map(([metric, value]) => (
            <Field key={metric} label={metric} value={value} />
          ))}
        </FieldGrid>
      </Section>
      <Section title="Segment regression">
        <SimpleList items={release.segmentRegression.map((item) => <strong>{item}</strong>)} emptyLabel="No segment regressions" />
      </Section>
      <AuditMetaPanel audit={release.audit} />
      {onOpenAudit ? (
        <button className="odp-domain-button" type="button" onClick={onOpenAudit}>
          Open audit
        </button>
      ) : null}
    </DomainCard>
  );
}

export function DecisionAuditTimeline({ timeline, "data-testid": testId = "decision-audit-timeline" }: DecisionAuditTimelineProps) {
  return (
    <DomainCard title={timeline.decisionId} eyebrow={entityLabel(timeline.entity)} badge={<StatusBadge label={timeline.auditStatus} tone={timeline.auditStatus === "READY" ? "green" : timeline.auditStatus === "MISSING" ? "red" : "yellow"} />} testId={testId}>
      <FieldGrid>
        <Field label="Actor" value={timeline.actor} />
        <Field label="Decision time" value={timeline.decisionTime} />
        <Field label="Model version" value={timeline.modelVersion ?? "n/a"} />
        <Field label="Feature snapshot" value={timeline.featureSnapshotTime ?? "n/a"} />
        <Field label="Execution status" value={timeline.executionStatus ?? "n/a"} />
        <Field label="Outcome status" value={timeline.outcomeStatus ?? "n/a"} />
      </FieldGrid>
      <TimelineList steps={decisionAuditSteps} nodes={timeline.nodes} />
    </DomainCard>
  );
}
