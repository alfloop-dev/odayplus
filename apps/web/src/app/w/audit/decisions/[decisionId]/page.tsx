import { AuditWorkspace } from "../../../../../../features/audit/AuditWorkspace.tsx";
import { getServerApiClient } from "../../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../../lib/api/binding.ts";

type PageProps = {
  params: Promise<{ decisionId: string }>;
};

export default async function AuditDecisionDetailPage({ params }: PageProps) {
  const { decisionId } = await params;
  const liveEvents = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listAuditEvents().then((response) => response.events),
  });
  return (
    <AuditWorkspace
      view="decisionDetail"
      decisionId={decodeURIComponent(decisionId)}
      liveEvents={liveEvents}
    />
  );
}
