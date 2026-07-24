import { LearningHubWorkspace } from "../../../features/learninghub/LearningHubWorkspace.tsx";
import { getServerApiClient } from "../../lib/api/client.ts";
import { loadApiBinding } from "../../lib/api/binding.ts";

// Releases advance on every governed write (shadow / canary / full / rollback),
// so this route is dynamic and reads the live release log on each request.
export const dynamic = "force-dynamic";

export default async function LearningPage() {
  const client = await getServerApiClient();
  const [liveReleases, liveModels] = await Promise.all([
    loadApiBinding({
      client,
      fetcher: (api) => api.listLearningReleases().then((response) => response.items),
    }),
    loadApiBinding({
      client,
      fetcher: (api) => api.listLearningModels().then((response) => response.items),
    }),
  ]);
  return <LearningHubWorkspace liveReleases={liveReleases} liveModels={liveModels} />;
}
