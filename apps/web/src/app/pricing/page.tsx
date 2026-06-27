import { ModulePlaceholder } from "@oday-plus/ui";

export default function PricingPage() {
  return (
    <ModulePlaceholder
      routeKey="pricing"
      scope={[
        "PricingPlanComparison：現行價與候選價並陳",
        "硬限制（hard constraint）違反明顯標示",
        "僅支援人工核准與 rollback 計畫，不自動執行",
      ]}
    />
  );
}
