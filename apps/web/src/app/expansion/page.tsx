import { ExpansionWorkspace } from "../../../features/expansion/ExpansionWorkspace.tsx";
import { getServerApiClient } from "../../lib/api/client.ts";
import { loadApiBinding } from "../../lib/api/binding.ts";

export const dynamic = "force-dynamic";

export default async function ExpansionPage() {
  const liveFreshness = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listExternalDataFreshness().then((response) => response.freshness),
  });
  return <ExpansionWorkspace liveFreshness={liveFreshness} />;
}
