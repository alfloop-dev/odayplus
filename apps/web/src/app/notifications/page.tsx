import {
  NotificationsWorkspace,
  type NotificationsSearchParams,
} from "../../../features/shell/NotificationsWorkspace.tsx";
import { loadApiResource } from "../../../features/shell/resource.ts";
import { getServerApiClient } from "../../lib/api/client.ts";

// Inbox acknowledgement state is per-role and changes on every write.
export const dynamic = "force-dynamic";

export default async function NotificationsPage({
  searchParams,
}: {
  searchParams: Promise<NotificationsSearchParams>;
}) {
  const params = await searchParams;
  const inbox = await loadApiResource({
    client: await getServerApiClient(),
    fetcher: (client) =>
      client.getShellNotifications({
        severity: params.severity,
        acknowledged:
          params.acknowledged === undefined ? undefined : params.acknowledged === "true",
      }),
  });
  return <NotificationsWorkspace inbox={inbox} searchParams={params} />;
}
