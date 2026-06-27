import { ModulePlaceholder } from "@oday-plus/ui";

export default function AdLiftPage() {
  return (
    <ModulePlaceholder
      routeKey="adlift"
      scope={[
        "AdLiftReportCard：treatment / control 與 pre-trend 狀態",
        "incremental revenue / gross margin、iROMI 與證據等級",
        "無對照組不得宣稱因果；重疊干預顯示 contamination",
      ]}
    />
  );
}
