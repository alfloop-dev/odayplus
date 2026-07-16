import { InterventionWorkspace } from "../../../features/intervention/InterventionWorkspace.tsx";
import { getServerApiClient } from "../../lib/api/client.ts";
import { loadApiBinding } from "../../lib/api/binding.ts";

// Intervention cases advance through the lifecycle on every backend write, so
// this route is dynamic and reads the live list on each request.
export const dynamic = "force-dynamic";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function InterventionsPage({ searchParams }: PageProps) {
  const liveInterventions = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listInterventions().then((response) => response.items),
  });
  return <InterventionWorkspace searchParams={await searchParams} liveInterventions={liveInterventions} />;
}
