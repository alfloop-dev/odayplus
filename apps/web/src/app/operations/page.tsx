import { ModulePlaceholder } from "@oday-plus/ui";

export default function OperationsPage() {
  return (
    <ModulePlaceholder
      routeKey="operations"
      scope={[
        "FourLightBadge 門市四燈（顏色 + 文字 + icon/pattern）",
        "ForecastBandChart 預測帶（actual / P50 / P10–P90 band）",
        "RootCauseEvidenceCard 根因證據與建議下一步檢查",
      ]}
    />
  );
}
