import { ExpansionWorkspace } from "../../../../features/expansion/ExpansionWorkspace.tsx";
import { loadApiBinding } from "../../../lib/api/binding.ts";
import { getServerApiClient } from "../../../lib/api/client.ts";

export const dynamic = "force-dynamic";

type PageProps = {
  params: Promise<{ intakeId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function IntakeRoutePage({ params, searchParams }: PageProps) {
  const { intakeId } = await params;
  const resolvedSearchParams = await searchParams;
  const heatZoneParam = resolvedSearchParams.heatZone;
  const selectedHeatZoneId = Array.isArray(heatZoneParam)
    ? heatZoneParam[0]
    : heatZoneParam;
  const liveNetwork = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) =>
      client
        .getNetworkListings({ selectedHeatZoneId })
        .then((response) => (response.listings.length > 0 ? [response] : [])),
  });

  return (
    <ExpansionWorkspace
      liveNetwork={liveNetwork}
      searchParams={{
        ...resolvedSearchParams,
        selected: intakeId,
        dialog: "detail",
      }}
      view="listings"
    />
  );
}
