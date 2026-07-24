import { AuditWorkspace } from "../../../../../features/audit/AuditWorkspace.tsx";
import { getServerApiClient } from "../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../lib/api/binding.ts";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function AuditDecisionsPage({ searchParams }: PageProps) {
  const liveEvents = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listAuditEvents().then((response) => response.events),
  });
  return <AuditWorkspace view="decisions" searchParams={await searchParams} liveEvents={liveEvents} />;
}
