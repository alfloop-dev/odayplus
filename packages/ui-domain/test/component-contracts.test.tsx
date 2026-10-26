import type { ComponentType } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
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

describe("SiteScoreReportSummary rent reasonableness", () => {
  it.each([
    { rentReasonableness: 0, expected: "0" },
    { rentReasonableness: 0.5722, expected: "0.5722" },
    { rentReasonableness: 1, expected: "1" },
    { rentReasonableness: null, expected: "未評估" },
  ])("renders $rentReasonableness as $expected without a risk badge", ({ rentReasonableness, expected }) => {
    const container = document.createElement("div");
    container.innerHTML = renderToStaticMarkup(
      <SiteScoreReportSummary report={{ ...siteScoreReportFixture, rentReasonableness }} />,
    );
    const rentLabel = Array.from(container.querySelectorAll("dt")).find(
      (label) => label.textContent === "Rent reasonableness",
    );
    const rentValue = rentLabel?.nextElementSibling;

    expect(rentLabel).toBeDefined();
    expect(rentValue?.tagName).toBe("DD");
    expect(rentValue?.textContent).toBe(expected);
    expect(rentValue?.querySelector(".odp-domain-badge")).toBeNull();

    // The unrelated, genuinely categorical risk field must keep its badge.
    const riskLabel = Array.from(container.querySelectorAll("dt")).find(
      (label) => label.textContent === "Cannibalization risk",
    );
    expect(riskLabel?.nextElementSibling?.querySelector(".odp-domain-badge")?.textContent)
      .toBe(siteScoreReportFixture.cannibalizationRisk);
  });
});
