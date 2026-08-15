import { FranchiseeWorkspace } from "../../../features/shell/FranchiseeWorkspace.tsx";
import { loadApiResource } from "../../../features/shell/resource.ts";
import { getServerApiClient } from "../../lib/api/client.ts";

// Scoped to the calling franchisee's own store and reports — never cached
// across callers.
export const dynamic = "force-dynamic";

export default async function FranchiseePage({
  searchParams,
}: {
  searchParams: Promise<{ storeId?: string }>;
}) {
  const { storeId } = await searchParams;
  const view = await loadApiResource({
    client: await getServerApiClient(),
    fetcher: (client) => client.getShellFranchisee(storeId),
  });
  return <FranchiseeWorkspace view={view} />;
}
