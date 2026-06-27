import { ModulePlaceholder } from "@oday-plus/ui";

export default function AdminPage() {
  return (
    <ModulePlaceholder
      routeKey="admin"
      scope={[
        "工作區、角色與權限管理",
        "環境（dev/staging/production）與平台設定",
        "權限變更為高風險動作，須觸發 Audit",
      ]}
    />
  );
}
