import { OperationsWorkspace } from "../../../features/operations/OperationsWorkspace.tsx";
import { getServerApiClient } from "../../lib/api/client.ts";
import { loadApiBinding } from "../../lib/api/binding.ts";

// Four-light alerts accumulate from every forecast run and acknowledgement, so
// this route reads live backend state on each request.
export const dynamic = "force-dynamic";

export default async function OperationsPage() {
  const liveAlerts = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listForecastAlerts().then((response) => response.items),
  });
  return <OperationsWorkspace liveAlerts={liveAlerts} />;
}
