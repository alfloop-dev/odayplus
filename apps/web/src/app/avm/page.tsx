import { AvmWorkspace } from "../../../features/avm/AvmWorkspace.tsx";
import { getServerApiClient } from "../../lib/api/client.ts";
import { loadApiBinding } from "../../lib/api/binding.ts";

export const dynamic = "force-dynamic";

export default async function AvmPage() {
  const liveCases = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listAvmCases().then((response) => response.items),
  });
  return <AvmWorkspace liveCases={liveCases} />;
}
