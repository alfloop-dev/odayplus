import { ModulePlaceholder } from "@oday-plus/ui";

export default function AvmPage() {
  return (
    <ModulePlaceholder
      routeKey="avm"
      scope={[
        "ValuationRangeChart 公允價值 P10/P50/P90（含 lens 比較）",
        "底價 / 開價依權限遮罩，匯出受限",
        "liquidityScore、資料室完整度與融資核准狀態",
      ]}
    />
  );
}
