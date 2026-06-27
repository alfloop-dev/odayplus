import { ModulePlaceholder } from "@oday-plus/ui";

export default function NetPlanPage() {
  return (
    <ModulePlaceholder
      routeKey="netplan"
      scope={[
        "NetPlanScenarioCard：OPEN/KEEP/IMPROVE/MOVE/EXIT 計數",
        "solver 狀態與 binding constraints",
        "無可行解時呈現 Infeasibility Diagnosis；不自動放寬限制",
      ]}
    />
  );
}
