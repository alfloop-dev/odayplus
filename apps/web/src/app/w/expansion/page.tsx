import { ExpansionWorkspace } from "../../../../features/expansion/ExpansionWorkspace.tsx";
import { getServerApiClient } from "../../../lib/api/client.ts";
import { loadApiBinding } from "../../../lib/api/binding.ts";
import { loadServerIntakeOperatorSession } from "../../../lib/api/intakeOperatorSession.ts";

// Freshness/lineage must reflect the backend's persisted ingestion run state,
// so this route is dynamic and reads it live (fixture fallback when no API).
export const dynamic = "force-dynamic";

export default async function ExpansionWorkspacePage() {
  const client = await getServerApiClient();
  const liveFreshness = await loadApiBinding({
    client,
    fetcher: (client) => client.listExternalDataFreshness().then((response) => response.freshness),
  });
  const operatorSession = await loadServerIntakeOperatorSession(client);
  return (
    <ExpansionWorkspace
      liveFreshness={liveFreshness}
      operatorSession={operatorSession}
    />
  );
}
