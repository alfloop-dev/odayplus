import { AdminWorkspace } from "../../../features/shell/AdminWorkspace.tsx";
import { loadApiResource } from "../../../features/shell/resource.ts";
import { getServerApiClient } from "../../lib/api/client.ts";

// Grants and their audit trail change on every governance write, and the 403
// for a non-admin caller must be re-evaluated per request rather than cached.
export const dynamic = "force-dynamic";

export default async function AdminPage() {
  const admin = await loadApiResource({
    client: await getServerApiClient(),
    fetcher: (client) => client.getShellAdmin(),
  });
  return <AdminWorkspace admin={admin} />;
}
