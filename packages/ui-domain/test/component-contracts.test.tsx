import type { ComponentType } from "react";
import type { FrontendDomainComponentKey } from "@oday-plus/domain-types";
import {
  AdLiftReportCard,
  CandidateSiteCard,
  DecisionAuditTimeline,
  ForecastBandChart,
  FourLightBadge,
  HeatZoneScoreCard,
  InterventionTimeline,
  ModelReleaseCard,
  NetPlanScenarioCard,
  PricingPlanComparison,
  RootCauseEvidenceCard,
  SiteScoreReportSummary,
  ValuationRangeChart,
} from "../src/index.ts";
import {
  adLiftReportFixture,
  candidateSiteFixture,
  decisionAuditTimelineFixture,
  forecastBandFixture,
  fourLightBadgeFixture,
  heatZoneScoreFixture,
  interventionTimelineFixture,
  modelReleaseFixture,
  netPlanScenarioFixture,
  pricingPlanComparisonFixture,
  rootCauseEvidenceFixture,
  siteScoreReportFixture,
  valuationRangeFixture,
} from "../src/fixtures.ts";

const exportedComponents = {
  HeatZoneScoreCard,
  CandidateSiteCard,
  SiteScoreReportSummary,
  ForecastBandChart,
  FourLightBadge,
  RootCauseEvidenceCard,
  InterventionTimeline,
  PricingPlanComparison,
  AdLiftReportCard,
  ValuationRangeChart,
  NetPlanScenarioCard,
  ModelReleaseCard,
  DecisionAuditTimeline,
} satisfies Record<FrontendDomainComponentKey, ComponentType<any>>;

export const componentContractExamples = [
  <HeatZoneScoreCard score={heatZoneScoreFixture} />,
  <CandidateSiteCard candidate={candidateSiteFixture} />,
  <SiteScoreReportSummary report={siteScoreReportFixture} />,
  <ForecastBandChart forecast={forecastBandFixture} />,
  <FourLightBadge badge={fourLightBadgeFixture} />,
  <RootCauseEvidenceCard evidence={rootCauseEvidenceFixture} />,
  <InterventionTimeline timeline={interventionTimelineFixture} />,
  <PricingPlanComparison comparison={pricingPlanComparisonFixture} />,
  <AdLiftReportCard report={adLiftReportFixture} />,
  <ValuationRangeChart valuation={valuationRangeFixture} />,
  <NetPlanScenarioCard scenario={netPlanScenarioFixture} />,
  <ModelReleaseCard release={modelReleaseFixture} />,
  <DecisionAuditTimeline timeline={decisionAuditTimelineFixture} />,
];

export const allFrontendDomainComponentNames = Object.keys(exportedComponents);
