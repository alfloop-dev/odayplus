import { LearningHubWorkspace } from "../../../features/learninghub/LearningHubWorkspace.tsx";
import { getServerApiClient } from "../../lib/api/client.ts";
import { loadApiBinding } from "../../lib/api/binding.ts";

// Releases advance on every governed write (shadow / canary / full / rollback),
// so this route is dynamic and reads the live release log on each request.
export const dynamic = "force-dynamic";

export default async function LearningPage() {
  const liveReleases = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listLearningReleases().then((response) => response.items),
  });
  return <LearningHubWorkspace liveReleases={liveReleases} />;
}
