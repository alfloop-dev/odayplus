import { ModulePlaceholder } from "@oday-plus/ui";

export default function ExpansionPage() {
  return (
    <ModulePlaceholder
      routeKey="expansion"
      scope={[
        "HeatZoneScoreCard 熱區評分與優先序",
        "CandidateSiteCard 候選點審查（敏感欄位依權限遮罩）",
        "SiteScoreReportSummary（P10/P50/P90 + EvidencePanel + ApprovalPanel）",
      ]}
    />
  );
}
