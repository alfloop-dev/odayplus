import { ModulePlaceholder } from "@oday-plus/ui";

export default function TasksPage() {
  return (
    <ModulePlaceholder
      routeKey="tasks"
      scope={[
        "待核准決策、待補件、待觀察窗成熟的決策任務",
        "指派、SLA 與批次操作（compact 密度收件匣）",
      ]}
    />
  );
}
