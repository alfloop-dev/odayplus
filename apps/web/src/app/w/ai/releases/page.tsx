import { LearningHubWorkspace } from "../../../../../features/learninghub/LearningHubWorkspace.tsx";
import { getServerApiClient } from "../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../lib/api/binding.ts";

// The release log is a live governance surface; read the backend on each request.
export const dynamic = "force-dynamic";

export default async function AiReleasesPage() {
  const liveReleases = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listLearningReleases().then((response) => response.items),
  });
  return <LearningHubWorkspace view="releases" liveReleases={liveReleases} />;
}
