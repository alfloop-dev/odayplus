import { AuditWorkspace } from "../../../../features/audit/AuditWorkspace.tsx";
import { getServerApiClient } from "../../../lib/api/client.ts";
import { loadApiBinding } from "../../../lib/api/binding.ts";

// Audit events accumulate from every backend write, so this route is dynamic.
export const dynamic = "force-dynamic";

export default async function AdminAuditPage() {
  const liveEvents = await loadApiBinding({
    client: getServerApiClient(),
    fetcher: (client) => client.listAuditEvents().then((response) => response.events),
  });
  return <AuditWorkspace view="admin" liveEvents={liveEvents} />;
}
