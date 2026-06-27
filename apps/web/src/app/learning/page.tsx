import { ModulePlaceholder } from "@oday-plus/ui";

export default function LearningPage() {
  return (
    <ModulePlaceholder
      routeKey="learning"
      scope={[
        "ModelReleaseCard：版本、champion/challenger、release stage",
        "metric summary、segment regression、drift 狀態",
        "release / rollback 須觸發後端 Audit",
      ]}
    />
  );
}
