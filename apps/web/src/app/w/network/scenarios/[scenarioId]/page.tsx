import { NetPlanWorkspace } from "../../../../../../features/netplan/NetPlanWorkspace.tsx";
import { getServerApiClient } from "../../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../../lib/api/binding.ts";

type PageProps = {
  params: Promise<{ scenarioId: string }>;
};

export default async function NetworkScenarioDetailPage({ params }: PageProps) {
  const { scenarioId } = await params;
  const liveScenarios = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listNetplanScenarios().then((response) => response.items),
  });
  return <NetPlanWorkspace view="scenarioDetail" scenarioId={scenarioId} liveScenarios={liveScenarios} />;
}
