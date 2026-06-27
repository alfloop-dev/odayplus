import { ModulePlaceholder } from "@oday-plus/ui";

export default function InterventionsPage() {
  return (
    <ModulePlaceholder
      routeKey="interventions"
      scope={[
        "InterventionTimeline（Triggered → … → Closed 固定序）",
        "Eligibility / Conflict 檢查、人工核准與觀察窗追蹤",
        "高風險動作不 optimistic，提交觸發後端 Audit",
      ]}
    />
  );
}
