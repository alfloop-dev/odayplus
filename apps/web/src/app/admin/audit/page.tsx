import { AuditWorkspace } from "../../../../features/audit/AuditWorkspace.tsx";
import { getServerApiClient } from "../../../lib/api/client.ts";
import { loadApiBinding } from "../../../lib/api/binding.ts";
import { isProductionMode } from "../../../../features/shell/mode.ts";
import { headers } from "next/headers";

// Audit events accumulate from every backend write, so this route is dynamic.
export const dynamic = "force-dynamic";

export default async function AdminAuditPage() {
  const reqHeaders = await headers();
  const isProduction =
    isProductionMode() ||
    reqHeaders.get("x-production-mode") === "true";

  const liveEvents = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listAuditEvents().then((response) => response.events),
  });
  return <AuditWorkspace view="admin" liveEvents={liveEvents} isProduction={isProduction} />;
}
