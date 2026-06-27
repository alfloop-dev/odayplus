import { ModulePlaceholder } from "@oday-plus/ui";

export default function AuditPage() {
  return (
    <ModulePlaceholder
      routeKey="audit"
      scope={[
        "DecisionAuditTimeline（Prediction → … → Feedback 固定節點）",
        "feature snapshot time / model version / policy version / actor / reason",
        "可匯出證據包；匯出本身記 Audit",
      ]}
    />
  );
}
