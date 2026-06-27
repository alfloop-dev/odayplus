import { ModulePlaceholder } from "@oday-plus/ui";

export default function SearchPage() {
  return (
    <ModulePlaceholder
      routeKey="search"
      scope={[
        "跨實體搜尋：門市、候選點、決策、模型版本、稽核紀錄",
        "最近瀏覽與權限內快速動作（Command Palette 同源）",
      ]}
    />
  );
}
