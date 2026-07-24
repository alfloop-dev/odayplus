import { ExpansionWorkspace } from "../../../../../features/expansion/ExpansionWorkspace.tsx";
import { getServerApiClient } from "../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../lib/api/binding.ts";

export const dynamic = "force-dynamic";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function HeatZonePage({ searchParams }: PageProps) {
  const liveHeatZones = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listHeatzones().then((response) => response.items),
  });
  return <ExpansionWorkspace view="heatzone" searchParams={await searchParams} liveHeatZones={liveHeatZones} />;
}
