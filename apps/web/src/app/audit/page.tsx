import { AuditWorkspace } from "../../../features/audit/AuditWorkspace.tsx";
import { getServerApiClient } from "../../lib/api/client.ts";
import { loadApiBinding } from "../../lib/api/binding.ts";

export const dynamic = "force-dynamic";

export default async function AuditPage() {
  const liveEvents = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listAuditEvents().then((response) => response.events),
  });
  return <AuditWorkspace liveEvents={liveEvents} />;
}
