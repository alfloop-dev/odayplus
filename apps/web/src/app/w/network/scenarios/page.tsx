import { NetPlanWorkspace } from "../../../../../features/netplan/NetPlanWorkspace.tsx";
import { getServerApiClient } from "../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../lib/api/binding.ts";

export const dynamic = "force-dynamic";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function NetworkScenariosPage({ searchParams }: PageProps) {
  const liveScenarios = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listNetplanScenarios().then((response) => response.items),
  });
  return <NetPlanWorkspace view="scenarios" searchParams={await searchParams} liveScenarios={liveScenarios} />;
}
