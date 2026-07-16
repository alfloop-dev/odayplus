import { NetPlanWorkspace } from "../../../features/netplan/NetPlanWorkspace.tsx";
import { getServerApiClient } from "../../lib/api/client.ts";
import { loadApiBinding } from "../../lib/api/binding.ts";

// Scenarios advance through the lifecycle on every backend write (solve,
// approval, execution, outcome), so this route is dynamic and reads the live
// list on each request.
export const dynamic = "force-dynamic";

export default async function NetPlanPage() {
  const liveScenarios = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listNetplanScenarios().then((response) => response.items),
  });
  return <NetPlanWorkspace liveScenarios={liveScenarios} />;
}
