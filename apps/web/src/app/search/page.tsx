import { SearchWorkspace } from "../../../features/shell/SearchWorkspace.tsx";
import { loadApiResource } from "../../../features/shell/resource.ts";
import { getServerApiClient } from "../../lib/api/client.ts";

// Results are authorization-scoped per caller, so a shared cache entry could
// serve one role's results to another.
export const dynamic = "force-dynamic";

export default async function SearchPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const { q = "" } = await searchParams;
  const results = await loadApiResource({
    client: await getServerApiClient(),
    fetcher: (client) => client.searchShell(q),
  });
  return <SearchWorkspace results={results} query={q} />;
}
