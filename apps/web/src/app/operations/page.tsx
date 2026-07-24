import { OperationsWorkspace } from "../../../features/operations/OperationsWorkspace.tsx";
import { getServerApiClient } from "../../lib/api/client.ts";
import { loadApiBinding } from "../../lib/api/binding.ts";

// Four-light alerts accumulate from every forecast run and acknowledgement, so
// this route reads live backend state on each request.
export const dynamic = "force-dynamic";

export default async function OperationsPage() {
  const client = await getServerApiClient();
  const [liveAlerts, liveForecasts] = await Promise.all([
    loadApiBinding({
      client,
      fetcher: (api) => api.listForecastAlerts().then((response) => response.items),
    }),
    loadApiBinding({
      client,
      fetcher: (api) => api.listForecasts().then((response) => response.items),
    }),
  ]);
  return <OperationsWorkspace liveAlerts={liveAlerts} liveForecasts={liveForecasts} />;
}
