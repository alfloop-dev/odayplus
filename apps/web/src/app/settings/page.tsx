import { SettingsWorkspace } from "../../../features/shell/SettingsWorkspace.tsx";
import { loadApiResource } from "../../../features/shell/resource.ts";
import { getServerApiClient } from "../../lib/api/client.ts";

// Settings are per-role server state; caching would show one role another's.
export const dynamic = "force-dynamic";

export default async function SettingsPage() {
  const settings = await loadApiResource({
    client: await getServerApiClient(),
    fetcher: (client) => client.getShellSettings(),
  });
  return <SettingsWorkspace settings={settings} />;
}
