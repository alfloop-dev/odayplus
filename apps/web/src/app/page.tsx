import { HomeWorkspace } from "../../features/shell/HomeWorkspace.tsx";
import { loadApiResource } from "../../features/shell/resource.ts";
import { getServerApiClient } from "../lib/api/client.ts";

// The first screen aggregates live queue, approval and notification state that
// changes on every backend write, so it must never be statically cached.
export const dynamic = "force-dynamic";

export default async function HomePage() {
  const home = await loadApiResource({
    client: await getServerApiClient(),
    fetcher: (client) => client.getShellHome(),
  });
  return <HomeWorkspace home={home} />;
}
