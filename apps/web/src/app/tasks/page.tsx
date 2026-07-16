import {
  TaskCenterWorkspace,
  type TaskCenterSearchParams,
} from "../../../features/shell/TaskCenterWorkspace.tsx";
import { loadApiResource } from "../../../features/shell/resource.ts";
import { getServerApiClient } from "../../lib/api/client.ts";

// Task assignment and SLA state change on every write; a cached Task Center
// would show an operator a task someone else already took.
export const dynamic = "force-dynamic";

export default async function TasksPage({
  searchParams,
}: {
  searchParams: Promise<TaskCenterSearchParams>;
}) {
  const params = await searchParams;
  const tasks = await loadApiResource({
    client: await getServerApiClient(),
    fetcher: (client) => client.getShellTasks(params),
  });
  return <TaskCenterWorkspace tasks={tasks} searchParams={params} />;
}
