import { AuditWorkspace } from "../../../../../features/audit/AuditWorkspace.tsx";
import { getServerApiClient } from "../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../lib/api/binding.ts";

export default async function AuditEvidencePage() {
  const liveEvents = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listAuditEvents().then((response) => response.events),
  });
  return <AuditWorkspace view="evidence" liveEvents={liveEvents} />;
}
