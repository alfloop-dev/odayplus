import { OperationsWorkspace } from "../../../../../features/operations/OperationsWorkspace.tsx";
import { getServerApiClient } from "../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../lib/api/binding.ts";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

// The alert center reads and acknowledges live four-light alerts.
export const dynamic = "force-dynamic";

export default async function AlertsPage({ searchParams }: PageProps) {
  const liveAlerts = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listForecastAlerts().then((response) => response.items),
  });
  return <OperationsWorkspace view="alerts" searchParams={await searchParams} liveAlerts={liveAlerts} />;
}
