import { ExpansionWorkspace } from "../../../../../features/expansion/ExpansionWorkspace.tsx";
import { getServerApiClient } from "../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../lib/api/binding.ts";

export const dynamic = "force-dynamic";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function ListingsPage({ searchParams }: PageProps) {
  const resolvedSearchParams = await searchParams;
  const selectedHeatZone = Array.isArray(resolvedSearchParams?.heatZone)
    ? resolvedSearchParams?.heatZone[0]
    : resolvedSearchParams?.heatZone;
  const liveNetwork = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) =>
      client
        .getNetworkListings({ selectedHeatZoneId: selectedHeatZone })
        .then((response) => (response.listings.length > 0 ? [response] : [])),
  });
  return <ExpansionWorkspace view="listings" searchParams={resolvedSearchParams} liveNetwork={liveNetwork} />;
}
